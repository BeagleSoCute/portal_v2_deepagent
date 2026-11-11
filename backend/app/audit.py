"""audit.py — เขียน audit log ของ DeepAgent ตามสเปกที่จะไปใช้ใน Portal

ของจริงใน Portal จะเขียนลงตาราง `audit_logs` ใน audit_db (Postgres) ผ่าน
`audit_log_service` ที่มีอยู่แล้ว POC นี้เขียน "แถวหน้าตาเดียวกัน" ลง 3 ที่
เพื่อให้ทีมเห็นภาพว่าอะไรจะไปอยู่ที่ไหน:

  1. SQLite  data/audit/audit.db
     ตาราง `audit_logs` คอลัมน์ตรงกับ Portal ทั้ง 18 ช่อง
     + trigger กัน UPDATE/DELETE เลียนแบบ `audit_logs_no_update_delete` ของจริง
     -> นี่คือส่วนที่จะเป็น Postgres

  2. JSONL   data/audit/rows/<วันที่>.jsonl
     แถวเดียวกับข้อ 1 แต่เป็นไฟล์รายวัน = รูปร่างของสิ่งที่จะ archive ขึ้น MinIO
     ตอนที่ partition เก่าถูกย้ายออกจาก Postgres

  3. RAW     data/audit/raw/<วันที่>/<run_id>.json
     "ทุกอย่าง" ที่ agent ส่งกลับมาตอนแชท — token ทุกก้อน, tool args เต็ม,
     ผลลัพธ์เต็ม, interrupt payload
     -> ของก้อนนี้ **ไม่ลง Postgres** เพราะปริมาณมหาศาลและไม่ตอบคำถามเชิงตรวจสอบ
        ถ้าจะเก็บ ต้องขึ้น MinIO ตรง ๆ แล้วให้แถวใน audit_logs ถือแค่ object key

เขียน audit ต้องไม่ทำให้ run ของผู้ใช้ล้ม -> ทุก public method ห่อ try/except
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("audit")

MAX_TEXT = 2000          # ตามสเปก ข้อ 3.3 — ตัดข้อความอิสระที่ 2,000 ตัวอักษร
APP_VERSION = "poc-deepagent-0.1"

# คอลัมน์ทั้ง 18 ช่อง ลอกจาก AuditLogModel ของ Portal ตรง ๆ
COLUMNS = [
    "id", "created_at", "resource_type", "resource_id", "action", "status",
    "actor_id", "actor_type", "actor_name", "ip_address",
    "request_id", "request", "result",
    "changed_fields", "old_values", "new_values",
    "app_version", "optional_info",
]
JSON_COLUMNS = {"result", "changed_fields", "old_values", "new_values", "optional_info"}

DDL = f"""
CREATE TABLE IF NOT EXISTS audit_logs (
  id             TEXT PRIMARY KEY,
  created_at     TEXT NOT NULL,
  resource_type  TEXT NOT NULL,
  resource_id    TEXT,
  action         TEXT NOT NULL,
  status         TEXT,
  actor_id       TEXT,
  actor_type     TEXT,
  actor_name     TEXT,
  ip_address     TEXT,
  request_id     TEXT,
  request        TEXT,
  result         TEXT,
  changed_fields TEXT,
  old_values     TEXT,
  new_values     TEXT,
  app_version    TEXT,
  optional_info  TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_created  ON audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_request  ON audit_logs (request_id);
CREATE INDEX IF NOT EXISTS ix_audit_resource ON audit_logs (resource_type, resource_id);

-- เลียน a002_append_only.py ของ Portal: แถวที่เขียนแล้วแก้ไม่ได้ ลบไม่ได้
CREATE TRIGGER IF NOT EXISTS audit_logs_no_update
BEFORE UPDATE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only: UPDATE is not allowed'); END;

CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete
BEFORE DELETE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only: DELETE is not allowed'); END;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        blob = bytes(value)
    elif isinstance(value, str):
        blob = value.encode("utf-8")
    else:
        blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _clip(text: Any, limit: int = MAX_TEXT) -> tuple[str, bool]:
    """คืน (ข้อความที่ตัดแล้ว, ถูกตัดหรือไม่)."""
    s = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return s, False
    return s[:limit], True


def audit_dir() -> Path:
    """ที่เก็บ audit — AUDIT_DIR ถ้าตั้งไว้ ไม่งั้นอยู่ใต้ DATA_DIR เหมือน store ตัวอื่น."""
    raw = os.getenv("AUDIT_DIR", "").strip()
    data = os.getenv("DATA_DIR", "").strip()
    default = Path(data).resolve() if data else Path(__file__).resolve().parents[1] / "data"
    root = Path(raw) if raw else default / "audit"
    root.mkdir(parents=True, exist_ok=True)
    return root


class AuditSink:
    """เขียนแถวลง SQLite + JSONL และเก็บ raw trace แยกไฟล์."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or audit_dir()
        self.db_path = self.root / "audit.db"
        self.rows_dir = self.root / "rows"
        self.raw_dir = self.root / "raw"
        self.rows_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ---------------------------------------------------------------- internal
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(DDL)

    def write(self, row: dict[str, Any]) -> dict[str, Any]:
        """เขียนแถวเดียวลงทั้ง SQLite และ JSONL — ล้มก็ไม่โยนต่อ."""
        full = {k: row.get(k) for k in COLUMNS}
        full["id"] = full["id"] or str(uuid.uuid4())
        full["created_at"] = full["created_at"] or _now()
        full["app_version"] = full["app_version"] or APP_VERSION
        try:
            with self._lock:
                flat = {
                    k: (json.dumps(v, ensure_ascii=False, default=str)
                        if k in JSON_COLUMNS and v is not None else v)
                    for k, v in full.items()
                }
                with self._connect() as conn:
                    conn.execute(
                        f"INSERT INTO audit_logs ({','.join(COLUMNS)}) "
                        f"VALUES ({','.join('?' for _ in COLUMNS)})",
                        [flat[k] for k in COLUMNS],
                    )
                day = full["created_at"][:10]
                with (self.rows_dir / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(full, ensure_ascii=False, default=str) + "\n")
        except Exception:  # noqa: BLE001 — สเปกข้อ 5: audit ล้ม run ต้องไม่ล้ม
            log.exception("เขียน audit ไม่สำเร็จ (run ยังทำงานต่อ)")
        return full

    # ------------------------------------------------------------------ query
    def rows(
        self,
        limit: int = 200,
        request_id: str | None = None,
        resource_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM audit_logs"
        where, args = [], []
        if request_id:
            where.append("request_id = ?"); args.append(request_id)
        if resource_type:
            where.append("resource_type = ?"); args.append(resource_type)
        if status:
            where.append("status = ?"); args.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        args.append(max(1, min(limit, 2000)))
        with self._connect() as conn:
            out = []
            for r in conn.execute(sql, args).fetchall():
                d = dict(r)
                for k in JSON_COLUMNS:
                    if d.get(k):
                        try:
                            d[k] = json.loads(d[k])
                        except Exception:  # noqa: BLE001
                            pass
                out.append(d)
            return out

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """สรุปทีละ run เพื่อให้หน้า viewer โชว์เป็นกลุ่มได้."""
        sql = """
        SELECT request_id,
               MIN(created_at) AS started_at,
               COUNT(*)        AS rows_count,
               SUM(CASE WHEN resource_type='agent_tool' THEN 1 ELSE 0 END) AS tool_calls,
               SUM(CASE WHEN status='failed'            THEN 1 ELSE 0 END) AS failed_rows
        FROM audit_logs
        WHERE request_id IS NOT NULL
        GROUP BY request_id
        ORDER BY started_at DESC
        LIMIT ?
        """
        with self._connect() as conn:
            runs = [dict(r) for r in conn.execute(sql, (max(1, min(limit, 500)),)).fetchall()]
        for r in runs:
            end = self.rows(limit=50, request_id=r["request_id"])
            agent_rows = [x for x in end if x["resource_type"] == "agent"]
            first = agent_rows[-1] if agent_rows else None
            last = agent_rows[0] if agent_rows else None
            info = (first or {}).get("optional_info") or {}
            r["agent_name"] = info.get("agent_name")
            r["version"] = info.get("version")
            r["model"] = info.get("model_ref")
            r["question"] = (first or {}).get("request")
            r["status"] = (last or {}).get("status")
            r["elapsed_ms"] = ((last or {}).get("result") or {}).get("elapsed_ms")
            r["raw_bytes"] = self.raw_size(r["request_id"])
        return runs

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
            by_type = {r[0]: r[1] for r in conn.execute(
                "SELECT resource_type, COUNT(*) FROM audit_logs GROUP BY resource_type").fetchall()}
        jsonl_bytes = sum(p.stat().st_size for p in self.rows_dir.glob("*.jsonl"))
        raw_bytes = sum(p.stat().st_size for p in self.raw_dir.rglob("*.json"))
        return {
            "total_rows": total,
            "by_resource_type": by_type,
            "postgres_bound": {  # ส่วนที่จะอยู่ในตาราง audit_logs จริง
                "sqlite_path": str(self.db_path),
                "sqlite_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            },
            "minio_bound": {     # ส่วนที่จะขึ้น object storage
                "rows_jsonl_files": len(list(self.rows_dir.glob("*.jsonl"))),
                "rows_jsonl_bytes": jsonl_bytes,
                "raw_trace_files": len(list(self.raw_dir.rglob("*.json"))),
                "raw_trace_bytes": raw_bytes,
            },
        }

    # -------------------------------------------------------------------- raw
    def _raw_path(self, run_id: str, day: str | None = None) -> Path:
        day = day or _now()[:10]
        d = self.raw_dir / day
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{run_id}.json"

    def raw_size(self, run_id: str) -> int:
        for p in self.raw_dir.rglob(f"{run_id}.json"):
            return p.stat().st_size
        return 0

    def raw_trace(self, run_id: str) -> dict[str, Any] | None:
        for p in self.raw_dir.rglob(f"{run_id}.json"):
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return None
        return None

    def save_raw(self, run_id: str, trace: dict[str, Any]) -> None:
        try:
            self._raw_path(run_id).write_text(
                json.dumps(trace, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception:  # noqa: BLE001
            log.exception("เขียน raw trace ไม่สำเร็จ")

    # ------------------------------------------------------- agent.config.change
    def config_change(
        self,
        *,
        agent_id: str,
        actor: dict[str, Any],
        old: dict[str, Any] | None,
        new: dict[str, Any],
        action: str = "update",
    ) -> dict[str, Any]:
        """event ที่สำคัญที่สุดในสเปก — แก้ prompt/tool/model ของ agent = เทียบเท่า deploy."""
        watch = ["instructions", "model", "tools", "subagents", "middleware", "hitl", "limits",
                 "skills", "memory", "mcp_servers", "backend_preset", "description"]
        old = old or {}
        changed = [k for k in watch if json.dumps(old.get(k), sort_keys=True, default=str)
                   != json.dumps(new.get(k), sort_keys=True, default=str)]
        return self.write({
            "resource_type": "agent",
            "resource_id": agent_id,
            "action": action,
            "status": "success",
            "actor_type": actor.get("actor_type", "user"),
            "actor_id": actor.get("actor_id"),
            "actor_name": actor.get("actor_name"),
            "ip_address": actor.get("ip_address"),
            "request_id": actor.get("request_id"),
            "request": f"{action} agent config: {new.get('name')}",
            "changed_fields": changed,
            # เก็บแค่ field ที่เปลี่ยน ไม่ดัมพ์ spec ทั้งก้อน
            "old_values": {k: old.get(k) for k in changed} or None,
            "new_values": {k: new.get(k) for k in changed} or None,
            "optional_info": {
                "event": "agent.config.change",
                "from_version": old.get("version"),
                "to_version": new.get("version"),
                "from_fingerprint": old.get("_fingerprint"),
                "to_fingerprint": new.get("_fingerprint"),
            },
        })

    # ------------------------------------------------------------------- run
    def begin_run(self, **kw: Any) -> "RunAudit":
        return RunAudit(self, **kw)


class RunAudit:
    """อายุเท่ากับ run เดียว — ถือ state ที่ต้องใช้จับคู่ tool_call กับ ToolMessage."""

    def __init__(
        self,
        sink: AuditSink,
        *,
        spec: Any,
        message: str,
        actor: dict[str, Any] | None = None,
        thread_id: str = "default",
        kind: str = "run",
    ) -> None:
        self.sink = sink
        self.spec = spec
        self.run_id = str(uuid.uuid4())
        self.actor = actor or {}
        self.thread_id = thread_id
        self.kind = kind
        self.message = message
        self.t0 = time.monotonic()
        self.agent_id = getattr(spec, "id", None) or getattr(spec, "name", "unsaved")

        self._pending: dict[str, dict[str, Any]] = {}   # tool_call_id -> {name,args,t0}
        self._seen_failed: dict[str, str] = {}          # tool name -> tool_call_id ที่ล้มก่อนหน้า
        self.tool_calls = 0
        self.tool_errors = 0
        self.files: list[dict[str, Any]] = []
        self._raw: list[dict[str, Any]] = []            # ทุก event ที่ agent ส่งกลับ
        self._tokens: list[str] = []

        try:
            self._kinds = {}
            for t in getattr(spec, "tools", []) or []:
                ref = getattr(t, "ref", None) or getattr(t, "server", None)
                if ref:
                    self._kinds[ref] = getattr(t, "kind", "builtin")
        except Exception:  # noqa: BLE001
            self._kinds = {}

    # --------------------------------------------------------------- helpers
    def _base(self) -> dict[str, Any]:
        return {
            "resource_id": self.agent_id,
            "actor_type": self.actor.get("actor_type", "user"),
            "actor_id": self.actor.get("actor_id"),
            "actor_name": self.actor.get("actor_name"),
            "ip_address": self.actor.get("ip_address"),
            "request_id": self.run_id,
        }

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def raw(self, event: str, data: Any) -> None:
        """เก็บ event ดิบ — ของก้อนนี้คือส่วนที่จะขึ้น MinIO ไม่ลง Postgres."""
        try:
            if event == "token":
                self._tokens.append((data or {}).get("text", ""))
            self._raw.append({"t_ms": self._elapsed_ms(), "event": event, "data": data})
        except Exception:  # noqa: BLE001
            pass

    # ----------------------------------------------------------------- events
    def start(self) -> dict[str, Any]:
        req, truncated = _clip(self.message)
        model = getattr(self.spec, "model", None)
        return self.sink.write({
            **self._base(),
            "resource_type": "agent",
            "action": "execute",
            "status": None,          # ตามสเปก: run.start ยังไม่มีผลลัพธ์ -> status ว่าง
            "request": req,
            "optional_info": {
                "event": "run.start",
                "agent_name": getattr(self.spec, "name", None),
                "version": getattr(self.spec, "version", None),
                "fingerprint": (self.spec.fingerprint() if hasattr(self.spec, "fingerprint") else None),
                "model_connection": getattr(model, "connection", None),
                "model_ref": getattr(model, "ref", None),
                "limits": (self.spec.limits.model_dump() if hasattr(self.spec, "limits") else None),
                "thread_id": self.thread_id,
                "run_kind": self.kind,
                "question_truncated": truncated,
                "question_sha256": _sha256(self.message),
            },
        })

    def tool_call_seen(self, call: dict[str, Any]) -> None:
        """โมเดลสั่งเรียก tool — ยังไม่เขียนแถว รอผลก่อนตามสเปกข้อ 1.3."""
        tcid = call.get("id") or str(uuid.uuid4())
        self._pending[tcid] = {
            "name": call.get("name"),
            "args": call.get("args"),
            "t0": time.monotonic(),
        }

    def tool_result_seen(self, tool_call_id: str, content: Any, status: str | None = None,
                         name: str | None = None) -> dict[str, Any] | None:
        """tool ทำงานเสร็จ — ตรงนี้คือจุดที่เขียนแถว tool.call (args + result รวมแถวเดียว)."""
        p = self._pending.pop(tool_call_id, None) or {"name": name, "args": None, "t0": self.t0}
        tool_name = p.get("name") or name or "unknown"
        text, res_truncated = _clip(content)
        failed = status == "error" or (isinstance(content, str) and text.startswith("Error:"))
        args_text, args_truncated = _clip(p.get("args"))

        self.tool_calls += 1
        if failed:
            self.tool_errors += 1

        retry_of = self._seen_failed.get(tool_name)
        if failed:
            self._seen_failed[tool_name] = tool_call_id
        else:
            self._seen_failed.pop(tool_name, None)

        files = _files_in(text)
        self.files.extend(files)

        return self.sink.write({
            **self._base(),
            "resource_type": "agent_tool",
            "action": "execute",
            "status": "failed" if failed else "success",
            "request": tool_name,
            "optional_info": {
                "event": "tool.call",
                "tool_call_id": tool_call_id,
                "tool_kind": self._kinds.get(tool_name, "builtin"),
                "args": args_text,
                "args_truncated": args_truncated,
                "args_sha256": _sha256(p.get("args")),
                "args_bytes": len(json.dumps(p.get("args"), ensure_ascii=False, default=str)),
                "retry_of": retry_of,
                "agent_name": getattr(self.spec, "name", None),
            },
            "result": {
                "elapsed_ms": int((time.monotonic() - p["t0"]) * 1000),
                "result_preview": text,
                "result_truncated": res_truncated,
                "result_sha256": _sha256(content),
                "error": text if failed else None,
                "files": files or None,
            },
        })

    def hitl(self, decision: str, tool_name: str | None = None,
             edited_args: dict[str, Any] | None = None,
             tool_call_id: str | None = None) -> dict[str, Any]:
        """ใครกดอนุมัติ — จุดที่ความรับผิดชอบย้ายจากระบบมาที่คน."""
        return self.sink.write({
            **self._base(),
            "resource_type": "agent",
            "action": "authorize",
            "status": "success" if decision in {"accept", "edit"} else "failed",
            "request": tool_name or "(unknown tool)",
            "optional_info": {"event": "hitl.decision", "agent_name": getattr(self.spec, "name", None)},
            "result": {"decision": decision, "edited_args": edited_args, "tool_call_id": tool_call_id},
        })

    def interrupt(self, payload: Any) -> dict[str, Any]:
        """run หยุดรออนุมัติ — ไม่ใช่ทั้งสำเร็จและไม่ใช่ล้มเหลว."""
        text, _ = _clip(payload)
        self._finish_raw(status="interrupted")
        return self.sink.write({
            **self._base(),
            "resource_type": "agent",
            "action": "execute",
            "status": None,
            "request": "รออนุมัติจากผู้ใช้ (interrupt)",
            "optional_info": {"event": "run.interrupt", "agent_name": getattr(self.spec, "name", None)},
            "result": {"elapsed_ms": self._elapsed_ms(), "interrupt_preview": text,
                       "tool_calls": self.tool_calls},
        })

    def end(self, status: str = "success", error: str | None = None) -> dict[str, Any]:
        err, _ = _clip(error or "")
        self._finish_raw(status=status)
        return self.sink.write({
            **self._base(),
            "resource_type": "agent",
            "action": "execute",
            "status": status,
            "request": _clip(self.message)[0],
            "optional_info": {
                "event": "run.end",
                "agent_name": getattr(self.spec, "name", None),
                "version": getattr(self.spec, "version", None),
                # แถวนี้คือสะพานไปหา MinIO: Postgres ถือแค่ key ไม่ถือเนื้อ
                "raw_trace_key": f"raw/{_now()[:10]}/{self.run_id}.json",
            },
            "result": {
                "elapsed_ms": self._elapsed_ms(),
                "tool_calls": self.tool_calls,
                "tool_errors": self.tool_errors,
                "files_created": self.files or None,
                "unfinished_tool_calls": list(self._pending.keys()) or None,
                "error": err or None,
                "answer_chars": sum(len(t) for t in self._tokens),
            },
        })

    def _finish_raw(self, status: str) -> None:
        self.sink.save_raw(self.run_id, {
            "run_id": self.run_id,
            "saved_at": _now(),
            "status": status,
            "agent": {
                "id": self.agent_id,
                "name": getattr(self.spec, "name", None),
                "version": getattr(self.spec, "version", None),
                "fingerprint": (self.spec.fingerprint() if hasattr(self.spec, "fingerprint") else None),
            },
            "actor": self.actor,
            "thread_id": self.thread_id,
            "question": self.message,
            "answer": "".join(self._tokens),
            "elapsed_ms": self._elapsed_ms(),
            "events": self._raw,
            "_note": "ก้อนนี้คือสิ่งที่ไม่ควรลง Postgres — ขึ้น MinIO แล้วให้ audit_logs ถือแค่ key",
        })


def _files_in(text: str) -> list[dict[str, Any]]:
    """หา path ไฟล์ที่ tool สร้าง แล้วเก็บแค่ path/bytes/sha256 — ห้ามเก็บเนื้อไฟล์."""
    import re
    out: list[dict[str, Any]] = []
    try:
        from . import main as _m  # noqa: PLC0415 — เลี่ยง import วน
        base = getattr(_m, "DOWNLOADS_DIR", None)
    except Exception:  # noqa: BLE001
        base = None
    for path in dict.fromkeys(re.findall(r"/downloads/[^\s\"')\]]+", text or "")):
        item: dict[str, Any] = {"path": path}
        if base is not None:
            f = Path(base) / os.path.basename(path)
            if f.exists():
                blob = f.read_bytes()
                item["bytes"] = len(blob)
                item["sha256"] = hashlib.sha256(blob).hexdigest()
        out.append(item)
    return out


SINK = AuditSink()
