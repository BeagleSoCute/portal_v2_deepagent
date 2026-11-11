"""Connection — ที่อยู่ของ LLM ที่ user ตั้งเองได้จาก UI.

แยกออกจาก AgentSpec โดยตั้งใจ:
  - AgentSpec เก็บได้แค่ "ชื่อ connection" ไม่เคยเก็บ api key
  - api key ถูกเข้ารหัสก่อนลงดิสก์ และไม่เคยถูกส่งกลับไปที่ browser
  - export spec ไปเครื่องอื่นได้โดยไม่พา key ติดไปด้วย

kind บอกว่าจะประกอบ chat model ยังไง:
  openai_compatible  -> ChatOpenAI(base_url=..., api_key=...)   ใช้กับ gateway ส่วนใหญ่
  anthropic          -> ChatAnthropic(base_url=..., api_key=...)
  google_genai       -> ChatGoogleGenerativeAI(api_key=...)
  env                -> init_chat_model() อ่าน env เองแบบเดิม (ไว้เทียบ/fallback)
  mock               -> MockChatModel ตอบตามกฎตายตัว ใช้ demo UI ตอนไม่มี key (ดู mock_llm.py)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import crypto

Kind = Literal["openai_compatible", "anthropic", "google_genai", "env", "mock"]

KIND_LABELS: dict[str, str] = {
    "openai_compatible": "OpenAI-compatible (มี base_url)",
    "anthropic": "Anthropic",
    "google_genai": "Google Gemini",
    "env": "อ่านจาก environment (ไม่ต้องกรอก key)",
    "mock": "Demo: mock model (ไม่เรียก LLM จริง ไม่ต้องมี key)",
}


class Connection(BaseModel):
    """รูปที่เก็บลงดิสก์ — api_key อยู่ในรูป ciphertext เท่านั้น"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=64, pattern=r"^[\w .\-]+$")
    kind: Kind = "openai_compatible"
    base_url: str | None = None
    default_model: str = ""
    extra_headers: dict[str, str] = Field(default_factory=dict)
    api_key_enc: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # -- view สำหรับส่งกลับ browser: ไม่มี key จริง มีแค่ mask --------------
    def public(self) -> dict[str, Any]:
        key = ""
        if self.api_key_enc:
            try:
                key = crypto.decrypt(self.api_key_enc)
            except Exception:  # noqa: BLE001
                key = ""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "kind_label": KIND_LABELS.get(self.kind, self.kind),
            "base_url": self.base_url,
            "default_model": self.default_model,
            "extra_headers": self.extra_headers,
            "has_key": bool(self.api_key_enc),
            "api_key_masked": crypto.mask(key),
            "updated_at": self.updated_at,
        }

    def api_key(self) -> str:
        return crypto.decrypt(self.api_key_enc) if self.api_key_enc else ""


