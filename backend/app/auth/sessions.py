"""Server-side sessions. The browser only ever holds the sid cookie."""

import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from fastapi import Response
from sqlalchemy import LargeBinary, Text, delete, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from .config import Settings
from .db import Base, utc_iso
from .oidc import LoginResult

SESSION_COOKIE = "sid"
TOKEN_NAMES = ("access_token", "refresh_token", "id_token")


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # hash of the sid cookie
    kc_sid: Mapped[str] = mapped_column(Text, index=True)  # for back-channel logout
    sub: Mapped[str] = mapped_column(Text)
    username: Mapped[str] = mapped_column(Text)
    tokens_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    access_expires_at: Mapped[str] = mapped_column(Text)
    refresh_expires_at: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text)


def encrypt_json(settings: Settings, value: dict) -> bytes:
    return Fernet(settings.token_encryption_key).encrypt(json.dumps(value).encode())


def decrypt_json(settings: Settings, blob: bytes, ttl: int | None = None) -> dict:
    """Raises cryptography.fernet.InvalidToken if the blob is altered or older than ttl."""
    return json.loads(Fernet(settings.token_encryption_key).decrypt(blob, ttl=ttl))


def hash_sid(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def set_session_cookie(response: Response, value: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def create_session(db: Session, settings: Settings, login: LoginResult) -> str:
    """Store the session and return the value for the sid cookie."""
    now = datetime.now(UTC)
    sid = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            id=hash_sid(sid),
            kc_sid=login.id_claims["sid"],
            sub=login.access_claims["sub"],
            username=login.access_claims["preferred_username"],
            tokens_encrypted=encrypt_json(
                settings, {name: login.tokens[name] for name in TOKEN_NAMES}
            ),
            access_expires_at=utc_iso(now + timedelta(seconds=login.tokens["expires_in"])),
            refresh_expires_at=utc_iso(now + timedelta(seconds=login.tokens["refresh_expires_in"])),
            created_at=utc_iso(now),
        )
    )
    return sid


def save_refreshed(
    db: Session, settings: Settings, session_id: str, previous: dict, fresh: dict
) -> dict | None:
    """Store the refreshed tokens. None means the session ended while Keycloak answered."""
    now = datetime.now(UTC)
    tokens = {name: fresh.get(name, previous[name]) for name in TOKEN_NAMES}
    result = db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id)
        .values(
            tokens_encrypted=encrypt_json(settings, tokens),
            access_expires_at=utc_iso(now + timedelta(seconds=fresh["expires_in"])),
            refresh_expires_at=utc_iso(now + timedelta(seconds=fresh["refresh_expires_in"])),
        )
    )
    db.commit()
    return tokens if result.rowcount else None


def delete_expired_sessions(db: Session) -> None:
    db.execute(delete(AuthSession).where(AuthSession.refresh_expires_at <= utc_iso()))


def stored_tokens(settings: Settings, row: AuthSession) -> dict:
    return decrypt_json(settings, row.tokens_encrypted)


def token_claims(token: str) -> dict:
    """Read a token that was verified before it was stored. Only for this session store."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
