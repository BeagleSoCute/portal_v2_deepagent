"""AgentSpec — สัญญาระหว่าง 'ฟอร์ม' กับ 'agent จริง'.

หลักการเดียวที่ยึด:
  - อะไรที่เป็น "ข้อมูล"  -> อยู่ในไฟล์นี้ (Tier A) user แก้ได้จากฟอร์ม
  - อะไรที่เป็น "ฟังก์ชัน" -> ไม่อยู่ในนี้ มีแค่ `ref` ชี้ไป registry ฝั่งโค้ด
  - อะไรที่เป็น infra/security (Tier B) -> ไม่โผล่ในนี้เลย ระบบคุมเอง
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SLUG = r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$"
Slug = Annotated[str, Field(pattern=SLUG)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- model
class ModelSpec(Strict):
    """ผู้ใช้เลือก Connection จาก UI แล้วพิมพ์ชื่อ model

    `connection` = ชื่อหรือ id ของ Connection ที่ตั้งไว้ในหน้า UI
                   (base_url + api key อยู่ในนั้น ไม่เคยอยู่ในไฟล์นี้)
    `connection` = None แปลว่าใช้ env แบบเดิม ตอนนั้น `ref` ต้องเป็น "provider:model"
    """

    connection: str | None = Field(
        default=None,
        description="ชื่อ/id ของ Connection ที่ตั้งจาก UI · None = อ่านจาก environment",
    )
    ref: str = Field(
        default="anthropic:claude-sonnet-4-6",
        description='ชื่อ model · ถ้ามี connection ใส่ชื่อล้วน เช่น "gpt-4o" · ถ้าไม่มี ใส่ "provider:model"',
    )
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)

    @model_validator(mode="after")
    def _check_ref_shape(self) -> "ModelSpec":
        if self.connection is None and (":" not in self.ref or self.ref.startswith(":") or self.ref.endswith(":")):
            raise ValueError(
                'ไม่ได้เลือก connection -> model.ref ต้องอยู่ในรูป "provider:model" '
                'เช่น "anthropic:claude-sonnet-4-6" (หรือเลือก connection ในหน้า UI ก่อน)'
            )
        return self


# --------------------------------------------------------------------------- knowledge
class SkillSpec(Strict):
    """Playbook แบบ markdown ที่ agent โหลดเมื่อเกี่ยวข้อง — ข้อความล้วน ทำฟอร์มได้ 100%."""

    name: Slug
    description: str = Field(min_length=1, max_length=500, description="ใช้ตัดสินใจว่าจะโหลด skill นี้เมื่อไหร่")
    body: str = Field(min_length=1, description="เนื้อ SKILL.md")


class MemoryDoc(Strict):
    name: str = Field(default="AGENTS.md", pattern=r"^[\w.\-]{1,64}$")
    body: str = Field(min_length=1)


# --------------------------------------------------------------------------- tools
class BuiltinTool(Strict):
    kind: Literal["builtin"] = "builtin"
    ref: Slug = Field(description="ชื่อใน ToolRegistry ฝั่งโค้ด")
    params: dict[str, Any] = Field(default_factory=dict)


class HttpAuth(Strict):
    type: Literal["none", "bearer", "header"] = "none"
    secret_ref: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$",
                                   description="ชื่อ env var เท่านั้น ห้ามใส่ค่าจริง")
    header_name: str | None = Field(default=None, max_length=64)


class HttpToolSpec(Strict):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url_template: str = Field(description="ใส่ {param} ได้ เช่น https://api.internal/crm/{id}")
    auth: HttpAuth = Field(default_factory=HttpAuth)
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        description="JSON Schema ของ argument ที่ agent ต้องส่งมา",
    )
    query_params: list[str] = Field(default_factory=list, description="ชื่อ arg ที่ให้ยัดเป็น query string")
    body_params: list[str] = Field(default_factory=list, description="ชื่อ arg ที่ให้ยัดเป็น JSON body")
    timeout_s: float = Field(default=15.0, gt=0, le=120)


class HttpTool(Strict):
    kind: Literal["http"] = "http"
    ref: Slug
    description: str = Field(min_length=1, max_length=1000, description="agent อ่านอันนี้เพื่อตัดสินใจเรียก")
    spec: HttpToolSpec


class PythonTool(Strict):
    """โค้ด Python ที่ผู้ใช้วางมาเองจาก UI.

    เก็บ "โค้ด" ไว้ใน spec ตรง ๆ ต่างจาก tool ชนิดอื่นที่เก็บแค่ ref
    ข้อดีที่ได้มาฟรีคือโค้ดถูก version ตาม spec -> ย้อนดูได้ว่าใครแก้ tool ตอนไหน
    และ fingerprint() ครอบโค้ดด้วย -> แก้โค้ดแล้ว cache ตกเอง

    ข้อแลกเปลี่ยน: นี่คือ escape hatch ที่ข้ามหลัก "ฟอร์มไม่รับโค้ด" โดยตั้งใจ
    ต้องเปิดด้วย ALLOW_PYTHON_TOOLS=1 และรันใน subprocess ที่ไม่มี secret เสมอ
    ดู app/registry/python_tools.py
    """

    kind: Literal["python"] = "python"
    ref: Slug
    description: str = Field(
        default="", max_length=1000,
        description="เว้นว่างได้ ระบบจะใช้ docstring ของ run() แทน",
    )
    code: str = Field(min_length=1, max_length=100_000, description="ต้องมี def run(...) พร้อม docstring")
    input_schema: dict[str, Any] | None = Field(
        default=None, description="เว้นว่าง = อนุมานจาก type hint ของ run() ให้อัตโนมัติ",
    )
    timeout_s: float = Field(default=15.0, gt=0, le=120)


class McpTool(Strict):
    kind: Literal["mcp"] = "mcp"
    server: Slug
    include_tools: list[str] = Field(default_factory=list)
    exclude_tools: list[str] = Field(default_factory=list)


ToolEntry = Annotated[BuiltinTool | HttpTool | PythonTool | McpTool, Field(discriminator="kind")]


class McpServer(Strict):
    transport: Literal["http", "sse"] = "http"
    url: str
    headers: dict[str, str] = Field(
        default_factory=dict,
        description='ค่าที่ขึ้นต้นด้วย "secret:" จะถูกแทนด้วย env var ตอน runtime',
    )
    default_tool_timeout: float | None = Field(default=None, gt=0, le=300)


# --------------------------------------------------------------------------- subagents
class SubAgentSpec(Strict):
    name: Slug
    description: str = Field(min_length=1, max_length=1000, description="parent ใช้ตัดสินใจว่าจะโยนงานให้ตัวไหน")
    system_prompt: str | None = None
    model: str | None = Field(default=None, description="เว้นว่าง = ใช้ model ของ parent")
    connection: str | None = Field(
        default=None, description="เว้นว่าง = ใช้ connection เดียวกับ parent"
    )
    tools: list[str] = Field(default_factory=list, description="ชื่อ tool ที่ประกาศไว้ใน spec.tools เท่านั้น")
    mode: Literal["isolated", "fork"] = "isolated"


# --------------------------------------------------------------------------- middleware / hitl / limits
class MiddlewareEntry(Strict):
    ref: Slug = Field(description="ชื่อใน MiddlewareRegistry — เลือกจาก catalog ไม่ใช่เขียนโค้ด")
    params: dict[str, Any] = Field(default_factory=dict)


class HitlSpec(Strict):
    interrupt_on: dict[str, bool] = Field(
        default_factory=dict, description="ชื่อ tool -> true คือหยุดรอคนอนุมัติก่อนเรียก"
    )


class LimitsSpec(Strict):
    """ไม่มีใน signature ของ deepagents — เราบังคับเองที่ชั้น factory/runtime."""

    max_steps: int = Field(default=50, ge=1, le=500)
    run_timeout_s: int = Field(default=600, ge=5, le=3600)
    max_tokens_per_run: int = Field(default=300_000, ge=1000)


# --------------------------------------------------------------------------- root
class AgentSpec(Strict):
    schema_version: Literal[1] = 1
    id: str | None = None
    version: int = Field(default=1, ge=1)
    name: Slug
    description: str = ""

    model: ModelSpec = Field(default_factory=ModelSpec)
    instructions: str = Field(default="", description="system prompt")
    skills: list[SkillSpec] = Field(default_factory=list)
    memory: list[MemoryDoc] = Field(default_factory=list)

    tools: list[ToolEntry] = Field(default_factory=list)
    mcp_servers: dict[str, McpServer] = Field(default_factory=dict)
    subagents: list[SubAgentSpec] = Field(default_factory=list)
    middleware: list[MiddlewareEntry] = Field(default_factory=list)

    hitl: HitlSpec = Field(default_factory=HitlSpec)
    response_format: dict[str, Any] | None = None
    limits: LimitsSpec = Field(default_factory=LimitsSpec)

    # Tier B: enum ที่ระบบคุม ไม่ใช่ path อิสระ
    backend_preset: Literal["state", "store"] = "state"

    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # -- cross-field validation ------------------------------------------------
    def tool_names(self) -> list[str]:
        out: list[str] = []
        for t in self.tools:
            if isinstance(t, McpTool):
                out.extend(t.include_tools)
            else:
                out.append(t.ref)
        return out

    def validate_refs(self) -> list[str]:
        """คืน list ของปัญหา — ให้ UI โชว์ได้ทีเดียวหมด ไม่ใช่ throw ทีละอัน."""
        problems: list[str] = []

        names = [t.server if isinstance(t, McpTool) else t.ref for t in self.tools]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            problems.append(f"tool ชื่อซ้ำ: {', '.join(sorted(dupes))}")

        declared = set(self.tool_names())
        for sa in self.subagents:
            missing = [t for t in sa.tools if t not in declared]
            if missing:
                problems.append(f"subagent '{sa.name}' อ้าง tool ที่ไม่ได้ประกาศ: {', '.join(missing)}")

        for tool_name in self.hitl.interrupt_on:
            if tool_name not in declared:
                problems.append(f"hitl.interrupt_on อ้าง tool ที่ไม่ได้ประกาศ: {tool_name}")

        for t in self.tools:
            if isinstance(t, McpTool) and t.server not in self.mcp_servers:
                problems.append(f"tool อ้าง mcp server '{t.server}' ที่ไม่ได้ประกาศใน mcp_servers")

        sa_names = [s.name for s in self.subagents]
        sa_dupes = {n for n in sa_names if sa_names.count(n) > 1}
        if sa_dupes:
            problems.append(f"subagent ชื่อซ้ำ: {', '.join(sorted(sa_dupes))}")

        if not self.instructions.strip() and not self.skills:
            problems.append("ยังไม่มีทั้ง instructions และ skills — agent จะไม่มีอะไรบอกว่าต้องทำอะไร")

        return problems

    # -- identity for caching --------------------------------------------------
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"updated_at", "id", "version"})
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def parse_spec(data: dict[str, Any]) -> AgentSpec:
    return AgentSpec.model_validate(data)
