"""Mock chat model สำหรับ demo — ไม่เรียก LLM จริง ไม่ต้องมี API key.

ใช้ผ่าน Connection ชนิด `mock` เท่านั้น จุดประสงค์คือให้เดินครบทั้ง flow ของ UI ได้
(stream ข้อความ, เรียก tool, หยุดรออนุมัติ) ตอนที่ยังไม่มี key หรือเน็ตมีปัญหา

พฤติกรรมเป็นกฎตายตัว ไม่ได้ "คิด":
  - ข้อความผู้ใช้ตรงกับ tool ตัวไหน (ชื่อ tool, รหัสแบบ cust-8842, ตัวเลข) -> เรียก tool นั้น
  - เพิ่งได้ผลจาก tool -> สรุปผลนั้นกลับ
  - นอกนั้น -> ตอบโดยยกบรรทัดแรกของ instructions มาให้เห็นว่า config มีผลจริง
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

MOCK_NOTE = "(โหมด demo: คำตอบนี้มาจาก mock model ไม่ใช่ LLM จริง)"

# tool ที่ deepagents ใส่ให้เอง mock ไม่เรียก จะได้เห็นแต่ tool ที่ผู้ใช้ประกาศใน spec
FRAMEWORK_TOOLS = {
    "write_todos", "ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute", "task",
}

# คำไทยที่คนมักพิมพ์ -> ส่วนของชื่อ tool
ALIASES = {
    "เวลา": "time", "กี่โมง": "time", "ราคา": "price", "เสนอราคา": "quote", "รายงาน": "report",
    "pdf": "pdf", "ค้นหา": "search", "หาข้อมูล": "search", "คำนวณ": "calculator", "ลูกค้า": "crm",
}

ID_TOKEN = re.compile(r"\b[a-zA-Z]{2,}-\d+\b")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
EXPRESSION = re.compile(r"\d[\d\s.+\-*/()]*[+\-*/][\d\s.+\-*/()]*\d")


def _text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content)


def _pick_tool(text: str, tools: list[dict[str, Any]]) -> dict[str, Any] | None:
    lowered = text.lower()
    wanted = {part for word, part in ALIASES.items() if word in lowered}
    best, best_score = None, 0
    for tool in tools:
        fn = tool["function"]
        props = (fn.get("parameters") or {}).get("properties") or {}
        parts = [p for p in fn["name"].lower().split("_") if len(p) > 2]
        score = sum(2 for p in parts if p in lowered or p in wanted)
        if ID_TOKEN.search(text) and any("id" in name.lower() for name in props):
            score += 3
        if EXPRESSION.search(text) and any(name in ("expression", "expr") for name in props):
            score += 3
        if score > best_score:
            best, best_score = fn, score
    return best


def _fill_args(text: str, fn: dict[str, Any]) -> dict[str, Any]:
    params = fn.get("parameters") or {}
    props = params.get("properties") or {}
    required = set(params.get("required") or [])
    numbers = NUMBER.findall(ID_TOKEN.sub("", text))
    args: dict[str, Any] = {}
    for name, schema in props.items():
        if name not in required and "id" not in name.lower():
            continue
        kind = schema.get("type")
        if kind in ("integer", "number"):
            raw = numbers.pop(0) if numbers else "1"
            args[name] = int(float(raw)) if kind == "integer" else float(raw)
        elif kind == "boolean":
            args[name] = False
        elif kind == "array":
            args[name] = [line.strip() for line in text.splitlines() if line.strip()] or [text]
        elif kind == "object":
            args[name] = {}
        else:
            found = ID_TOKEN.search(text) if "id" in name.lower() else None
            expr = EXPRESSION.search(text) if name in ("expression", "expr") else None
            args[name] = found.group(0) if found else expr.group(0).strip() if expr else text
    return args


class MockChatModel(BaseChatModel):
    """ตอบตามกฎตายตัว เพื่อ demo UI โดยไม่ต้องมี LLM."""

    model_name: str = "mock"
    bound_tools: list[dict[str, Any]] = []
    delay_s: float = 0.03

    @property
    def _llm_type(self) -> str:
        return "demo-mock"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "MockChatModel":  # noqa: ARG002
        return self.model_copy(update={"bound_tools": [convert_to_openai_tool(t) for t in tools]})

    def _decide(self, messages: list[BaseMessage]) -> AIMessage:
        last = messages[-1] if messages else None

        if isinstance(last, ToolMessage):
            results = []
            for m in reversed(messages):
                if not isinstance(m, ToolMessage):
                    break
                results.append(f"ผลจาก tool `{m.name or 'tool'}`:\n\n```\n{_text(m)[:1200]}\n```")
            return AIMessage(content="\n\n".join(reversed(results)) + f"\n\n{MOCK_NOTE}")

        text = _text(last) if isinstance(last, HumanMessage) else ""
        user_tools = [t for t in self.bound_tools if t["function"]["name"] not in FRAMEWORK_TOOLS]
        fn = _pick_tool(text, user_tools)
        if fn is not None:
            call = {"name": fn["name"], "args": _fill_args(text, fn),
                    "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
            return AIMessage(content="", tool_calls=[call])

        system = next((_text(m) for m in messages if isinstance(m, SystemMessage)), "")
        first_rule = next((line.strip() for line in system.splitlines() if line.strip()), "")
        names = ", ".join(t["function"]["name"] for t in user_tools) or "ไม่มี"
        reply = f"ได้รับข้อความ: \"{text[:200]}\"\n\n"
        if first_rule:
            reply += f"instructions บรรทัดแรกของ agent นี้คือ: {first_rule[:200]}\n\n"
        reply += f"tool ที่เรียกได้: {names}\n\n{MOCK_NOTE}"
        return AIMessage(content=reply)

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:  # noqa: ARG002
        return ChatResult(generations=[ChatGeneration(message=self._decide(messages))])

    def _stream(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None,
                **kwargs: Any) -> Iterator[ChatGenerationChunk]:  # noqa: ARG002
        decided = self._decide(messages)
        if decided.tool_calls:
            call = decided.tool_calls[0]
            chunk = AIMessageChunk(content="", tool_call_chunks=[{
                "name": call["name"], "args": json.dumps(call["args"], ensure_ascii=False),
                "id": call["id"], "index": 0, "type": "tool_call_chunk",
            }])
            yield ChatGenerationChunk(message=chunk)
            return
        for piece in re.findall(r"\S+\s*", _text(decided)):
            time.sleep(self.delay_s)
            if run_manager is not None:
                run_manager.on_llm_new_token(piece)
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece))
