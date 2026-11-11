"""ToolRegistry — จุดเดียวที่ "ชื่อใน JSON" กลายเป็น "ฟังก์ชันจริง".

สามชนิด:
  builtin : ฟังก์ชันที่ dev ลงทะเบียนไว้ในโค้ด user แค่เลือก + ใส่ params
  http    : ประกาศ REST endpoint แบบ declarative -> gen เป็น tool ให้อัตโนมัติ
  mcp     : ดึงจาก remote MCP server

POC นี้ built-in เป็น mock ทั้งหมด (ตกลงกันไว้) จะได้เดโมได้โดยไม่ต้องต่อระบบจริง
เพิ่ม tool ใหม่ = เพิ่ม @builtin ตัวเดียวในไฟล์นี้ ฟอร์มฝั่ง UI จะมีตัวเลือกใหม่เอง
"""

from __future__ import annotations

import ast
import json
import operator
import os
import random
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from langchain_core.tools import StructuredTool, tool

# --------------------------------------------------------------------------- builtin

_BUILTINS: dict[str, dict[str, Any]] = {}


def builtin(ref: str, *, label: str, params_schema: dict[str, Any] | None = None):
    """ลงทะเบียน builtin tool. `params_schema` คือ config ที่ user ตั้งได้จากฟอร์ม
    (ไม่ใช่ argument ที่ agent ส่งมา)."""

    def deco(factory: Callable[..., Any]):
        _BUILTINS[ref] = {
            "ref": ref,
            "label": label,
            "params_schema": params_schema or {"type": "object", "properties": {}},
            "factory": factory,
            "doc": (factory.__doc__ or "").strip(),
        }
        return factory

    return deco


@builtin(
    "mock_web_search",
    label="Mock web search",
    params_schema={
        "type": "object",
        "properties": {"max_results": {"type": "integer", "default": 3, "minimum": 1, "maximum": 10}},
    },
)
def _mock_web_search(max_results: int = 3):
    """ค้นเว็บ (mock) — คืนผลลัพธ์สมมติที่หน้าตาเหมือนของจริง"""

    @tool("mock_web_search")
    def _t(query: str) -> str:
        """Search the web for a query and return a list of results with title, url and snippet."""
        rows = [
            {
                "title": f"{query} — overview ({i + 1})",
                "url": f"https://example.invalid/{query.replace(' ', '-').lower()}/{i + 1}",
                "snippet": f"[MOCK] ผลการค้นหาจำลองอันดับ {i + 1} สำหรับ '{query}'.",
            }
            for i in range(max_results)
        ]
        return json.dumps(rows, ensure_ascii=False, indent=2)

    return _t


@builtin(
    "mock_crm_lookup",
    label="Mock CRM lookup",
    params_schema={"type": "object", "properties": {}},
)
def _mock_crm_lookup():
    """ดึงข้อมูลลูกค้าจาก CRM (mock)"""

    @tool("mock_crm_lookup")
    def _t(customer_id: str) -> str:
        """Look up a customer record by id. Returns tier, open tickets and MRR."""
        rnd = random.Random(customer_id)
        rec = {
            "customer_id": customer_id,
            "name": f"[MOCK] Customer {customer_id.upper()}",
            "tier": rnd.choice(["free", "pro", "enterprise"]),
            "open_tickets": rnd.randint(0, 5),
            "mrr_thb": rnd.choice([0, 1500, 12000, 88000]),
            # ใส่ไว้ให้ middleware pii_redaction มีของให้ปิดจริง ตอนเดโม
            "contact_email": f"{customer_id.lower()}@example.invalid",
            "contact_phone": f"08{rnd.randint(10000000, 99999999)}",
            "note": "ข้อมูลจำลอง ไม่ใช่ของจริง",
        }
        return json.dumps(rec, ensure_ascii=False, indent=2)

    return _t


_SAFE_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("นิพจน์นี้ไม่อนุญาต — รับเฉพาะเลขและ + - * / % // **")


@builtin("calculator", label="Calculator", params_schema={"type": "object", "properties": {}})
def _calculator():
    """คำนวณเลขคณิตแบบปลอดภัย (ไม่ใช่ eval)"""

    @tool("calculator")
    def _t(expression: str) -> str:
        """Evaluate an arithmetic expression, e.g. '(1200 * 12) / 4'."""
        try:
            return str(_safe_eval(ast.parse(expression, mode="eval")))
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    return _t


