from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything comes from the environment, so a new secret needs a restart, not a build."""

    model_config = SettingsConfigDict(env_prefix="AGENTS_", env_file=".env", extra="ignore")

    app_url: str = "http://agents.localhost"
    issuer: str = "http://idp.localhost/realms/portal"
    portal_url: str = "http://portal.localhost"
    client_id: str = "agents"
    client_secret: str = ""
    token_encryption_key: str = ""
    database_url: str = ""
    frontend_dist: str = ""

    # Login is required unless this is set. Only for a developer's own machine:
    # with it, anyone who can reach the port is the same user "demo".
    auth_disabled: bool = False

    # Built from app_url instead of the request, so a missing X-Forwarded-Proto
    # can never turn these into http:// behind a TLS proxy.
    @property
    def redirect_uri(self) -> str:
        return f"{self.app_url}/auth/callback"

    @property
    def post_logout_uri(self) -> str:
        return f"{self.app_url}/logged-out"

    @property
    def denied_url(self) -> str:
        return f"{self.portal_url}/denied?app={self.client_id}"

    @property
    def cookie_secure(self) -> bool:
        return self.app_url.startswith("https://")

    def sessions_url(self, data_dir: Path) -> str:
        return self.database_url or f"sqlite:///{data_dir / 'sessions.db'}"

    def problems(self) -> list[str]:
        """What is missing for login to work. Empty when login is off or fully configured."""
        if self.auth_disabled:
            return []
        missing = [name for name in ("client_secret", "token_encryption_key") if not getattr(self, name)]
        return [f"AGENTS_{name.upper()} is not set" for name in missing]
