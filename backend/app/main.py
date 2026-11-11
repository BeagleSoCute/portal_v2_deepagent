"""Control plane — FastAPI.

หน้าที่: เก็บ spec, ป้อน registry ให้ฟอร์ม generate ตัวเอง, และรัน agent แบบ stream
ตัว agent เกิดจาก factory ตอน request ไม่มีการ deploy
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

load_dotenv()

from . import factory  # noqa: E402
from .audit import SINK as AUDIT, RunAudit  # noqa: E402
from .auth import routes as auth_routes  # noqa: E402
from .auth.config import Settings  # noqa: E402
from .auth.current_user import NoAccess, SessionLocks, User, require_user  # noqa: E402
from .auth.db import Base, make_engine  # noqa: E402
from .auth.oidc import OidcClient  # noqa: E402
from .auth.sessions import delete_expired_sessions  # noqa: E402
from .connections import KIND_LABELS, ConnectionInput, ConnectionStore, test_connection  # noqa: E402
from .registry import middleware as mw_registry  # noqa: E402
from .registry import models as model_registry  # noqa: E402
from .registry import python_tools  # noqa: E402
from .registry import tools as tool_registry  # noqa: E402
from .spec import AgentSpec  # noqa: E402
from .store import SpecStore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")

HERE = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

settings = Settings()
if problems := settings.problems():
    # Refuse to start half-configured: without these, login could only fail at the first request.
    raise RuntimeError("login is not configured: " + "; ".join(problems)
                       + " (or set AGENTS_AUTH_DISABLED=1 on a developer machine)")

app = FastAPI(title="Agent Builder", version="0.1.0", docs_url=None, redoc_url=None)
engine = make_engine(settings.sessions_url(DATA_DIR))
app.state.settings = settings
app.state.engine = engine
app.state.sessions = sessionmaker(engine)
app.state.oidc = OidcClient(settings)
app.state.refresh_locks = SessionLocks()

# Every /api route goes through require_user: login and the access role, or the demo user
# when login is switched off. Nothing under /api is public.
api = APIRouter(prefix="/api", dependencies=[Depends(require_user)])

# ไฟล์ที่ tool ผลิต — เอา path มาจาก python_tools ตัวเดียว ไม่ประกาศซ้ำ
# ไม่งั้นวันหนึ่งมีคนแก้ฝั่งเดียว แล้ว tool เขียนลงที่ที่ไม่มีใครเสิร์ฟ
DOWNLOADS_DIR = python_tools.downloads_dir()


@app.get(python_tools.DOWNLOAD_URL_PREFIX + "/{name}", dependencies=[Depends(require_user)])
def download(name: str) -> FileResponse:
    """A generated file. Behind login like the API, so not a static mount."""
    target = DOWNLOADS_DIR / Path(name).name
    if Path(name).name != name or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)


@app.exception_handler(NoAccess)
async def no_access(_request: Request, _error: NoAccess) -> JSONResponse:
    # The frontend sends the browser on to denied_url, see frontend/src/api.ts.
    return JSONResponse({"detail": "no_access", "denied_url": settings.denied_url}, 403)


store = SpecStore()
connections = ConnectionStore()

# checkpointer เป็น Tier B: ระบบเลือกให้ ไม่ใช่ user
try:
    from langgraph.checkpoint.memory import InMemorySaver

    CHECKPOINTER: Any = InMemorySaver()
except Exception:  # noqa: BLE001
    CHECKPOINTER = None


@app.on_event("startup")
def _seed() -> None:
    Base.metadata.create_all(engine)
    with app.state.sessions() as db:
        delete_expired_sessions(db)
        db.commit()
    if settings.auth_disabled:
        log.warning("AGENTS_AUTH_DISABLED is set: no login, every request is the user 'demo'")
    created = store.seed_from(HERE.parent / "specs")
    if created:
        log.info("seeded %d sample spec(s)", len(created))


# --------------------------------------------------------------------------- registry
@api.get("/registry")
def get_registry() -> dict[str, Any]:
    """ฟอร์มฝั่ง UI generate ตัวเองจากอันนี้ — เพิ่ม tool ในโค้ดแล้ว UI มีตัวเลือกใหม่เอง"""
    return {
        "connections": [c.public() for c in connections.list()],
        "connection_kinds": [{"ref": k, "label": v} for k, v in KIND_LABELS.items()],
        "models": model_registry.describe(),
        "tools": tool_registry.describe(),
        "python_tools": python_tools.describe(),
        "middleware": mw_registry.describe(),
        "backend_presets": [
            {"ref": "state", "label": "State (ephemeral ต่อ thread)"},
            {"ref": "store", "label": "Store (ข้ามthread)"},
        ],
        "tool_kinds": ["builtin", "http", "python", "mcp"],
        "subagent_modes": ["isolated", "fork"],
        "json_schema": AgentSpec.model_json_schema(),
    }


# --------------------------------------------------------------------------- specs
class ValidateResult(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)
    fingerprint: str | None = None


@api.post("/specs/validate", response_model=ValidateResult)
def validate_spec(body: dict[str, Any]) -> ValidateResult:
    try:
        spec = AgentSpec.model_validate(body)
    except Exception as exc:  # noqa: BLE001
        return ValidateResult(ok=False, problems=[str(exc)])
    problems = spec.validate_refs() + _connection_problems(spec)
    return ValidateResult(ok=not problems, problems=problems, fingerprint=spec.fingerprint())


# --------------------------------------------------------------------------- audit
def _actor(request: Request) -> dict[str, Any]:
    """Who did it, for the audit log: the signed-in user that require_user put on the request."""
    user: User = request.state.user
    return {
        "actor_type": "user",
        "actor_id": user.sub,
        "actor_name": user.username,
        "ip_address": (request.client.host if request.client else None),
        "request_id": request.headers.get("x-request-id"),
        "request_url": str(request.url),
    }


@api.get("/me")
def me(request: Request) -> dict[str, Any]:
    return {
        "username": request.state.user.username,
        "auth": not settings.auth_disabled,
        "portal_url": settings.portal_url,
    }


def _snapshot(spec: AgentSpec) -> dict[str, Any]:
    """ค่าที่เอาไปเทียบหา changed_fields — ใส่ fingerprint ติดไปด้วย"""
    d = spec.model_dump(mode="json")
    d["_fingerprint"] = spec.fingerprint()
    return d


@api.get("/agents")
def list_agents() -> list[dict[str, Any]]:
    return store.list_agents()


@api.post("/agents")
def create_agent(body: dict[str, Any], request: Request) -> dict[str, Any]:
    spec = _parse(body)
    saved = store.create(spec)
    AUDIT.config_change(agent_id=saved.id or saved.name, actor=_actor(request),
                        old=None, new=_snapshot(saved), action="create")
    return saved.model_dump(mode="json")


@api.get("/agents/{agent_id}")
def get_agent(agent_id: str, version: int | None = None) -> dict[str, Any]:
    try:
        return store.get(agent_id, version).model_dump(mode="json")
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@api.put("/agents/{agent_id}")
def update_agent(agent_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    spec = _parse(body)
    try:
        before = _snapshot(store.get(agent_id))      # อ่านค่าเดิมก่อนเขียนทับ ไม่งั้นไม่มี old_values
    except Exception:  # noqa: BLE001
        before = None
    saved = store.update(agent_id, spec)
    factory.invalidate(agent_id)  # กันเคส "แก้แล้วไม่เปลี่ยน"
    AUDIT.config_change(agent_id=agent_id, actor=_actor(request),
                        old=before, new=_snapshot(saved), action="update")
    return saved.model_dump(mode="json")


@api.get("/agents/{agent_id}/versions")
def agent_versions(agent_id: str) -> dict[str, Any]:
    return {"versions": store.versions(agent_id), "head": store.head(agent_id)}


@api.post("/agents/{agent_id}/activate/{version}")
def activate_version(agent_id: str, version: int, request: Request) -> dict[str, Any]:
    try:
        before = _snapshot(store.get(agent_id))
    except Exception:  # noqa: BLE001
        before = None
    try:
        spec = store.activate(agent_id, version)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    factory.invalidate(agent_id)
    AUDIT.config_change(agent_id=agent_id, actor=_actor(request),
                        old=before, new=_snapshot(spec), action="update")
    return spec.model_dump(mode="json")


@api.delete("/agents/{agent_id}")
def delete_agent(agent_id: str, request: Request) -> dict[str, str]:
    try:
        before = _snapshot(store.get(agent_id))
    except Exception:  # noqa: BLE001
        before = None
    store.delete(agent_id)
    AUDIT.write({
        "resource_type": "agent", "resource_id": agent_id, "action": "delete", "status": "success",
        **{k: v for k, v in _actor(request).items() if k != "request_url"},
        "request": f"delete agent {agent_id}",
        "old_values": {"name": (before or {}).get("name"), "version": (before or {}).get("version")},
        "optional_info": {"event": "agent.config.change"},
    })
    factory.invalidate(agent_id)
    return {"status": "deleted"}


def _parse(body: dict[str, Any]) -> AgentSpec:
    try:
        spec = AgentSpec.model_validate(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"spec ไม่ถูกต้อง: {exc}") from exc
    problems = spec.validate_refs() + _connection_problems(spec)
    if problems:
        raise HTTPException(422, {"problems": problems})
    return spec


def _connection_problems(spec: AgentSpec) -> list[str]:
    """เช็คว่า connection ที่ spec อ้างมีอยู่จริง — ต้องทำที่นี่ เพราะ spec.py
    ไม่ควรรู้จัก store (จะได้ export/import spec ข้ามเครื่องได้)"""
    out: list[str] = []
    refs = {spec.model.connection} | {sa.connection for sa in spec.subagents}
    for ref in {r for r in refs if r}:
        try:
            connections.get(ref)  # type: ignore[arg-type]
        except FileNotFoundError:
            out.append(f"อ้าง connection '{ref}' ที่ยังไม่ได้สร้าง")
    return out


# --------------------------------------------------------------------------- connections
class ConnectionTest(BaseModel):
    model: str = ""


@api.get("/connections")
def list_connections() -> list[dict[str, Any]]:
    """ไม่มี api key จริงในผลลัพธ์ มีแค่ค่าที่ mask แล้ว"""
    return [c.public() for c in connections.list()]


@api.post("/connections")
def create_connection(body: ConnectionInput) -> dict[str, Any]:
    try:
        return connections.create(body).public()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@api.get("/connections/{cid}")
def get_connection(cid: str) -> dict[str, Any]:
    try:
        return connections.get(cid).public()
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@api.put("/connections/{cid}")
def update_connection(cid: str, body: ConnectionInput) -> dict[str, Any]:
    """api_key เว้นว่าง = คงของเดิม จะได้แก้ base_url โดยไม่ต้องพิมพ์ key ใหม่"""
    try:
        conn = connections.update(cid, body)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    factory.invalidate()  # agent ที่ cache ไว้ถือ model instance เก่าอยู่
    return conn.public()


@api.delete("/connections/{cid}")
def delete_connection(cid: str) -> dict[str, str]:
    try:
        connections.delete(cid)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    factory.invalidate()
    return {"status": "deleted"}


@api.post("/connections/{cid}/apply-to-all")
def apply_connection_to_all(cid: str, body: ConnectionTest) -> dict[str, Any]:
    """ตั้ง connection นี้ให้ agent ทุกตัว (บันทึกเป็นเวอร์ชันใหม่ ไม่ทับของเดิม).

    มีไว้เพื่อไม่ให้ผู้ใช้ต้องไล่แก้ JSON ทีละ agent หลังสร้าง connection แรก
    """
    try:
        conn = connections.get(cid)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    updated = []
    for row in store.list_agents():
        spec = store.get(row["id"])
        model = spec.model.model_copy(update={
            "connection": conn.name,
            "ref": body.model or conn.default_model or spec.model.ref,
        })
        saved = store.update(row["id"], spec.model_copy(update={"model": model}))
        updated.append({"id": saved.id, "name": saved.name, "version": saved.version})
    factory.invalidate()
    return {"connection": conn.name, "updated": updated}


@api.post("/connections/{cid}/test")
async def test_connection_endpoint(cid: str, body: ConnectionTest) -> dict[str, Any]:
    """ยิงจริงหนึ่งครั้งสั้น ๆ — บอกผู้ใช้ตรงนั้นเลยว่า base_url/key ใช้ได้ไหม
    ดีกว่าปล่อยให้ไปพังตอนแชทแล้วงงว่าพังเพราะอะไร"""
    try:
        conn = connections.get(cid)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return await asyncio.to_thread(test_connection, conn, body.model)


# --------------------------------------------------------------------------- python tools
class PythonToolDraft(BaseModel):
    code: str
    kwargs: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 15.0


@api.post("/tools/python/check")
def check_python_tool(body: PythonToolDraft) -> dict[str, Any]:
    """ตรวจโค้ดแบบไม่รัน + อนุมาน schema ให้ดูก่อนบันทึก

    แยกจาก /run เพราะอยากให้ผู้ใช้เห็นว่า agent จะเห็น tool นี้หน้าตายังไง
    (ชื่อ argument, ชนิด, description) ก่อนจะยอมรันโค้ดของตัวเองด้วยซ้ำ
    """
    problems = python_tools.guard_code(body.code)
    return {
        "enabled": python_tools.enabled(),
        "ok": not problems and python_tools.enabled(),
        "problems": problems,
        "input_schema": None if problems else python_tools.infer_schema(body.code),
        "description": python_tools.extract_docstring(body.code),
    }


@api.post("/tools/python/run")
async def run_python_tool(body: PythonToolDraft) -> dict[str, Any]:
    """ลองรันจริงด้วย argument ที่ผู้ใช้ใส่มา — ดีบักในหน้าเว็บได้เลย ไม่ต้องผ่าน agent"""
    if not python_tools.enabled():
        raise HTTPException(403, "tool ชนิด python ถูกปิดอยู่ — ตั้ง ALLOW_PYTHON_TOOLS=1 ก่อน")
    problems = python_tools.guard_code(body.code)
    if problems:
        return {"ok": False, "error": "ไม่ผ่านการตรวจ", "problems": problems}
    return await asyncio.to_thread(
        python_tools.run_code, body.code, body.kwargs, timeout_s=body.timeout_s
    )


# --------------------------------------------------------------------------- run
class RunRequest(BaseModel):
    agent_id: str | None = None
    version: int | None = None
    spec: dict[str, Any] | None = Field(default=None, description="ส่ง spec สด ๆ มาลองได้โดยไม่ต้องบันทึก")
    thread_id: str = "default"
    message: str


class ResumeRequest(BaseModel):
    agent_id: str | None = None
    spec: dict[str, Any] | None = None
    thread_id: str = "default"
    decision: str = Field(default="accept", description="accept | reject | edit")
    edited_args: dict[str, Any] | None = None


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _resolve_spec(agent_id: str | None, version: int | None, raw: dict[str, Any] | None) -> AgentSpec:
    if raw is not None:
        return AgentSpec.model_validate(raw)
    if not agent_id:
        raise HTTPException(400, "ต้องส่ง agent_id หรือ spec อย่างใดอย่างหนึ่ง")
    try:
        return store.get(agent_id, version)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


async def _stream(built: factory.BuiltAgent, payload: Any, thread_id: str, ra: RunAudit | None = None):
    started = time.monotonic()
    budget = built.spec.limits.run_timeout_s
    config = {**built.config, "configurable": {"thread_id": thread_id}}

    def emit(event: str, data: Any) -> str:
        """ยิง SSE ให้ browser + เก็บ event ดิบลง raw trace ในคราวเดียว"""
        if ra is not None:
            ra.raw(event, data)
        return _sse(event, data)

    if ra is not None:
        ra.start()                                  # สเปกข้อ 3.2: เขียน run.start ก่อนเริ่มประมวลผล

    yield emit("start", {
        "agent": built.spec.name, "version": built.spec.version,
        "model": (f"{built.spec.model.connection}/" if built.spec.model.connection else "")
                 + built.spec.model.ref,
        "tools": built.tool_names,
        "fingerprint": built.spec.fingerprint(),
        "run_id": ra.run_id if ra else None,
    })

    try:
        async for mode, chunk in built.agent.astream(
            payload, config=config, stream_mode=["updates", "messages"]
        ):
            if time.monotonic() - started > budget:
                msg = f"เกิน run_timeout_s ({budget}s) — ตัดจบตาม limits ใน spec"
                yield emit("error", {"message": msg})
                if ra is not None:
                    ra.end(status="failed", error=msg)
                return

            if mode == "messages":
                msg, meta = chunk
                text = getattr(msg, "content", "")
                if isinstance(text, list):  # content blocks
                    text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
                if text:
                    yield emit("token", {"text": text, "node": (meta or {}).get("langgraph_node")})

            elif mode == "updates":
                for node, update in (chunk or {}).items():
                    if node == "__interrupt__":
                        data = {"payload": _interrupts(update)}
                        yield emit("interrupt", data)
                        if ra is not None:
                            ra.interrupt(data["payload"])
                        return
                    calls = _tool_calls(update, ra)
                    if calls:
                        yield emit("tool_calls", {"node": node, "calls": calls})
                    else:
                        yield emit("step", {"node": node})
    except Exception as exc:  # noqa: BLE001
        log.exception("run failed")
        detail = f"{type(exc).__name__}: {exc}"
        yield emit("error", {"message": detail})
        if ra is not None:
            ra.end(status="failed", error=detail)
        return

    yield emit("done", {"elapsed_s": round(time.monotonic() - started, 2)})
    if ra is not None:
        ra.end(status="success")


def _tool_calls(update: Any, ra: RunAudit | None = None) -> list[dict[str, Any]]:
    """ดึง tool call ที่โมเดลสั่ง และผลลัพธ์ที่ tool คืนกลับ ออกมาจาก state update ก้อนเดียวกัน

    tool_call_id เป็นของ LangChain อยู่แล้ว ใช้จับคู่ AIMessage.tool_calls กับ ToolMessage
    เพื่อเขียน audit แถวเดียวที่มีทั้ง args และ result ตามสเปกข้อ 1.3
    """
    out: list[dict[str, Any]] = []
    msgs = (update or {}).get("messages") if isinstance(update, dict) else None
    for m in msgs or []:
        tcid = getattr(m, "tool_call_id", None)          # ToolMessage = ผลลัพธ์
        if tcid and ra is not None:
            ra.tool_result_seen(
                tcid,
                getattr(m, "content", ""),
                status=getattr(m, "status", None),
                name=getattr(m, "name", None),
            )
        for tc in getattr(m, "tool_calls", None) or []:   # AIMessage = คำสั่งเรียก
            if ra is not None:
                ra.tool_call_seen(tc)
            out.append({"name": tc.get("name"), "args": tc.get("args"), "id": tc.get("id")})
    return out


def _interrupts(update: Any) -> list[dict[str, Any]]:
    """Interrupt ของ LangGraph ไม่ใช่ JSON — ดึง id กับ value (action_requests) ออกมาให้ UI ใช้ได้"""
    return [{"id": getattr(i, "id", None), "value": _jsonable(getattr(i, "value", i))}
            for i in (update if isinstance(update, (list, tuple)) else [update])]


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


@api.post("/runs/stream")
async def run_stream(req: RunRequest, request: Request) -> StreamingResponse:
    spec = _resolve_spec(req.agent_id, req.version, req.spec)
    try:
        built = factory.get_agent(spec, checkpointer=CHECKPOINTER)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"สร้าง agent จาก spec ไม่ได้: {exc}") from exc

    ra = AUDIT.begin_run(spec=spec, message=req.message,
                         actor=_actor(request), thread_id=req.thread_id, kind="run")
    payload = {"messages": [{"role": "user", "content": req.message}]}
    return StreamingResponse(
        _stream(built, payload, req.thread_id, ra),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@api.post("/runs/resume")
async def run_resume(req: ResumeRequest, request: Request) -> StreamingResponse:
    """HITL: ต่อ run ที่ค้างอยู่ตรง interrupt.

    ออกแบบไว้ตั้งแต่ P2 ตามที่เอกสารเตือน — ถ้าไปเพิ่มทีหลังจะกระทบ API ทั้งชุด
    """
    spec = _resolve_spec(req.agent_id, None, req.spec)
    built = factory.get_agent(spec, checkpointer=CHECKPOINTER)

    from langgraph.types import Command

    # langchain 1.x: resume ต้องเป็น {"decisions": [...]} หนึ่งอันต่อ tool call ที่รออยู่
    # และชนิดคือ approve | edit | reject (API นี้ยังรับ "accept" แบบเดิมด้วย)
    kind = {"accept": "approve"}.get(req.decision, req.decision)
    if kind not in ("approve", "edit", "reject"):
        raise HTTPException(400, "decision ต้องเป็น accept | reject | edit")
    state = await built.agent.aget_state({"configurable": {"thread_id": req.thread_id}})
    pending: list[dict[str, Any]] = []
    for it in getattr(state, "interrupts", None) or []:
        value = getattr(it, "value", None)
        if isinstance(value, dict):
            pending.extend(value.get("action_requests") or [])
    if not pending:
        raise HTTPException(409, "ไม่มี run ที่รออนุมัติอยู่ใน thread นี้")

    decisions: list[dict[str, Any]] = []
    for index, action in enumerate(pending):
        if kind == "edit" and index == 0 and req.edited_args is not None:
            decisions.append({"type": "edit",
                              "edited_action": {"name": action["name"], "args": req.edited_args}})
        elif kind == "reject":
            decisions.append({"type": "reject"})
        else:
            decisions.append({"type": "approve"})

    ra = AUDIT.begin_run(spec=spec, message=f"(resume: {req.decision})",
                         actor=_actor(request), thread_id=req.thread_id, kind="resume")
    ra.hitl(req.decision, edited_args=req.edited_args,
            tool_name=", ".join(a["name"] for a in pending))   # ใครกดอนุมัติ บันทึกก่อนเดินต่อ

    return StreamingResponse(
        _stream(built, Command(resume={"decisions": decisions}), req.thread_id, ra),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



# --------------------------------------------------------------------------- audit API
@api.get("/audit/rows")
def audit_rows(limit: int = 200, request_id: str | None = None,
               resource_type: str | None = None, status: str | None = None) -> dict[str, Any]:
    """แถวที่จะไปอยู่ในตาราง audit_logs ของ Portal"""
    return {"rows": AUDIT.rows(limit=limit, request_id=request_id,
                               resource_type=resource_type, status=status)}


@api.get("/audit/runs")
def audit_runs(limit: int = 50) -> dict[str, Any]:
    """รวมแถวเป็นราย run ให้หน้า viewer โชว์เป็นกลุ่มได้"""
    return {"runs": AUDIT.runs(limit=limit)}


@api.get("/audit/raw/{run_id}")
def audit_raw(run_id: str) -> dict[str, Any]:
    """trace เต็มของ run นั้น = ของที่จะขึ้น MinIO ไม่ลง Postgres"""
    trace = AUDIT.raw_trace(run_id)
    if trace is None:
        raise HTTPException(404, "ไม่พบ raw trace ของ run นี้")
    return trace


@api.get("/audit/stats")
def audit_stats() -> dict[str, Any]:
    return AUDIT.stats()


@api.get("/audit/append-only-check")
def audit_append_only_check() -> dict[str, Any]:
    """พิสูจน์ว่าแถวที่เขียนแล้วแก้/ลบไม่ได้ — ตรงกับ trigger ของ Portal"""
    out: dict[str, Any] = {}
    for op, sql in (("update", "UPDATE audit_logs SET status='tampered'"),
                    ("delete", "DELETE FROM audit_logs")):
        try:
            with AUDIT._connect() as conn:  # noqa: SLF001 — เป็น endpoint สาธิตโดยเฉพาะ
                conn.execute(sql)
            out[op] = {"blocked": False, "error": None}
        except Exception as exc:  # noqa: BLE001
            out[op] = {"blocked": True, "error": str(exc)}
    return out


# --------------------------------------------------------------------------- misc
@api.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "checkpointer": CHECKPOINTER is not None,
        "python_tools_enabled": python_tools.enabled(),
        "downloads_dir": str(DOWNLOADS_DIR),
        "downloads_count": len(list(DOWNLOADS_DIR.glob("*"))),
        "data_dir": str(store.root),
        "audit": AUDIT.stats(),
        "connections": [{"name": c.name, "kind": c.kind, "has_key": bool(c.api_key_enc)}
                        for c in connections.list()],
        "env_keys_present": [k["provider"] for k in model_registry.describe()["keys"] if k["present"]],
    }


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, bool]:
    """For container health checks. Says nothing about the configuration."""
    return {"ok": True}


def mount_frontend(dist: Path) -> None:
    if not (dist / "index.html").is_file():
        return  # Vite serves the frontend during development
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.split("/")[0] in {"api", "auth", "downloads"}:
            raise HTTPException(404)
        return FileResponse(dist / "index.html")


app.include_router(api)
if not settings.auth_disabled:
    app.include_router(auth_routes.router)
mount_frontend(Path(settings.frontend_dist or HERE.parents[1] / "frontend" / "dist"))
