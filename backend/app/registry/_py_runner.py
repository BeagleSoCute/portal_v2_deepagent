"""ตัวรันโค้ดที่ผู้ใช้วางมาจาก UI — อยู่คนละ process กับ API เสมอ.

อ่าน JSON จาก stdin -> exec -> เรียก run(**kwargs) -> พิมพ์ JSON กลับทาง stdout

ตั้งใจให้ไฟล์นี้ไม่ import อะไรจาก app/ เลยแม้แต่ตัวเดียว
โค้ดของผู้ใช้จะได้ไม่มีทางเดินไปแตะ ConnectionStore / crypto / SpecStore ของแอป
และ parent เรียกด้วย env ที่ถูกตัดจนเหลือเท่าที่จำเป็น -> ในนี้ไม่มี api key ให้ขโมย

print() ของผู้ใช้ถูกดักไว้ ไม่ให้ไปปนกับ JSON ที่เป็นผลลัพธ์จริง (แต่ส่งกลับไปให้ดูได้)

โค้ดผู้ใช้มี save_file(name, data) ให้เรียกได้ — เป็นทางเดียวที่เขียนไฟล์ได้
เพราะ open() ถูก AST guard บล็อกไว้ (และควรบล็อกต่อไป) ตัว save_file เขียนได้
เฉพาะในโฟลเดอร์ดาวน์โหลดที่ parent ส่งมาให้ แล้วคืน path ที่เปิดจากเบราว์เซอร์ได้
ไฟล์จะได้ไม่ต้องวิ่งผ่าน context ของโมเดลเป็น base64
"""

import contextlib
import io
import json
import os
import re
import sys
import traceback
import uuid

MAX_FILE_BYTES = 10 * 1024 * 1024


def _make_save_file(outdir: str, url_prefix: str):
    """คืนฟังก์ชัน save_file ที่ผูกกับโฟลเดอร์ปลายทางไว้แล้ว."""

    def save_file(name: str, data) -> str:
        """เซฟไฟล์ลงโฟลเดอร์ดาวน์โหลด แล้วคืน path ที่เปิดจากเบราว์เซอร์ได้

        name: ชื่อไฟล์ เช่น "report.pdf" (path ถูกตัดทิ้ง กันไต่ไปโฟลเดอร์อื่น)
        data: bytes หรือ str — ถ้าเป็น fpdf ให้ส่ง pdf.output(dest='S').encode('latin-1')
        """
        if not outdir:
            raise RuntimeError("deployment นี้ไม่ได้เปิดให้เซฟไฟล์")

        safe = os.path.basename(str(name)).strip()
        safe = re.sub(r"[^A-Za-z0-9._\-\u0E00-\u0E7F]", "_", safe)
        if not safe or safe.startswith("."):
            raise ValueError("ชื่อไฟล์ไม่ถูกต้อง")

        if isinstance(data, str):
            blob = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray)):
            blob = bytes(data)
        else:
            raise TypeError("data ต้องเป็น bytes หรือ str")

        if len(blob) > MAX_FILE_BYTES:
            raise ValueError(f"ไฟล์ใหญ่เกิน {MAX_FILE_BYTES // 1024 // 1024} MB")

        target = os.path.join(outdir, safe)
        if os.path.exists(target):  # ไม่ทับของเดิม
            stem, ext = os.path.splitext(safe)
            safe = f"{stem}-{uuid.uuid4().hex[:6]}{ext}"
            target = os.path.join(outdir, safe)

        with open(target, "wb") as fh:  # noqa: PTH123
            fh.write(blob)

        return f"{url_prefix}/{safe}"

    return save_file


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"payload ไม่ใช่ JSON: {exc}"}))
        return 0

    code = payload.get("code", "")
    func_name = payload.get("func", "run")
    kwargs = payload.get("kwargs", {})

    captured = io.StringIO()
    namespace: dict = {
        "__name__": "user_tool",
        "save_file": _make_save_file(
            payload.get("outdir", ""),
            payload.get("url_prefix", "/downloads"),
        ),
    }

    try:
        with contextlib.redirect_stdout(captured):
            exec(compile(code, "<user_tool>", "exec"), namespace, namespace)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "ok": False,
            "error": f"โค้ดพังตั้งแต่ตอนโหลด: {type(exc).__name__}: {exc}",
            "trace": traceback.format_exc()[-2000:],
        }, ensure_ascii=False))
        return 0

    fn = namespace.get(func_name)
    if not callable(fn):
        print(json.dumps({
            "ok": False,
            "error": f"ไม่พบฟังก์ชันชื่อ {func_name}() — tool ต้องมี def {func_name}(...) เสมอ",
        }, ensure_ascii=False))
        return 0

    try:
        with contextlib.redirect_stdout(captured):
            result = fn(**kwargs)
    except TypeError as exc:
        print(json.dumps({
            "ok": False,
            "error": f"เรียก {func_name}() ด้วย argument ไม่ตรง signature: {exc}",
        }, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc()[-2000:],
            "stdout": captured.getvalue()[-2000:],
        }, ensure_ascii=False))
        return 0

    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception:  # noqa: BLE001
            text = str(result)

    print(json.dumps({
        "ok": True,
        "result": text,
        "stdout": captured.getvalue()[-2000:],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
