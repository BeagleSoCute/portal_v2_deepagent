import logging
from typing import Annotated
from urllib.parse import urlencode

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import delete

from .current_user import DbDep, OidcDep, SettingsDep, same_origin_write
from .oidc import OidcError, validate_logout_token
from .sessions import (
    SESSION_COOKIE,
    AuthSession,
    create_session,
    decrypt_json,
    delete_expired_sessions,
    encrypt_json,
    hash_sid,
    set_session_cookie,
    stored_tokens,
)

LOGIN_COOKIE = "login_tx"
LOGIN_TTL_SECONDS = 600

logger = logging.getLogger("agents.auth")
router = APIRouter(prefix="/auth")


@router.get("/login")
def login(settings: SettingsDep, oidc: OidcDep) -> Response:
    url, transaction = oidc.begin_login()
    response = RedirectResponse(url, status.HTTP_302_FOUND)
    # Encrypted, so the PKCE verifier and nonce are unreadable in the browser.
    response.set_cookie(
        LOGIN_COOKIE,
        encrypt_json(settings, transaction).decode(),
        max_age=LOGIN_TTL_SECONDS,
        path="/auth",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    return response


@router.get("/callback")
def callback(
    request: Request,
    settings: SettingsDep,
    oidc: OidcDep,
    db: DbDep,
    state: str = "",
    code: str = "",
    error: str = "",
) -> Response:
    try:
        cookie = request.cookies.get(LOGIN_COOKIE, "").encode()
        transaction = decrypt_json(settings, cookie, ttl=LOGIN_TTL_SECONDS)
    except InvalidToken:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "login expired") from None
    if state != transaction["state"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad state")
    if error or not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "login refused")

    try:
        result = oidc.complete_login(code, transaction)
    except OidcError as failure:
        logger.warning("login rejected: %s", failure)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "login failed") from None
    except httpx.HTTPError:
        logger.warning("Keycloak unreachable during login")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "identity provider error") from None

    # A session is created even without the access role: the role is checked by
    # current_user, which sends the browser on to the Portal's /denied page.
    delete_expired_sessions(db)
    sid = create_session(db, settings, result)
    db.commit()

    response = RedirectResponse("/", status.HTTP_302_FOUND)
    set_session_cookie(response, sid, settings)
    response.delete_cookie(LOGIN_COOKIE, path="/auth")
    return response


@router.post("/logout", dependencies=[Depends(same_origin_write)])
def logout(request: Request, settings: SettingsDep, oidc: OidcDep, db: DbDep) -> Response:
    id_token = None
    sid = request.cookies.get(SESSION_COOKIE)
    row = db.get(AuthSession, hash_sid(sid)) if sid else None
    if row is not None:
        id_token = stored_tokens(settings, row)["id_token"]
        db.delete(row)
        db.commit()

    target = f"{oidc.logout_endpoint}?{urlencode(oidc.logout_url_params(id_token))}"
    response = RedirectResponse(target, status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/backchannel-logout")
def backchannel_logout(
    settings: SettingsDep,
    oidc: OidcDep,
    db: DbDep,
    logout_token: Annotated[str, Form()] = "",
) -> Response:
    """Keycloak calls this server to server, so anything unverified is refused."""
    no_store = {"Cache-Control": "no-store"}
    try:
        claims = oidc.verified_claims(logout_token)
        validate_logout_token(claims, settings)
    except OidcError as failure:
        logger.warning("rejected logout token: %s", failure)
        return JSONResponse({"error": "invalid_request"}, 400, headers=no_store)

    # Only this sid: another device of the same user has a sid of its own.
    db.execute(delete(AuthSession).where(AuthSession.kc_sid == claims["sid"]))
    db.commit()
    return Response(status_code=status.HTTP_200_OK, headers=no_store)
