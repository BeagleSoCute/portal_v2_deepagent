"""ModelRegistry — เหลือหน้าที่แค่ "แนะนำชื่อ model" กับ "บอกสถานะ env".

ตั้งแต่ย้ายมาใช้ Connection ที่ผู้ใช้ตั้งเองจาก UI แล้ว
base_url + api key ไม่ได้อยู่ที่นี่อีกต่อไป (ดู app/connections.py)

โหมด env ยังเหลือไว้เป็นทางเลือกรอง: spec ที่ไม่ระบุ connection จะ fallback
มาอ่าน env แบบเดิม ซึ่งมีประโยชน์ตอนรัน CLI หรือ CI ที่ไม่อยากตั้ง connection
"""

from __future__ import annotations

import os
from typing import Any

PROVIDER_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "bedrock_converse": "AWS_ACCESS_KEY_ID",
    "groq": "GROQ_API_KEY",
    "ollama": "",  # local, ไม่ต้องมี key
}

SUGGESTIONS: list[dict[str, str]] = [
    {"ref": "anthropic:claude-sonnet-4-6", "label": "Claude Sonnet 4.6"},
    {"ref": "anthropic:claude-haiku-4-5", "label": "Claude Haiku 4.5"},
    {"ref": "openai:gpt-5.5", "label": "GPT-5.5"},
    {"ref": "google_genai:gemini-3.6-flash", "label": "Gemini 3.6 Flash"},
]


def provider_of(ref: str) -> str:
    return ref.split(":", 1)[0]


def missing_key_for(ref: str) -> str | None:
    """คืนชื่อ env var ที่ยังไม่ได้ตั้ง (None = พร้อมใช้)."""
    env = PROVIDER_ENV.get(provider_of(ref))
    if env is None:
        return None  # provider ที่เราไม่รู้จัก ปล่อยให้ langchain ว่าเอง
    if env == "":
        return None
    return None if os.getenv(env) else env


def resolve(ref: str, *, temperature: float | None = None, max_tokens: int | None = None) -> Any:
    """model string -> chat model instance."""
    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    missing = missing_key_for(ref)
    if missing:
        raise RuntimeError(
            f"ยังไม่ได้ตั้ง {missing} ใน .env — model '{ref}' เรียกไม่ได้"
        )
    return init_chat_model(ref, **kwargs)


def describe() -> dict[str, Any]:
    """ป้อนให้ /api/registry เพื่อให้ฟอร์ม generate ตัวเองได้."""
    return {
        "input": "freetext",
        "placeholder": "anthropic:claude-sonnet-4-6",
        "suggestions": SUGGESTIONS,
        "keys": [
            {"provider": p, "env": e, "present": bool(os.getenv(e)) if e else True}
            for p, e in PROVIDER_ENV.items()
            if e
        ],
    }
