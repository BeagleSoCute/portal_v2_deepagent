#!/usr/bin/env python3
"""รัน agent จากไฟล์ JSON ตรง ๆ — ใช้พิสูจน์ P1 โดยไม่ต้องมี API/UI.

    uv run python cli.py specs/researcher.json "สรุปเรื่อง X ให้หน่อย"
    uv run python cli.py --validate specs/*.json
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.factory import get_agent  # noqa: E402
from app.spec import AgentSpec  # noqa: E402


def load(path: str) -> AgentSpec:
    return AgentSpec.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


async def run(spec_path: str, message: str) -> int:
    spec = load(spec_path)
    built = get_agent(spec)
    print(f"agent={spec.name} v{spec.version} model={spec.model.ref}")
    print(f"tools={built.tool_names}")
    print(f"fingerprint={spec.fingerprint()}\n{'-' * 60}")

    config = {**built.config, "configurable": {"thread_id": "cli"}}
    async for mode, chunk in built.agent.astream(
        {"messages": [{"role": "user", "content": message}]},
        config=config,
        stream_mode=["updates", "messages"],
    ):
        if mode == "messages":
            msg, _ = chunk
            text = getattr(msg, "content", "")
            if isinstance(text, list):
                text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
            if text:
                print(text, end="", flush=True)
        elif mode == "updates":
            for node, update in (chunk or {}).items():
                for m in (update or {}).get("messages", []) if isinstance(update, dict) else []:
                    for tc in getattr(m, "tool_calls", None) or []:
                        print(f"\n  [tool] {tc.get('name')} {tc.get('args')}", flush=True)
    print()
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2

    if args[0] == "--validate":
        bad = 0
        for p in args[1:]:
            try:
                spec = load(p)
                problems = spec.validate_refs()
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {p}: {exc}")
                bad += 1
                continue
            if problems:
                bad += 1
                print(f"FAIL {p}")
                for pr in problems:
                    print(f"   - {pr}")
            else:
                print(f"OK   {p}  ({spec.name}, fingerprint={spec.fingerprint()})")
        return 1 if bad else 0

    if len(args) < 2:
        print(__doc__)
        return 2
    return asyncio.run(run(args[0], " ".join(args[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
