"""Python tool ที่ผู้ใช้วางโค้ดมาเองจาก UI.

=== อ่านก่อนแก้ไฟล์นี้ ===

เอกสาร docs/01-analysis-and-plan.md เขียนไว้ว่า "ช่องพิมพ์โค้ดในฟอร์มคือ RCE"
ไฟล์นี้คือการข้ามข้อนั้นโดยตั้งใจ พร้อมเงื่อนไข:

  1. ปิดเป็นค่าเริ่มต้น ต้องตั้ง ALLOW_PYTHON_TOOLS=1 ถึงจะใช้ได้
  2. โค้ดรันใน subprocess เสมอ ไม่เคย exec() ใน process ของ API
  3. subprocess ได้ env ที่ตัดจนเหลือแต่ PATH -> ไม่เห็น api key, ไม่เห็น SECRET_KEY
  4. มี timeout จริง (kill process ได้ ต่างจาก thread ที่ kill ไม่ได้)
  5. AST guard กันพลาด เช่นเผลอ import os แล้ว rm -rf

ข้อ 5 คือ "กันอุบัติเหตุ" ไม่ใช่ "กันคนตั้งใจ" — AST guard ของ Python
ถูกหลบได้เสมอถ้าคนเขียนตั้งใจจริง ตัวที่เป็นขอบเขตความปลอดภัยจริงคือข้อ 2-3
เพราะต่อให้หลบ guard ได้ ก็ยังอยู่ใน process ที่ไม่มี credential ให้ขโมย

ของจริงบน Portal: ย้าย _run_in_subprocess() ไปเป็น container/microVM
(deepagents รองรับ sandbox backend อยู่แล้ว: Daytona, E2B, Modal, Runloop, Vercel)
รูปของ AgentSpec ไม่ต้องเปลี่ยนเลย เปลี่ยนแค่ executor ตัวนี้ตัวเดียว
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("agent.python_tool")

RUNNER = Path(__file__).parent / "_py_runner.py"

# ไฟล์ที่ tool ผลิต (PDF/CSV/รูป) ไปอยู่ที่นี่ แล้ว main.py mount เป็น static
# ประกาศไว้ที่นี่เพราะฝั่งนี้เป็นคนเขียน main.py เป็นคนเสิร์ฟ — จะได้ไม่หลุดกัน
DOWNLOAD_URL_PREFIX = "/downloads"


def downloads_dir() -> Path:
    """โฟลเดอร์ที่ save_file() ของ tool เขียนลง (สร้างให้ถ้ายังไม่มี)."""
    raw = os.getenv("DOWNLOADS_DIR", "").strip()
    d = Path(raw) if raw else Path(__file__).resolve().parents[2] / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d

DEFAULT_ALLOWED_IMPORTS = {
    "json", "math", "re", "datetime", "decimal", "statistics", "textwrap",
    "uuid", "base64", "hashlib", "itertools", "functools", "collections",
    "typing", "urllib", "urllib.parse", "string", "random", "time",
    "httpx", "requests",
    "fpdf",  # dependency ของโปรเจกต์ และ specs/pdf-generator.json ใช้ตั้งแต่ seed
}

# ชื่อที่เป็นทางลัดออกจากกรอบแบบคลาสสิก
BLOCKED_NAMES = {
    "eval", "exec", "compile", "__import__", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "open", "input", "breakpoint",
    "memoryview", "exit", "quit", "help",
}


def enabled() -> bool:
    return os.getenv("ALLOW_PYTHON_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}


def allowed_imports() -> set[str]:
    raw = os.getenv("PYTHON_TOOL_ALLOWED_IMPORTS", "").strip()
    if not raw:
        return DEFAULT_ALLOWED_IMPORTS
    return {m.strip() for m in raw.split(",") if m.strip()}


# --------------------------------------------------------------------------- guard

def guard_code(code: str, *, func_name: str = "run") -> list[str]:
    """ตรวจโค้ดแบบ static คืน list ของปัญหา (ว่าง = ผ่าน).

    คืนทั้งหมดพร้อมกัน ไม่ throw ทีละอัน — ฟอร์มจะได้โชว์ทีเดียวจบ
    """
    problems: list[str] = []

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"syntax error บรรทัด {exc.lineno}: {exc.msg}"]

    ok_imports = allowed_imports()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if alias.name not in ok_imports and root not in ok_imports:
                    problems.append(f"บรรทัด {node.lineno}: ห้าม import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            root = mod.split(".")[0]
            if mod not in ok_imports and root not in ok_imports:
                problems.append(f"บรรทัด {node.lineno}: ห้าม import จาก '{mod}'")
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            problems.append(f"บรรทัด {node.lineno}: ห้ามใช้ '{node.id}'")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                problems.append(f"บรรทัด {node.lineno}: ห้ามแตะ attribute '{node.attr}'")

    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    names = [f.name for f in funcs]
    if func_name not in names:
        problems.append(
            f"ต้องมี def {func_name}(...) ที่ระดับบนสุด — ตอนนี้เจอ: {', '.join(names) or 'ไม่มีฟังก์ชันเลย'}"
        )
    else:
        target = next(f for f in funcs if f.name == func_name)
        if isinstance(target, ast.AsyncFunctionDef):
            problems.append(f"{func_name}() ต้องเป็น def ธรรมดา ไม่ใช่ async def")
        if not ast.get_docstring(target):
            problems.append(
                f"{func_name}() ต้องมี docstring — agent ใช้ข้อความนั้นตัดสินใจว่าจะเรียก tool นี้เมื่อไหร่"
            )

    return problems


# --------------------------------------------------------------------------- schema

_TYPE_MAP = {
    "str": "string", "int": "integer", "float": "number", "bool": "boolean",
    "list": "array", "dict": "object",
}


def infer_schema(code: str, *, func_name: str = "run") -> dict[str, Any]:
    """อนุมาน JSON Schema จาก signature + type hint ของ run().

    ผู้ใช้จะได้ไม่ต้องเขียน JSON Schema เองซ้ำกับที่เขียนใน signature อยู่แล้ว
    """
    tree = ast.parse(code)
    target = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func_name), None
    )
    if target is None:
        return {"type": "object", "properties": {}}

    args = target.args
    positional = args.args
    defaults = args.defaults
    first_default = len(positional) - len(defaults)

    props: dict[str, Any] = {}
    required: list[str] = []

    for i, arg in enumerate(positional):
        hint = "string"
        if arg.annotation is not None:
            raw = ast.unparse(arg.annotation)
            base = raw.split("[")[0].split("|")[0].strip()
            hint = _TYPE_MAP.get(base, "string")
        props[arg.arg] = {"type": hint}
        if i < first_default:
            required.append(arg.arg)
        else:
            try:
                props[arg.arg]["default"] = ast.literal_eval(defaults[i - first_default])
            except Exception:  # noqa: BLE001
                pass

    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def extract_docstring(code: str, *, func_name: str = "run") -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ""
    target = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func_name), None
    )
    return (ast.get_docstring(target) or "").strip() if target else ""


# --------------------------------------------------------------------------- run

def _child_env() -> dict[str, str]:
    """env ของ subprocess — ตัดจนเหลือแต่ที่จำเป็น.

    ที่สำคัญคือ *ไม่มี* OPENAI_API_KEY / SECRET_KEY / อะไรก็ตามที่เป็นความลับ
    ต่อให้โค้ดผู้ใช้อ่าน os.environ ก็ไม่เจออะไร

    ถ้า deployment ไหนต้องให้ tool ยิงออกเน็ตผ่าน proxy ค่อยเปิดทีละตัวด้วย
    PYTHON_TOOL_PASS_ENV=HTTPS_PROXY,NO_PROXY  (Tier B ของ deployment ไม่ใช่ของ user)
    """
    env = {"PATH": os.getenv("PATH", "/usr/bin:/bin"), "PYTHONIOENCODING": "utf-8"}
    for name in os.getenv("PYTHON_TOOL_PASS_ENV", "").split(","):
        name = name.strip()
        if name and os.getenv(name):
            env[name] = os.environ[name]
    return env


def run_code(code: str, kwargs: dict[str, Any], *, timeout_s: float = 15.0,
             func_name: str = "run") -> dict[str, Any]:
    """รันโค้ดผู้ใช้ใน subprocess คืน dict {ok, result|error, stdout}"""
    payload = json.dumps(
        {
            "code": code,
            "func": func_name,
            "kwargs": kwargs,
            # ปลายทางของ save_file() — ส่งเป็น path จริงของ host
            "outdir": str(downloads_dir()),
            "url_prefix": DOWNLOAD_URL_PREFIX,
        },
        ensure_ascii=False,
    )

    try:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, str(RUNNER)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=_child_env(),
            cwd="/",  # ไม่ให้เริ่มที่โฟลเดอร์โปรเจกต์
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"tool ทำงานเกิน {timeout_s}s — ถูกหยุดแล้ว"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"เรียก subprocess ไม่สำเร็จ: {type(exc).__name__}: {exc}"}

    out = (proc.stdout or "").strip()
    if not out:
        return {"ok": False, "error": f"ไม่มีผลลัพธ์กลับมา (exit={proc.returncode}) {(proc.stderr or '')[-500:]}"}
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": f"ผลลัพธ์ไม่ใช่ JSON: {out[:500]}"}


# --------------------------------------------------------------------------- build

def build_python_tool(ref: str, description: str, code: str, *,
                      timeout_s: float = 15.0,
                      input_schema: dict[str, Any] | None = None) -> Any:
    """โค้ดที่ผู้ใช้วางมา -> LangChain tool ที่ agent เรียกได้"""
    from langchain_core.tools import StructuredTool

    if not enabled():
        raise RuntimeError(
            "tool ชนิด python ถูกปิดอยู่ — ตั้ง ALLOW_PYTHON_TOOLS=1 ใน .env ของ deployment ก่อน"
        )

    problems = guard_code(code)
    if problems:
        raise RuntimeError(f"โค้ดของ tool '{ref}' ไม่ผ่านการตรวจ:\n- " + "\n- ".join(problems))

    schema = input_schema or infer_schema(code)
    desc = description.strip() or extract_docstring(code) or f"Custom python tool '{ref}'"

    def _call(**kwargs: Any) -> str:
        log.info("python tool %s args=%s", ref, list(kwargs))
        out = run_code(code, kwargs, timeout_s=timeout_s)
        if out.get("ok"):
            return out.get("result", "")
        return f"tool '{ref}' error: {out.get('error', 'unknown')}"

    return StructuredTool.from_function(
        func=_call, name=ref, description=desc, args_schema=schema,
    )


def describe() -> dict[str, Any]:
    """ป้อนให้ /api/registry เพื่อให้ UI รู้ว่าเปิดใช้ได้ไหม และ import อะไรได้บ้าง"""
    return {
        "enabled": enabled(),
        "entrypoint": "run",
        "allowed_imports": sorted(allowed_imports()),
        "blocked_names": sorted(BLOCKED_NAMES),
        "note": "โค้ดรันใน subprocess ที่ไม่มี env/secret ของแอป และมี timeout จริง",
        "save_file": {
            "signature": "save_file(name, data) -> str",
            "url_prefix": DOWNLOAD_URL_PREFIX,
            "max_mb": 10,
            "note": "เขียนไฟล์ได้ทางนี้ทางเดียว (open() ถูกบล็อก) คืน path ที่กดดาวน์โหลดได้",
        },
    }