class ConnectionInput(BaseModel):
    """รูปที่รับจากฟอร์ม — api_key เป็น write-only เว้นว่าง = ไม่แตะของเดิม"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[\w .\-]+$")
    kind: Kind = "openai_compatible"
    base_url: str | None = None
    default_model: str = ""
    extra_headers: dict[str, str] = Field(default_factory=dict)
    api_key: str | None = None


class ConnectionStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.getenv("DATA_DIR", "./data")).resolve() / "connections"
        self.root.mkdir(parents=True, exist_ok=True)

    def _file(self, cid: str) -> Path:
        return self.root / f"{cid}.json"

    def list(self) -> list[Connection]:
        out = []
        for f in sorted(self.root.glob("*.json")):
            try:
                out.append(Connection.model_validate(json.loads(f.read_text(encoding="utf-8"))))
            except Exception:  # noqa: BLE001
                continue
        return out

    def get(self, ref: str) -> Connection:
        """หาได้ทั้งด้วย id และด้วยชื่อ — spec ที่ export ไปเครื่องอื่นจะอ่านง่ายกว่าถ้าใช้ชื่อ"""
        f = self._file(ref)
        if f.exists():
            return Connection.model_validate(json.loads(f.read_text(encoding="utf-8")))
        for c in self.list():
            if c.name == ref:
                return c
        raise FileNotFoundError(f"ไม่พบ connection '{ref}'")

    def create(self, data: ConnectionInput) -> Connection:
        if any(c.name == data.name for c in self.list()):
            raise ValueError(f"มี connection ชื่อ '{data.name}' อยู่แล้ว")
        conn = Connection(
            id=uuid.uuid4().hex[:12],
            name=data.name,
            kind=data.kind,
            base_url=(data.base_url or None),
            default_model=data.default_model,
            extra_headers=data.extra_headers,
            api_key_enc=crypto.encrypt(data.api_key) if data.api_key else "",
        )
        self._write(conn)
        return conn

    def update(self, cid: str, data: ConnectionInput) -> Connection:
        conn = self.get(cid)
        clash = [c for c in self.list() if c.name == data.name and c.id != conn.id]
        if clash:
            raise ValueError(f"มี connection ชื่อ '{data.name}' อยู่แล้ว")
        conn.name = data.name
        conn.kind = data.kind
        conn.base_url = data.base_url or None
        conn.default_model = data.default_model
        conn.extra_headers = data.extra_headers
        if data.api_key:  # เว้นว่าง (None หรือ "") = คงของเดิม จะได้แก้ base_url โดยไม่ต้องพิมพ์ key ใหม่
            conn.api_key_enc = crypto.encrypt(data.api_key)
        conn.updated_at = datetime.now(timezone.utc).isoformat()
        self._write(conn)
        return conn

    def delete(self, cid: str) -> None:
        self._file(self.get(cid).id).unlink(missing_ok=True)

    def _write(self, conn: Connection) -> None:
        p = self._file(conn.id)
        p.write_text(json.dumps(conn.model_dump(mode="json"), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        try:
            p.chmod(0o600)
        except OSError:
            pass


# --------------------------------------------------------------------------- resolve

def _allowed_base_hosts() -> set[str]:
    """Tier B: นโยบายของ deployment ไม่ใช่ของ user — ว่าง = ไม่จำกัด (โหมด POC)"""
    raw = os.getenv("LLM_BASE_URL_ALLOWED_HOSTS", "").strip()
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def build_chat_model(
    conn: Connection,
    model_name: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> Any:
    """Connection + ชื่อ model -> chat model instance."""
    from urllib.parse import urlparse

    if conn.kind == "mock":
        from .mock_llm import MockChatModel

        return MockChatModel(model_name=model_name or conn.default_model or "mock")

    allowed = _allowed_base_hosts()
    if allowed and conn.base_url:
        host = (urlparse(conn.base_url).hostname or "").lower()
        if host not in allowed:
            raise RuntimeError(
                f"base_url host '{host}' ไม่อยู่ใน LLM_BASE_URL_ALLOWED_HOSTS ของ deployment นี้"
            )

    name = model_name or conn.default_model
    if not name:
        raise RuntimeError(f"connection '{conn.name}' ยังไม่ได้ระบุชื่อ model")

    kwargs: dict[str, Any] = {}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    if conn.kind == "env":
        from langchain.chat_models import init_chat_model

        return init_chat_model(name, **kwargs)

    key = conn.api_key()
    if not key:
        raise RuntimeError(f"connection '{conn.name}' ยังไม่ได้ใส่ API key")

    if conn.kind == "openai_compatible":
        from langchain_openai import ChatOpenAI

        if conn.base_url:
            kwargs["base_url"] = conn.base_url
        if conn.extra_headers:
            kwargs["default_headers"] = conn.extra_headers
        return ChatOpenAI(model=name, api_key=key, **kwargs)

    if conn.kind == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if conn.base_url:
            kwargs["base_url"] = conn.base_url
        if conn.extra_headers:
            kwargs["default_headers"] = conn.extra_headers
        if "max_tokens" not in kwargs:
            kwargs["max_tokens"] = 4096  # ChatAnthropic บังคับต้องมี
        return ChatAnthropic(model=name, api_key=key, **kwargs)

    if conn.kind == "google_genai":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=name, google_api_key=key, **kwargs)

    raise RuntimeError(f"ไม่รู้จัก connection kind '{conn.kind}'")


def test_connection(conn: Connection, model_name: str = "") -> dict[str, Any]:
    """ยิงจริงหนึ่งครั้งสั้น ๆ เพื่อบอก user ตรง ๆ ว่า base_url + key ใช้ได้ไหม"""
    import time

    started = time.monotonic()
    try:
        llm = build_chat_model(conn, model_name or conn.default_model, max_tokens=8)
        resp = llm.invoke([{"role": "user", "content": "ping"}])
        text = getattr(resp, "content", "")
        if isinstance(text, list):
            text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
        return {
            "ok": True,
            "model": model_name or conn.default_model,
            "elapsed_s": round(time.monotonic() - started, 2),
            "reply_preview": str(text)[:120],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "model": model_name or conn.default_model,
            "elapsed_s": round(time.monotonic() - started, 2),
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }
