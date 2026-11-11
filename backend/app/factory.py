"""Agent Factory — จุดเดียวในระบบที่แปลง "JSON config" เป็น "agent จริง".

ทุกอย่างที่เป็น Tier B (backend, checkpointer, state schema, ลำดับ middleware)
ถูกตัดสินใจที่นี่ ไม่ใช่ใน spec ของ user

cache key = fingerprint(spec) + version -> แก้ config แล้ว cache ตกเอง
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .connections import ConnectionStore, build_chat_model
from .registry import middleware as mw_registry
from .registry import python_tools
from .registry import tools as tool_registry
from .spec import AgentSpec, BuiltinTool, HttpTool, McpTool, PythonTool

log = logging.getLogger("agent.factory")

_CACHE: "OrderedDict[str, BuiltAgent]" = OrderedDict()
_CACHE_MAX = 32


class BuiltAgent:
    def __init__(self, agent: Any, spec: AgentSpec, workdir: Path, tool_names: list[str]):
        self.agent = agent
        self.spec = spec
        self.workdir = workdir
        self.tool_names = tool_names

    @property
    def config(self) -> dict[str, Any]:
        """config ที่ต้องส่งตอน invoke — บังคับ limits ที่ spec ประกาศไว้."""
        return {"recursion_limit": self.spec.limits.max_steps}

    def dispose(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


# --------------------------------------------------------------------------- materialize

def _materialize(spec: AgentSpec) -> tuple[Path, list[str], list[str]]:
    """skills/memory ใน spec เก็บเป็น "ข้อความ" แต่ deepagents รับเป็น "path"
    เลยต้องเขียนลงดิสก์ชั่วคราวก่อน แล้วส่ง path เข้าไป"""
    root = Path(tempfile.mkdtemp(prefix=f"agent-{spec.name}-"))

    skill_paths: list[str] = []
    if spec.skills:
        sdir = root / "skills"
        sdir.mkdir(parents=True, exist_ok=True)
        for sk in spec.skills:
            d = sdir / sk.name
            d.mkdir(parents=True, exist_ok=True)
            fm = f"---\nname: {sk.name}\ndescription: {sk.description}\n---\n\n"
            (d / "SKILL.md").write_text(fm + sk.body, encoding="utf-8")
        skill_paths.append(str(sdir))

    memory_paths: list[str] = []
    for doc in spec.memory:
        p = root / doc.name
        p.write_text(doc.body, encoding="utf-8")
        memory_paths.append(str(p))

    return root, skill_paths, memory_paths


# --------------------------------------------------------------------------- tools

def _build_tools(spec: AgentSpec) -> tuple[list[Any], dict[str, Any]]:
    built: list[Any] = []
    by_name: dict[str, Any] = {}

    mcp_entries = [t for t in spec.tools if isinstance(t, McpTool)]

    for entry in spec.tools:
        if isinstance(entry, BuiltinTool):
            t = tool_registry.resolve_builtin(entry.ref, entry.params)
            built.append(t)
            by_name[entry.ref] = t
        elif isinstance(entry, HttpTool):
            t = tool_registry.build_http_tool(entry.ref, entry.description, entry.spec)
            built.append(t)
            by_name[entry.ref] = t
        elif isinstance(entry, PythonTool):
            t = python_tools.build_python_tool(
                entry.ref, entry.description, entry.code,
                timeout_s=entry.timeout_s, input_schema=entry.input_schema,
            )
            built.append(t)
            by_name[entry.ref] = t

    if mcp_entries:
        for t in tool_registry.build_mcp_tools(spec.mcp_servers, mcp_entries):
            built.append(t)
            by_name[getattr(t, "name", str(t))] = t

    return built, by_name


# --------------------------------------------------------------------------- build

def _resolve_model(
    connection: str | None,
    ref: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    connections: ConnectionStore | None = None,
) -> Any:
    """connection + ชื่อ model -> chat model instance.

    ไม่มี connection = โหมดเดิม อ่าน key จาก environment
    """
    if connection:
        store = connections or ConnectionStore()
        try:
            conn = store.get(connection)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"spec อ้าง connection '{connection}' ที่ยังไม่ได้สร้าง — ไปสร้างในหน้า Connections ก่อน"
            ) from exc
        return build_chat_model(conn, ref, temperature=temperature, max_tokens=max_tokens)

    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return init_chat_model(ref, **kwargs)


def build_agent(spec: AgentSpec, *, checkpointer: Any | None = None) -> BuiltAgent:
    problems = spec.validate_refs()
    if problems:
        raise ValueError("spec ไม่ผ่าน validation:\n- " + "\n- ".join(problems))

    from deepagents import create_deep_agent

    workdir, skill_paths, memory_paths = _materialize(spec)

    try:
        tools, by_name = _build_tools(spec)

        connections = ConnectionStore()
        model = _resolve_model(
            spec.model.connection,
            spec.model.ref,
            temperature=spec.model.temperature,
            max_tokens=spec.model.max_tokens,
            connections=connections,
        )

        subagents: list[dict[str, Any]] = []
        for sa in spec.subagents:
            item: dict[str, Any] = {"name": sa.name, "description": sa.description, "mode": sa.mode}
            if sa.system_prompt:
                item["system_prompt"] = sa.system_prompt
            if sa.model or sa.connection:
                # subagent ที่ไม่ระบุ connection ให้ใช้ของ parent — ผู้ใช้จะได้ไม่ต้องตั้งซ้ำ
                item["model"] = _resolve_model(
                    sa.connection or spec.model.connection,
                    sa.model or spec.model.ref,
                    connections=connections,
                )
            if sa.tools:
                item["tools"] = [by_name[n] for n in sa.tools if n in by_name]
            subagents.append(item)

        # ส่ง instance ไม่ใช่ string เพราะ middleware บางตัว (summarization)
        # ต้องเรียก LLM ตัวเดียวกับ agent ซึ่งตอนนี้ผูกกับ connection ที่ผู้ใช้ตั้งเอง
        extra_mw = [
            mw_registry.resolve(m.ref, m.params, agent_model=model)
            for m in spec.middleware
        ]

        kwargs: dict[str, Any] = {
            "model": model,
            "name": spec.name,
            "tools": tools,
            "middleware": extra_mw,
        }
        if spec.instructions.strip():
            kwargs["system_prompt"] = spec.instructions
        if skill_paths:
            kwargs["skills"] = skill_paths
        if memory_paths:
            kwargs["memory"] = memory_paths
        if subagents:
            kwargs["subagents"] = subagents
        if spec.hitl.interrupt_on:
            kwargs["interrupt_on"] = {k: v for k, v in spec.hitl.interrupt_on.items() if v}
        if spec.response_format:
            kwargs["response_format"] = spec.response_format
        if checkpointer is not None:
            kwargs["checkpointer"] = checkpointer

        # Tier B: backend เป็น preset ที่ระบบคุม ไม่ใช่ path อิสระของ user
        if spec.backend_preset == "store":
            try:
                from deepagents.backends import StoreBackend

                kwargs["backend"] = StoreBackend()
            except Exception:  # noqa: BLE001
                log.warning("StoreBackend ใช้ไม่ได้ ตกกลับไปใช้ default (state)")

        agent = create_deep_agent(**kwargs)
        return BuiltAgent(agent, spec, workdir, sorted(by_name))
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def cache_key(spec: AgentSpec) -> str:
    raw = f"{spec.id or spec.name}:{spec.version}:{spec.fingerprint()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def get_agent(spec: AgentSpec, *, checkpointer: Any | None = None) -> BuiltAgent:
    key = cache_key(spec)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit

    built = build_agent(spec, checkpointer=checkpointer)
    _CACHE[key] = built
    while len(_CACHE) > _CACHE_MAX:
        _, evicted = _CACHE.popitem(last=False)
        evicted.dispose()
    return built


def invalidate(spec_id: str | None = None) -> int:
    """ล้าง cache — เรียกตอน spec ถูกแก้ (ป้องกันเคส 'แก้แล้วไม่เปลี่ยน')."""
    if spec_id is None:
        n = len(_CACHE)
        for b in _CACHE.values():
            b.dispose()
        _CACHE.clear()
        return n
    doomed = [k for k, v in _CACHE.items() if v.spec.id == spec_id]
    for k in doomed:
        _CACHE.pop(k).dispose()
    return len(doomed)
