"""MiddlewareRegistry — catalog ที่ parameterize ได้ แทนการให้ user เขียน hook เอง.

จากเอกสาร: middleware ทำเป็นฟอร์มตรง ๆ ได้แค่ ~30% เพราะ hook เป็นโค้ด
ทางออกคือ dev เตรียม middleware ที่ครอบเคสจริงไว้ แล้วเปิดให้ปรับแค่ "ค่า"
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

log = logging.getLogger("agent.middleware")

_CATALOG: dict[str, dict[str, Any]] = {}


def _tool_name(request: Any) -> str:
    """ToolCallRequest เก็บชื่อ tool ไว้ที่ tool_call["name"] ไม่มี attribute .name"""
    return (getattr(request, "tool_call", None) or {}).get("name", "?")


def register(ref: str, *, label: str, params_schema: dict[str, Any], note: str = ""):
    def deco(factory: Callable[..., Any]):
        _CATALOG[ref] = {"ref": ref, "label": label, "params_schema": params_schema,
                         "note": note, "factory": factory}
        return factory

    return deco


@register(
    "summarization",
    label="บีบอัดประวัติแชทเมื่อยาวเกิน",
    note="override built-in SummarizationMiddleware ด้วยการใช้ .name เดียวกัน",
    params_schema={
        "type": "object",
        "properties": {
            "trigger_tokens": {"type": "integer", "default": 100000, "minimum": 1000},
            "keep_messages": {"type": "integer", "default": 20, "minimum": 1},
        },
    },
)
def _summarization(*, agent_model: Any, trigger_tokens: int = 100_000, keep_messages: int = 20):
    # ใช้ model ตัวเดียวกับ agent เสมอ — จะได้ไม่ต้องตั้ง connection ซ้ำ
    from deepagents.middleware import SummarizationMiddleware

    return SummarizationMiddleware(
        model=agent_model,
        trigger=("tokens", trigger_tokens),
        keep=("messages", keep_messages),
    )


@register(
    "tool_call_log",
    label="Log ทุกครั้งที่เรียก tool",
    params_schema={
        "type": "object",
        "properties": {"include_args": {"type": "boolean", "default": False}},
    },
)
def _tool_call_log(*, agent_model: Any, include_args: bool = False):  # noqa: ARG001
    from langchain.agents.middleware import wrap_tool_call

    @wrap_tool_call(name="ToolCallLog")
    async def _mw(request, handler):  # type: ignore[no-untyped-def]
        name = _tool_name(request)
        if include_args:
            log.info("tool %s args=%s", name, (request.tool_call or {}).get("args"))
        else:
            log.info("tool %s", name)
        return await handler(request)

    return _mw


@register(
    "tool_denylist",
    label="บล็อก tool บางตัวไม่ให้ถูกเรียก",
    note="ใช้คู่กับ RBAC ตอนขึ้น Portal — คนแก้ prompt ได้ อาจไม่ควรปลด tool ได้",
    params_schema={
        "type": "object",
        "properties": {
            "blocked": {"type": "array", "items": {"type": "string"}, "default": []},
            "message": {"type": "string", "default": "tool นี้ถูกปิดโดยผู้ดูแลระบบ"},
        },
        "required": ["blocked"],
    },
)
def _tool_denylist(*, agent_model: Any, blocked: list[str], message: str = "tool นี้ถูกปิดโดยผู้ดูแลระบบ"):  # noqa: ARG001
    from langchain.agents.middleware import wrap_tool_call

    blocked_set = set(blocked)

    @wrap_tool_call(name="ToolDenylist")
    async def _mw(request, handler):  # type: ignore[no-untyped-def]
        if _tool_name(request) in blocked_set:
            from langchain_core.messages import ToolMessage

            return ToolMessage(content=message, name=_tool_name(request),
                               tool_call_id=(request.tool_call or {}).get("id", ""), status="error")
        return await handler(request)

    return _mw


@register(
    "pii_redaction",
    label="ปกปิด PII ในผลลัพธ์ของ tool",
    note="ตัวอย่างของ guardrail ที่ทำเป็นฟอร์มได้ — user เลือกแค่ว่าจะปิดอะไร",
    params_schema={
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {"type": "string", "enum": ["email", "phone_th", "thai_id"]},
                "default": ["email"],
            }
        },
    },
)
def _pii_redaction(*, agent_model: Any, entities: list[str] | None = None):  # noqa: ARG001
    from langchain.agents.middleware import wrap_tool_call

    patterns = {
        "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
        "phone_th": re.compile(r"\b0\d{1,2}[- ]?\d{3}[- ]?\d{4}\b"),
        "thai_id": re.compile(r"\b\d{1}[- ]?\d{4}[- ]?\d{5}[- ]?\d{2}[- ]?\d{1}\b"),
    }
    active = [patterns[e] for e in (entities or ["email"]) if e in patterns]

    @wrap_tool_call(name="PiiRedaction")
    async def _mw(request, handler):  # type: ignore[no-untyped-def]
        result = await handler(request)
        # handler คืน ToolMessage ไม่ใช่ str — ต้องแก้ที่ .content
        content = getattr(result, "content", None)
        if isinstance(content, str):
            for pat in active:
                content = pat.sub("[REDACTED]", content)
            result.content = content
        return result

    return _mw


def resolve(ref: str, params: dict[str, Any], *, agent_model: Any) -> Any:
    entry = _CATALOG.get(ref)
    if entry is None:
        raise RuntimeError(
            f"ไม่รู้จัก middleware '{ref}' — มีให้เลือก: {', '.join(sorted(_CATALOG))}"
        )
    return entry["factory"](agent_model=agent_model, **params)


def describe() -> list[dict[str, Any]]:
    return [
        {"ref": e["ref"], "label": e["label"], "note": e["note"],
         "params_schema": e["params_schema"]}
        for e in sorted(_CATALOG.values(), key=lambda x: x["ref"])
    ]
