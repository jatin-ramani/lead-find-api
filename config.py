"""
Centralised application configuration.

Every tunable value in the project is declared here and nowhere else. Modules
import `settings`; no module calls `os.getenv` or `load_dotenv` of its own, so
there is exactly one place to look when a value needs changing and exactly one
place that can be wrong.

Values are resolved in this order:

1. real environment variables (what a container or CI runner sets)
2. the `.env` file next to this module (developer convenience)
3. the defaults below (which reproduce the historical behaviour)

Import-time validation means a misconfigured process fails at startup with a
readable message rather than at the first request that happens to need the
missing value.
"""

import os
import sys
import secrets
from enum import Enum
from pathlib import Path
from typing import Annotated, List, Literal, Optional

from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent

DEFAULT_SQLITE_PATH = BACKEND_DIR / "database" / "leadfinder.db"

# Values shipped in .env.example. Deploying with one of these means the file
# was copied and never filled in — a mistake worth refusing to boot on.
DEFAULT_ADMIN_SECRET = "leadfinder_admin_secret_2026_change_in_production"

PLACEHOLDER_SECRETS = frozenset(
    {
        "your-geoapify-api-key-here",
        "changeme",
        "change-me",
        "replace-me",
        "todo",
        "xxx",
        "secret",
    }
)


class Environment(str, Enum):
    development = "development"
    testing = "testing"
    production = "production"


def mask_url_password(url: str) -> str:
    """
    A connection string with its password replaced by `***`.

    For logs and error messages. `postgresql://app:hunter2@db/leadfinder`
    becomes `postgresql://app:***@db/leadfinder`, which still answers "which
    database am I pointed at" without answering "with what credentials".
    """

    try:
        from sqlalchemy.engine import make_url

        return make_url(url).render_as_string(hide_password=True)

    except Exception:
        # An unparseable URL must not take the process down from inside a log
        # call. Say nothing rather than risk echoing credentials.
        return "<unparseable database url>"


# The one `os.getenv` in the project, and it cannot be a setting: it decides
# whether the settings file is read at all. Containers and CI pass real
# environment variables and should not silently inherit a `.env` that happened
# to be copied into the image.
IGNORE_ENV_FILE = os.getenv("LEADFINDER_IGNORE_ENV_FILE", "").strip().lower() in {
    "1",
    "true",
    "yes",
}


class Settings(BaseSettings):
    """Application settings, read from the environment and `.env`."""

    model_config = SettingsConfigDict(
        env_file=None if IGNORE_ENV_FILE else BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------------
    ENVIRONMENT: Environment = Environment.development
    DEBUG: bool = False

    # ------------------------------------------------------------------
    # Application metadata (surfaced by /version and the OpenAPI schema)
    # ------------------------------------------------------------------
    APP_NAME: str = "Lead Finder API"
    APP_VERSION: str = "1.0.0"

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DATABASE_URL: SecretStr = SecretStr(
        f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
    )
    DATABASE_ECHO: bool = False
    DATABASE_POOL_RECYCLE_SECONDS: int = Field(default=1800, ge=0)
    RUN_MIGRATIONS: bool = True

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    CORS_ORIGINS: Annotated[List[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True

    # ------------------------------------------------------------------
    # Administrative Access & Sessions
    # ------------------------------------------------------------------
    ADMIN_SECRET_KEY: SecretStr = Field(
        default=SecretStr(DEFAULT_ADMIN_SECRET),
        description="Secret key required for administrative API access.",
    )
    SESSION_TTL_SECONDS: int = Field(default=604800, gt=0)

    # ------------------------------------------------------------------
    # Geoapify (business discovery)
    # ------------------------------------------------------------------
    GEOAPIFY_API_KEY: Optional[SecretStr] = None
    GEOAPIFY_PLACES_URL: str = "https://api.geoapify.com/v2/places"
    GEOAPIFY_GEOCODE_URL: str = "https://api.geoapify.com/v1/geocode/search"
    GEOAPIFY_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=300)
    GEOAPIFY_SEARCH_LIMIT: int = Field(default=500, ge=1, le=500)
    GEOAPIFY_MAX_SCAN_PAGES: int = Field(default=20, ge=1, le=100)
    GEOAPIFY_SEARCH_RADIUS_METRES: int = Field(default=5000, ge=100, le=50_000)

    # ------------------------------------------------------------------
    # Website scraper
    # ------------------------------------------------------------------
    SCRAPER_TIMEOUT_SECONDS: int = Field(default=20, ge=1, le=300)
    SCRAPER_MAX_RESPONSE_BYTES: int = Field(
        default=5 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024
    )
    SCRAPER_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )

    # ------------------------------------------------------------------
    # Email Automation & Provider
    # ------------------------------------------------------------------
    EMAIL_PROVIDER: Literal["mock", "gmail", "resend"] = "mock"
    RESEND_API_KEY: Optional[SecretStr] = None
    RESEND_FROM_EMAIL: str = "noreply@leadfinder.local"

    # Gmail API OAuth 2.0 & Quota Settings
    GMAIL_CLIENT_ID: Optional[SecretStr] = None
    GMAIL_CLIENT_SECRET: Optional[SecretStr] = None
    GMAIL_REDIRECT_URI: Optional[str] = None
    GMAIL_TOKEN_ENCRYPTION_KEY: Optional[SecretStr] = None
    EMAIL_DAILY_QUOTA_LIMIT: int = Field(default=400, ge=1)

    # ------------------------------------------------------------------
    # AI Template Generation Provider
    # ------------------------------------------------------------------
    AI_PROVIDER: Literal["mock", "openai", "anthropic", "gemini"] = "mock"
    OPENAI_API_KEY: Optional[SecretStr] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    ANTHROPIC_API_KEY: Optional[SecretStr] = None
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    GEMINI_API_KEY: Optional[SecretStr] = None
    GEMINI_MODEL: str = "gemini-1.5-flash"

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    DEFAULT_PAGE_SIZE: int = Field(default=20, ge=1)
    MAX_PAGE_SIZE: int = Field(default=100, ge=1)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    LOG_LEVEL: Literal[
        "CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"
    ] = "INFO"
    LOG_FORMAT: str = (
        "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s | %(message)s"
    )
    LOG_JSON: bool = False
    LOG_ACCESS_EXCLUDE_PATHS: Annotated[List[str], NoDecode] = []
    LOG_UVICORN_ACCESS: bool = False

    LOG_TRUST_PROXY_HEADERS: bool = False

    # ------------------------------------------------------------------
    # Secret accessors
    # ------------------------------------------------------------------
    @property
    def database_url(self) -> str:
        url = self.DATABASE_URL.get_secret_value()
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        return url

    @property
    def safe_database_url(self) -> str:
        return mask_url_password(self.database_url)

    @property
    def geoapify_api_key(self) -> str:
        if self.GEOAPIFY_API_KEY is None:
            return ""
        return self.GEOAPIFY_API_KEY.get_secret_value()

    @property
    def admin_secret(self) -> str:
        return self.ADMIN_SECRET_KEY.get_secret_value()

    @property
    def has_geoapify_key(self) -> bool:
        return bool(self.geoapify_api_key)

    @property
    def resend_api_key(self) -> str:
        if self.RESEND_API_KEY is None:
            return ""
        return self.RESEND_API_KEY.get_secret_value()

    @property
    def has_resend_key(self) -> bool:
        return bool(self.resend_api_key)

    @property
    def gmail_client_id(self) -> str:
        if self.GMAIL_CLIENT_ID is None:
            return ""
        if isinstance(self.GMAIL_CLIENT_ID, str):
            return self.GMAIL_CLIENT_ID
        return self.GMAIL_CLIENT_ID.get_secret_value()

    @property
    def gmail_client_secret(self) -> str:
        if self.GMAIL_CLIENT_SECRET is None:
            return ""
        if isinstance(self.GMAIL_CLIENT_SECRET, str):
            return self.GMAIL_CLIENT_SECRET
        return self.GMAIL_CLIENT_SECRET.get_secret_value()

    @property
    def gmail_token_encryption_key(self) -> str:
        if self.GMAIL_TOKEN_ENCRYPTION_KEY is None:
            return ""
        if isinstance(self.GMAIL_TOKEN_ENCRYPTION_KEY, str):
            return self.GMAIL_TOKEN_ENCRYPTION_KEY
        return self.GMAIL_TOKEN_ENCRYPTION_KEY.get_secret_value()

    @property
    def has_gmail_credentials(self) -> bool:
        return bool(self.gmail_client_id and self.gmail_client_secret)



    @property
    def openai_api_key(self) -> str:
        if self.OPENAI_API_KEY is None:
            return ""
        return self.OPENAI_API_KEY.get_secret_value()

    @property
    def anthropic_api_key(self) -> str:
        if self.ANTHROPIC_API_KEY is None:
            return ""
        return self.ANTHROPIC_API_KEY.get_secret_value()

    @property
    def gemini_api_key(self) -> str:
        if self.GEMINI_API_KEY is None:
            return ""
        return self.GEMINI_API_KEY.get_secret_value()

    @property
    def has_ai_key(self) -> bool:
        if self.AI_PROVIDER == "openai":
            return bool(self.openai_api_key)
        elif self.AI_PROVIDER == "anthropic":
            return bool(self.anthropic_api_key)
        elif self.AI_PROVIDER == "gemini":
            return bool(self.gemini_api_key)
        return True

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT is Environment.production

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def docs_url(self) -> Optional[str]:
        return None if (self.is_production and not self.DEBUG) else "/docs"

    @property
    def redoc_url(self) -> Optional[str]:
        return None if (self.is_production and not self.DEBUG) else "/redoc"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @field_validator("CORS_ORIGINS", "LOG_ACCESS_EXCLUDE_PATHS", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if not isinstance(value, str):
            return value

        text = value.strip()
        if text.startswith("["):
            import json
            return json.loads(text)

        return [item.strip() for item in text.split(",") if item.strip()]

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _validate_cors_origins(cls, origins: List[str]) -> List[str]:
        for origin in origins:
            if origin == "*":
                continue
            if not (origin.startswith("http://") or origin.startswith("https://")):
                raise ValueError(
                    f"CORS origin {origin!r} must start with http:// or https://"
                )
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            if parsed.path and parsed.path != "/":
                raise ValueError(
                    f"CORS origin {origin!r} must not contain a path"
                )
        return origins

    @field_validator("DATABASE_URL")
    @classmethod
    def _known_dialect(cls, value: SecretStr) -> SecretStr:
        allowed = ("sqlite", "postgresql", "postgres")
        url = value.get_secret_value()
        if not url.startswith(allowed):
            scheme = url.split("://", 1)[0][:20] if "://" in url else "(none)"
            raise ValueError(
                f"unsupported database dialect {scheme!r}. "
                "Expected a sqlite:// or postgresql:// URL."
            )
        return value

    @field_validator("GEOAPIFY_API_KEY", mode="before")
    @classmethod
    def _reject_placeholder_key(cls, value):
        if value is None:
            return None

        if isinstance(value, SecretStr):
            unwrapped = value.get_secret_value().strip()
        else:
            unwrapped = str(value).strip()

        if not unwrapped:
            return None

        if unwrapped.lower() in PLACEHOLDER_SECRETS:
            raise ValueError(
                "placeholder value detected. Paste a real Geoapify API key "
                "into backend/.env or unset the variable."
            )
        return SecretStr(unwrapped)

    @model_validator(mode="after")
    def _cross_field_and_production_rules(self) -> "Settings":
        if self.DEFAULT_PAGE_SIZE > self.MAX_PAGE_SIZE:
            raise ValueError(
                f"DEFAULT_PAGE_SIZE ({self.DEFAULT_PAGE_SIZE}) cannot exceed "
                f"MAX_PAGE_SIZE ({self.MAX_PAGE_SIZE})"
            )

        if self.is_production:
            if len(self.admin_secret) < 32:
                raise ValueError(
                    "ADMIN_SECRET_KEY must be at least 32 characters in production"
                )

            if secrets.compare_digest(self.admin_secret, DEFAULT_ADMIN_SECRET):
                raise ValueError(
                    "ADMIN_SECRET_KEY must be changed from its default in production"
                )

            if not self.has_geoapify_key:
                raise ValueError(
                    "GEOAPIFY_API_KEY is required when ENVIRONMENT=production"
                )

            if self.is_sqlite:
                raise ValueError(
                    "SQLite is not supported in production — it allows a "
                    "single writer, and background scrape jobs will contend "
                    "with API requests. Set DATABASE_URL to PostgreSQL."
                )

            if "*" in self.CORS_ORIGINS:
                raise ValueError(
                    "CORS_ORIGINS may not be '*' in production"
                )

            if any(not origin.startswith("https://") for origin in self.CORS_ORIGINS):
                raise ValueError(
                    "CORS_ORIGINS must use https in production"
                )

            for name in ("GEOAPIFY_PLACES_URL", "GEOAPIFY_GEOCODE_URL"):
                url = getattr(self, name)
                if not url.startswith("https://"):
                    raise ValueError(
                        f"{name} must use https in production — the API key is "
                        "sent as a query parameter and would be in cleartext"
                    )

        return self

    def configuration_warnings(self) -> List[str]:
        warnings: List[str] = []

        if self.is_production and self.DEBUG:
            warnings.append(
                "DEBUG=true in production: exception types and messages are "
                "returned in error responses, and /docs is served. Turn it "
                "off unless you are actively debugging this deployment."
            )

        if self.is_production and self.CORS_ALLOW_CREDENTIALS:
            if len(self.CORS_ORIGINS) > 3:
                warnings.append(
                    f"{len(self.CORS_ORIGINS)} CORS origins are allowed to "
                    "send credentials. Keep this list to the origins that "
                    "genuinely need it."
                )

        if not self.has_geoapify_key:
            warnings.append(
                "GEOAPIFY_API_KEY is not set — scanning will fail with 502. "
                "This is refused outright when ENVIRONMENT=production."
            )

        if self.DATABASE_ECHO:
            warnings.append(
                "DATABASE_ECHO=true: every SQL statement, including its bound "
                "parameters, is written to the log."
            )

        return warnings


def _fail(error: ValidationError) -> None:
    lines = [
        "",
        "=" * 68,
        " Lead Finder failed to start: invalid configuration",
        "=" * 68,
    ]

    for err in error.errors():
        name = ".".join(str(part) for part in err["loc"]) or "(model)"
        lines.append(f"  {name}: {err['msg']}")

    lines += [
        "",
        "  Set these in the environment or in backend/.env",
        "  See backend/.env.example for the full list.",
        "=" * 68,
        "",
    ]

    print("\n".join(lines), file=sys.stderr)
    raise SystemExit(1)


try:
    settings = Settings()
except ValidationError as exc:  # pragma: no cover - exercised in a subprocess
    _fail(exc)
