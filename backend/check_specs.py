#!/usr/bin/env python3
"""ตรวจ spec แบบ static — ไม่ต้องติดตั้ง dependency อะไรเลย

ใช้ตอนที่ยัง uv sync ไม่ได้ หรือใน CI ที่อยากตรวจเร็ว ๆ ก่อนรันของจริง
ตรวจ 3 อย่าง: คีย์ที่ไม่มีใน model, ref ข้ามกัน (subagent/hitl -> tools),
และ ref ที่ชี้ไป registry แต่ registry ไม่มีของ

    python3 check_specs.py [specs/*.json]
"""
from __future__ import annotations

import ast
import glob
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent

# อ่าน .env เอง (ไม่พึ่ง python-dotenv เพื่อให้ไฟล์นี้รันได้โดยไม่ต้องลง dependency)
# ไม่อ่านแล้วจะไม่เห็น PYTHON_TOOL_ALLOWED_IMPORTS ที่ deployment ตั้งไว้
# แล้วฟ้อง import ผิด ๆ ว่าไม่อนุญาต ทั้งที่ของจริงอนุญาต
_ENV = HERE / ".env"
if _ENV.exists():
    for _line in _ENV.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
TOOLKIND = {"builtin": "BuiltinTool", "http": "HttpTool", "python": "PythonTool", "mcp": "McpTool"}
NESTED = {
    "model": "ModelSpec", "hitl": "HitlSpec", "limits": "LimitsSpec",
}
LISTS = {
    "skills": "SkillSpec", "memory": "MemoryDoc",
    "subagents": "SubAgentSpec", "middleware": "MiddlewareEntry",
}


def model_fields() -> dict[str, set[str]]:
    tree = ast.parse((HERE / "app" / "spec.py").read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out[node.name] = {
                s.target.id for s in node.body
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            }
    return out


def registered(path: Path, func: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        n.args[0].value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == func and n.args
    }


def main(paths: list[str]) -> int:
    fields = model_fields()
    tools_ok = registered(HERE / "app" / "registry" / "tools.py", "builtin")
    mw_ok = registered(HERE / "app" / "registry" / "middleware.py", "register")
    problems: list[str] = []

    for p in paths:
        d = json.loads(Path(p).read_text(encoding="utf-8"))

        def unknown(obj: dict, model: str, where: str) -> None:
            for k in obj:
                if k not in fields.get(model, set()):
                    problems.append(f"{p}{where}: '{k}' ไม่มีใน {model}")

        unknown(d, "AgentSpec", "")
        for key, model in NESTED.items():
            if isinstance(d.get(key), dict):
                unknown(d[key], model, f".{key}")
        for key, model in LISTS.items():
            for i, item in enumerate(d.get(key, [])):
                unknown(item, model, f".{key}[{i}]")

        declared: set[str] = set()
        for i, t in enumerate(d.get("tools", [])):
            kind = t.get("kind")
            if kind not in TOOLKIND:
                problems.append(f"{p}.tools[{i}]: kind '{kind}' ไม่รู้จัก")
                continue
            unknown(t, TOOLKIND[kind], f".tools[{i}]")
            declared.update(t.get("include_tools", []) if kind == "mcp" else [t.get("ref", "")])
            if kind == "builtin" and t.get("ref") not in tools_ok:
                problems.append(f"{p}: builtin '{t.get('ref')}' ไม่มีใน ToolRegistry")
            if kind == "python":
                sys.path.insert(0, str(HERE))
                from app.registry.python_tools import guard_code

                for pr in guard_code(t.get("code", "")):
                    problems.append(f"{p}: python tool '{t.get('ref')}' — {pr}")
            if kind == "mcp" and t.get("server") not in d.get("mcp_servers", {}):
                problems.append(f"{p}: อ้าง mcp server '{t.get('server')}' ที่ไม่ได้ประกาศ")

        for sa in d.get("subagents", []):
            for t in sa.get("tools", []):
                if t not in declared:
                    problems.append(f"{p}: subagent '{sa.get('name')}' อ้าง tool '{t}' ที่ไม่ได้ประกาศ")
        for t in d.get("hitl", {}).get("interrupt_on", {}):
            if t not in declared:
                problems.append(f"{p}: interrupt_on อ้าง tool '{t}' ที่ไม่ได้ประกาศ")
        for m in d.get("middleware", []):
            if m.get("ref") not in mw_ok:
                problems.append(f"{p}: middleware '{m.get('ref')}' ไม่มีใน MiddlewareRegistry")

        print(("bad  " if any(pr.startswith(p) for pr in problems) else "ok   ") + p)

    for pr in problems:
        print("  ✖ " + pr)
    print("\nRESULT:", "ผ่านหมด" if not problems else f"มีปัญหา {len(problems)} จุด")
    return 1 if problems else 0


if __name__ == "__main__":
    args = sys.argv[1:] or sorted(glob.glob(str(HERE / "specs" / "*.json")))
    raise SystemExit(main(args))