@builtin("current_time", label="Current time", params_schema={"type": "object", "properties": {}})
def _current_time():
    """เวลาปัจจุบัน UTC"""

    @tool("current_time")
    def _t() -> str:
        """Return the current UTC time in ISO-8601 format."""
        return datetime.now(timezone.utc).isoformat()

    return _t


# --------------------------------------------------------------------------- http

def _allowed_hosts() -> set[str]:
    raw = os.getenv("HTTP_TOOL_ALLOWED_HOSTS", "").strip()
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def build_http_tool(ref: str, description: str, spec: Any) -> Any:
    """แปลง HttpToolSpec (ข้อมูลล้วน) -> StructuredTool.

    egress allowlist เป็น Tier B: user ตั้งไม่ได้จากฟอร์ม อยู่ใน env ของ deployment
    """
    import httpx

    host = (urlparse(spec.url_template).hostname or "").lower()
    allowed = _allowed_hosts()
    if not allowed or host not in allowed:
        raise RuntimeError(
            f"host '{host}' ไม่อยู่ใน HTTP_TOOL_ALLOWED_HOSTS — เพิ่มใน .env ก่อน (กัน SSRF)"
        )

    def _headers() -> dict[str, str]:
        h: dict[str, str] = {}
        auth = spec.auth
        if auth.type != "none":
            if not auth.secret_ref:
                raise RuntimeError(f"tool '{ref}': auth.type={auth.type} แต่ไม่ได้ระบุ secret_ref")
            value = os.getenv(auth.secret_ref)
            if not value:
                raise RuntimeError(f"tool '{ref}': ยังไม่ได้ตั้ง env '{auth.secret_ref}'")
            if auth.type == "bearer":
                h["Authorization"] = f"Bearer {value}"
            else:
                h[auth.header_name or "X-API-Key"] = value
        return h

    def _call(**kwargs: Any) -> str:
        url = spec.url_template
        path_args = {k: v for k, v in kwargs.items()
                     if "{" + k + "}" in spec.url_template}
        for k, v in path_args.items():
            url = url.replace("{" + k + "}", str(v))

        params = {k: kwargs[k] for k in spec.query_params if k in kwargs}
        body = {k: kwargs[k] for k in spec.body_params if k in kwargs}

        with httpx.Client(timeout=spec.timeout_s, follow_redirects=False) as client:
            resp = client.request(
                spec.method, url, params=params or None,
                json=body or None, headers=_headers(),
            )
        text = resp.text[:20_000]
        return f"HTTP {resp.status_code}\n{text}"

    return StructuredTool.from_function(
        func=_call,
        name=ref,
        description=description,
        args_schema=spec.input_schema,
    )


# --------------------------------------------------------------------------- mcp

def build_mcp_tools(servers: dict[str, Any], entries: list[Any]) -> list[Any]:
    """เชื่อม remote MCP server. ค่า header ที่ขึ้นต้น 'secret:' -> อ่านจาก env."""
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "ต้องติดตั้ง langchain-mcp-adapters ก่อนถึงจะใช้ tool ชนิด mcp ได้"
        ) from exc

    wanted = {e.server for e in entries}
    conf: dict[str, Any] = {}
    for name, srv in servers.items():
        if name not in wanted:
            continue
        headers = {}
        for k, v in srv.headers.items():
            headers[k] = os.getenv(v[7:], "") if v.startswith("secret:") else v
        conf[name] = {"transport": srv.transport, "url": srv.url, "headers": headers}

    client = MultiServerMCPClient(conf)
    all_tools = client.get_tools()  # sync facade; ถ้า API เป็น async ให้ย้ายไป factory async

    keep: list[Any] = []
    for e in entries:
        for t in all_tools:
            name = getattr(t, "name", "")
            if e.include_tools and name not in e.include_tools:
                continue
            if e.exclude_tools and name in e.exclude_tools:
                continue
            keep.append(t)
    return keep


# --------------------------------------------------------------------------- public

def resolve_builtin(ref: str, params: dict[str, Any]) -> Any:
    entry = _BUILTINS.get(ref)
    if entry is None:
        raise RuntimeError(f"ไม่รู้จัก builtin tool '{ref}' — มีให้เลือก: {', '.join(sorted(_BUILTINS))}")
    return entry["factory"](**params)


def describe() -> list[dict[str, Any]]:
    return [
        {"kind": "builtin", "ref": e["ref"], "label": e["label"],
         "doc": e["doc"], "params_schema": e["params_schema"]}
        for e in sorted(_BUILTINS.values(), key=lambda x: x["ref"])
    ]
