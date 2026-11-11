"""เข้ารหัส secret ที่ผู้ใช้กรอกผ่าน UI ก่อนเขียนลงดิสก์.

ทำไมต้องมี: พอ api key ไม่ได้อยู่ใน .env แล้ว มันต้องไปอยู่ที่ไหนสักที่
เก็บ plaintext ในไฟล์ JSON = ใครอ่านโฟลเดอร์ data/ ได้ก็ได้ key ไปหมด

master key มาจาก env SECRET_KEY ถ้าไม่มีจะสร้างเก็บไว้ที่ data/.secret_key (0600)
เพื่อให้ผู้ใช้ไม่ต้องไปยุ่งกับ .env เลยตามที่ตกลง

ข้อจำกัดที่ต้องรู้: key อยู่บนเครื่องเดียวกับ ciphertext ป้องกันได้แค่
"ไฟล์หลุด" ไม่ได้ป้องกัน "เครื่องหลุด" ของจริงบน Portal ควรใช้ KMS/Vault
"""

from __future__ import annotations

import os
from pathlib import Path

_FERNET = None


def _key_path() -> Path:
    return Path(os.getenv("DATA_DIR", "./data")).resolve() / ".secret_key"


def _load_key() -> bytes:
    from cryptography.fernet import Fernet

    env = os.getenv("SECRET_KEY", "").strip()
    if env:
        return env.encode()

    p = _key_path()
    if p.exists():
        return p.read_bytes().strip()

    p.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    p.write_bytes(key)
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return key


def _fernet():
    global _FERNET
    if _FERNET is None:
        from cryptography.fernet import Fernet

        _FERNET = Fernet(_load_key())
    return _FERNET


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")


def mask(plaintext: str) -> str:
    """โชว์ให้ user รู้ว่าใส่ตัวไหนไว้ โดยไม่คืนค่าจริงกลับไปที่ browser."""
    if not plaintext:
        return ""
    if len(plaintext) <= 8:
        return "•" * len(plaintext)
    return f"{plaintext[:4]}{'•' * 8}{plaintext[-4:]}"
