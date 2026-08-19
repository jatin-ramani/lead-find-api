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
    # Absolute by default so the same file is used no matter which directory
    # the process was launched from. Swap for a postgresql+psycopg:// URL to
    # move to PostgreSQL — nothing else in the project needs to change.
    #
    # SecretStr because a PostgreSQL DSN embeds the database password. Read it
    # through `settings.database_url`; log it through `safe_database_url`.
    DATABASE_URL: SecretStr = SecretStr(
        f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
    )
    DATABASE_ECHO: bool = False
    DATABASE_POOL_RECYCLE_SECONDS: int = Field(default=1800, ge=0)

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    # `NoDecode` stops pydantic-settings from JSON-parsing the raw environment
    # value before validation, which is what lets the comma-separated form
    # below work — otherwise `CORS_ORIGINS=a,b` dies with a JSON decode error
    # inside the settings source, never reaching our validator.
    CORS_ORIGINS: Annotated[List[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True

    # ------------------------------------------------------------------
    # Geoapify (business discovery)
    # ------------------------------------------------------------------
    # Required in production; see the validator at the bottom of this class.
    # Read it through `settings.geoapify_api_key`.
    ADMIN_SECRET_KEY: SecretStr = Field(
        default=SecretStr(DEFAULT_ADMIN_SECRET),
        description="Secret key required for administrative API access.",
    )
    SESSION_TTL_SECONDS: int = Field(default=604800, gt=0)
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
    # `request_id` is injected by logging_config.RequestContextFilter, so every
    # line emitted while handling a request can be traced back to it. The
    # method, path, status, duration and client IP are appended after this
    # format string when they are known — see logging_config.HumanFormatter.
    LOG_FORMAT: str = (
        "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s | %(message)s"
    )

    # One JSON object per line instead of the human format above. Flip this in
    # any environment with a log shipper; nothing else changes.
    LOG_JSON: bool = False

    # Paths whose successful requests are not access-logged. A health probe
    # every two seconds otherwise drowns everything else. Failures on these
    # paths are always logged regardless.
    LOG_ACCESS_EXCLUDE_PATHS: Annotated[List[str], NoDecode] = []

    # Keep uvicorn's own access line as well as ours. Off by default: ours
    # says everything uvicorn's does plus the duration and a status-derived
    # level, so leaving both on logs every request twice.
    LOG_UVICORN_ACCESS: bool = False

    # Read the client address from X-Forwarded-For rather than the socket.
    # Required behind a load balancer, where every request otherwise appears
    # to come from the balancer. Off by default: the header is client-supplied
    # and forgeable unless something upstream overwrites it.
    LOG_TRUST_PROXY_HEADERS: bool = False

    # ------------------------------------------------------------------
    # Secret accessors
    #
    # The only places a secret is unwrapped. Everything else in the project
    # touches these, so `grep get_secret_value` lists every path a secret can
    # take out of this module — and it is this short.
    # ------------------------------------------------------------------
    @property
    def database_url(self) -> str:
        """The real DSN. For building the engine, not for printing."""

        url = self.DATABASE_URL.get_secret_value()
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        return url

    @property
    def safe_database_url(self) -> str:
        """The DSN with its password masked. Safe to log."""

        return mask_url_password(self.database_url)

    @property
    def geoapify_api_key(self) -> str:
        """
        The key, or `""` when unset.

        An empty string rather than None so callers can truth-test it without
        having to care which, and so a missing key can never be interpolated
        into a request as the literal "None".
        """

        if self.GEOAPIFY_API_KEY is None:
            return ""

        return self.GEOAPIFY_API_KEY.get_secret_value()

    @property
    def admin_secret(self) -> str:
        return self.ADMIN_SECRET_KEY.get_secret_value()

    @property
    def has_geoapify_key(self) -> bool:
        """For log lines and health output that must report presence only."""

        return bool(self.geoapify_api_key)

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
        """Swagger is hidden in production unless DEBUG is explicitly on."""
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
        """Accept `A,B` as well as a JSON array, since shells hate brackets."""

        if not isinstance(value, str):
            return value

        text = value.strip()

        if text.startswith("["):
            import json

            return json.loads(text)

        return [item.strip() for item in text.split(",") if item.strip()]

    @field_validator("DATABASE_URL")
    @classmethod
    def _known_dialect(cls, value: SecretStr) -> SecretStr:
        allowed = ("sqlite", "postgresql", "postgres")

        url = value.get_secret_value()

        if not url.startswith(allowed):
            # Only the scheme is quoted back. The full URL would put the
            # database password into stderr and into any log that captures it.
            scheme = url.split("://", 1)[0][:20] if "://" in url else "(none)"

            raise ValueError(
                f"unsupported database dialect {scheme!r}. "
                "Expected a sqlite:// or postgresql:// URL."
            )

        return value

    @field_validator("GEOAPIFY_API_KEY")
    @classmethod
    def _reject_placeholder_key(cls, value: Optional[SecretStr]):
        """
        Refuse the value shipped in `.env.example`.

        Copying the example and forgetting to fill it in otherwise produces a
        401 from Geoapify at the first scan, which reads like an outage.
        """

        if value is None:
            return None

        key = value.get_secret_value().strip()

        if not key:
            return None

        if key.lower() in PLACEHOLDER_SECRETS:
            raise ValueError(
                "GEOAPIFY_API_KEY is still the placeholder from .env.example. "
                "Set a real key from https://myprojects.geoapify.com/"
            )

        return SecretStr(key)

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _origins_are_origins(cls, value: List[str]) -> List[str]:
        """
        An origin is scheme + host + optional port. A trailing path is the
        commonest mistake and silently matches nothing, so the browser blocks
        the frontend with no clue why.
        """

        for origin in value:
            if origin == "*":
                continue

            if not origin.startswith(("http://", "https://")):
                raise ValueError(
                    f"CORS origin {origin!r} must start with http:// or "
                    "https://"
                )

            if origin.rstrip("/").count("/") > 2:
                raise ValueError(
                    f"CORS origin {origin!r} must not contain a path — an "
                    "origin is scheme://host[:port] only"
                )

        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        if self.MAX_PAGE_SIZE < self.DEFAULT_PAGE_SIZE:
            raise ValueError(
                "MAX_PAGE_SIZE must be greater than or equal to "
                "DEFAULT_PAGE_SIZE"
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

            # Scanning is the product; booting production without a key would
            # only surface at the first scan, long after deploy.
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

            # The key travels as a query parameter, so plain HTTP would put it
            # in cleartext on the wire and in every proxy log along the way.
            for name in ("GEOAPIFY_PLACES_URL", "GEOAPIFY_GEOCODE_URL"):
                url = getattr(self, name)

                if not url.startswith("https://"):
                    raise ValueError(
                        f"{name} must use https in production — the API key is "
                        "sent as a query parameter and would be in cleartext"
                    )

        return self

    # ------------------------------------------------------------------
    # Non-fatal risks
    # ------------------------------------------------------------------
    def configuration_warnings(self) -> List[str]:
        """
        Settings that are legal but dangerous.

        Not raised, because each has a legitimate use — a staging box with
        DEBUG on, say. Logged loudly at startup instead, so they are visible
        in the place someone actually looks when things go wrong.
        """

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
    """Print a readable summary and stop, instead of a bare traceback."""

    lines = [
        "",
        "=" * 68,
        " Lead Finder failed to start: invalid configuration",
        "=" * 68,
    ]

    for err in error.errors():
        name = ".".join(str(part) for part in err["loc"]) or "(model)"

        # `msg` only. `err["input"]` holds the offending value verbatim — for
        # DATABASE_URL that is the DSN, password and all — and pydantic puts
        # it in `str(error)`, which is why that is never printed here.
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
