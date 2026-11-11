"""Login, refresh and token checks. Authlib does the code flow and PKCE.

Authlib has no back-channel logout, so `validate_logout_token` follows the OIDC spec by hand.
"""

import secrets
import time
from dataclasses import dataclass

import httpx
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import OAuth2Client
from joserfc import jws, jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from .config import Settings

BACKCHANNEL_EVENT = "http://schemas.openid.net/event/backchannel-logout"


class OidcError(Exception):
    """The identity provider's answer cannot be trusted or the login was refused."""


class RefreshRejected(Exception):
    """Keycloak answered a refresh with an OAuth error. `error` is its error code."""

    def __init__(self, error: str):
        super().__init__(error)
        self.error = error


class KeycloakUnavailable(Exception):
    """A refresh got no usable answer, so nothing is known about the session."""


@dataclass(frozen=True)
class LoginResult:
    tokens: dict
    id_claims: dict
    access_claims: dict


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise OidcError(reason)


def _audience_has(claims: dict, client_id: str) -> bool:
    audience = claims.get("aud", [])
    return client_id in ([audience] if isinstance(audience, str) else audience)


def _check_common(claims: dict, settings: Settings) -> None:
    _require(claims.get("iss") == settings.issuer, "bad issuer")
    _require(_audience_has(claims, settings.client_id), "bad audience")
    _require(claims.get("exp", 0) > time.time(), "expired")


def validate_id_token(claims: dict, settings: Settings, *, nonce: str) -> None:
    _check_common(claims, settings)
    _require(secrets.compare_digest(claims.get("nonce", ""), nonce), "bad nonce")
    _require(bool(claims.get("sid")), "no session id")


def validate_access_token(claims: dict, settings: Settings) -> None:
    _check_common(claims, settings)


def validate_logout_token(claims: dict, settings: Settings) -> None:
    _check_common(claims, settings)
    _require(BACKCHANNEL_EVENT in (claims.get("events") or {}), "not a logout token")
    _require("nonce" not in claims, "logout token has a nonce")
    _require(bool(claims.get("sid")), "no session id")


class OidcClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self._endpoints = f"{settings.issuer}/protocol/openid-connect"
        self._keys: KeySet | None = None

    def _oauth(self) -> OAuth2Client:
        # Built per call, so a client_secret changed in settings applies at once.
        return OAuth2Client(
            client_id=self.settings.client_id,
            client_secret=self.settings.client_secret,
            scope="openid",
            redirect_uri=self.settings.redirect_uri,
            code_challenge_method="S256",
            timeout=10,
            transport=self.transport,
        )

    def begin_login(self) -> tuple[str, dict]:
        """Return the Keycloak URL and the values the callback has to check."""
        transaction = {
            "state": secrets.token_urlsafe(32),
            "nonce": secrets.token_urlsafe(32),
            "code_verifier": secrets.token_urlsafe(32),
        }
        with self._oauth() as oauth:
            url, _ = oauth.create_authorization_url(
                f"{self._endpoints}/auth",
                state=transaction["state"],
                nonce=transaction["nonce"],
                code_verifier=transaction["code_verifier"],
            )
        return url, transaction

    def complete_login(self, code: str, transaction: dict) -> LoginResult:
        try:
            with self._oauth() as oauth:
                tokens = dict(
                    oauth.fetch_token(
                        f"{self._endpoints}/token",
                        grant_type="authorization_code",
                        code=code,
                        code_verifier=transaction["code_verifier"],
                    )
                )
        except OAuthError as error:
            raise OidcError(error.error) from None
        id_claims = self.verified_claims(tokens["id_token"])
        access_claims = self.verified_claims(tokens["access_token"])
        validate_id_token(id_claims, self.settings, nonce=transaction["nonce"])
        validate_access_token(access_claims, self.settings)
        return LoginResult(tokens, id_claims, access_claims)

    def refresh(self, refresh_token: str) -> dict:
        """Return new tokens, or raise RefreshRejected or KeycloakUnavailable."""
        try:
            with self._oauth() as oauth:
                tokens = dict(
                    oauth.refresh_token(f"{self._endpoints}/token", refresh_token=refresh_token)
                )
            validate_access_token(self.verified_claims(tokens["access_token"]), self.settings)
        except OAuthError as error:
            raise RefreshRejected(error.error) from None
        except (httpx.HTTPError, ValueError, KeyError, OidcError):
            # ValueError: not JSON, which is what a proxy answers without Keycloak behind it.
            raise KeycloakUnavailable from None
        return tokens

    def logout_url_params(self, id_token: str | None) -> dict:
        params = {
            "client_id": self.settings.client_id,
            "post_logout_redirect_uri": self.settings.post_logout_uri,
        }
        return params | ({"id_token_hint": id_token} if id_token else {})

    @property
    def logout_endpoint(self) -> str:
        return f"{self._endpoints}/logout"

    def _load_keys(self) -> KeySet:
        response = httpx.get(f"{self._endpoints}/certs", timeout=10)
        response.raise_for_status()
        return KeySet.import_key_set(response.json())

    def verified_claims(self, token: str) -> dict:
        """Check the signature against Keycloak's keys and return the claims."""
        try:
            header = jws.extract_compact(token.encode()).headers()
            _require(header.get("alg") == "RS256", "unexpected algorithm")
            kid = header.get("kid")
            known = self._keys is not None and any(key.kid == kid for key in self._keys.keys)
            if not known:  # first use, or Keycloak rotated its keys
                self._keys = self._load_keys()
            return dict(jwt.decode(token, self._keys, algorithms=["RS256"]).claims)
        except (JoseError, ValueError, httpx.HTTPError) as error:
            raise OidcError(type(error).__name__) from None
