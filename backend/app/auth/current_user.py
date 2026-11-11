"""The one place that decides who is calling, and the one place that refreshes tokens.

| Refresh outcome      | What happens                             |
|----------------------|------------------------------------------|
| success              | save the tokens, check the role again    |
| invalid_grant        | delete the session, answer 401           |
| unauthorized_client  | keep the session, answer 503, raise alert|
| invalid_client       | keep the session, answer 503             |
| Keycloak unreachable | keep the session, answer 503             |

invalid_client means an admin disabled or deleted this client. That is no fault of the secret,
so no alert, and after an enable the same refresh token works again.

Old claims are never used after a failed refresh.
"""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .config import Settings
from .db import get_db, utc_iso
from .oidc import KeycloakUnavailable, OidcClient, RefreshRejected
from .sessions import (
    SESSION_COOKIE,
    AuthSession,
    hash_sid,
    save_refreshed,
    stored_tokens,
    token_claims,
)

ACCESS_ROLE = "access"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

logger = logging.getLogger("agents.auth")


@dataclass(frozen=True)
class User:
    sub: str
    username: str


# Who every request is when login is switched off (AGENTS_AUTH_DISABLED=1).
DEMO_USER = User(sub="00000000-0000-0000-0000-000000000001", username="demo")


class NoAccess(Exception):
    """Signed in, but without the access role. Answered with 403 by main.py."""


class SessionLocks:
    """One lock per session. This app runs as one instance, so in-process is enough."""

    def __init__(self):
        self._guard = threading.Lock()
        self._locks: dict[str, tuple[threading.Lock, int]] = {}

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            lock, users = self._locks.get(key, (threading.Lock(), 0))
            self._locks[key] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._guard:
                lock, users = self._locks[key]
                if users == 1:
                    del self._locks[key]
                else:
                    self._locks[key] = (lock, users - 1)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_oidc(request: Request) -> OidcClient:
    return request.app.state.oidc


SettingsDep = Annotated[Settings, Depends(get_settings)]
OidcDep = Annotated[OidcClient, Depends(get_oidc)]
DbDep = Annotated[Session, Depends(get_db)]


def not_logged_in() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")


def keycloak_down() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Login service unavailable")


def refresh_session(
    db: Session, session_id: str, settings: Settings, oidc: OidcClient, row: AuthSession
) -> dict:
    """Return the claims of a new access token, or raise as the table above says."""
    previous = stored_tokens(settings, row)
    db.rollback()  # no open transaction while Keycloak answers
    try:
        fresh = oidc.refresh(previous["refresh_token"])
    except RefreshRejected as rejected:
        if rejected.error == "invalid_grant":
            db.execute(delete(AuthSession).where(AuthSession.id == session_id))
            db.commit()
            raise not_logged_in() from None
        if rejected.error == "unauthorized_client":
            logger.error("ALERT refresh refused with unauthorized_client: check the client secret")
        else:
            logger.warning("refresh refused: %s", rejected.error)
        raise keycloak_down() from None
    except KeycloakUnavailable:
        logger.warning("Keycloak unreachable during refresh")
        raise keycloak_down() from None

    tokens = save_refreshed(db, settings, session_id, previous, fresh)
    if tokens is None:
        raise not_logged_in()
    return token_claims(tokens["access_token"])


def live_claims(
    db: Session, session_id: str, settings: Settings, oidc: OidcClient, locks: SessionLocks
) -> tuple[User, dict]:
    def user_of(row: AuthSession) -> User:
        return User(sub=row.sub, username=row.username)

    def claims_of(row: AuthSession) -> dict:
        return token_claims(stored_tokens(settings, row)["access_token"])

    row = db.get(AuthSession, session_id)
    if row is None:
        raise not_logged_in()
    if row.access_expires_at > utc_iso():
        return user_of(row), claims_of(row)

    db.rollback()  # requests queueing for the lock must not hold a connection
    with locks.hold(session_id):
        # Another request may have refreshed, or logout may have deleted, in the meantime.
        row = db.get(AuthSession, session_id, populate_existing=True)
        if row is None:
            raise not_logged_in()
        user = user_of(row)
        if row.access_expires_at > utc_iso():
            return user, claims_of(row)
        return user, refresh_session(db, session_id, settings, oidc, row)


def current_user(request: Request, db: DbDep, settings: SettingsDep, oidc: OidcDep) -> User:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        raise not_logged_in()
    locks = request.app.state.refresh_locks
    user, claims = live_claims(db, hash_sid(sid), settings, oidc, locks)
    client = claims.get("resource_access", {}).get(settings.client_id, {})
    if ACCESS_ROLE not in client.get("roles", []):
        raise NoAccess
    return user


def same_origin_write(request: Request, settings: SettingsDep) -> None:
    """Apps live on sibling subdomains, so SameSite=Lax still sends them the cookie."""
    if request.method in SAFE_METHODS:
        return
    if request.headers.get("origin") != settings.app_url:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad origin")


def require_user(request: Request, db: DbDep, settings: SettingsDep, oidc: OidcDep) -> User:
    """The guard on every /api route and on generated files. The user is kept for the audit log."""
    if settings.auth_disabled:
        user = DEMO_USER
    else:
        same_origin_write(request, settings)
        user = current_user(request, db, settings, oidc)
    request.state.user = user
    return user
