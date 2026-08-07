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

import sys
from enum import Enum
from pathlib import Path
from typing import Annotated, List, Literal, Optional

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent

DEFAULT_SQLITE_PATH = BACKEND_DIR / "database" / "leadfinder.db"


class Environment(str, Enum):
    development = "development"
    testing = "testing"
    production = "production"


class Settings(BaseSettings):
    """Application settings, read from the environment and `.env`."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
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
    DATABASE_URL: str = f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
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
    GEOAPIFY_API_KEY: Optional[str] = None
    GEOAPIFY_PLACES_URL: str = "https://api.geoapify.com/v2/places"
    GEOAPIFY_GEOCODE_URL: str = "https://api.geoapify.com/v1/geocode/search"
    GEOAPIFY_TIMEOUT_SECONDS: int = Field(default=30, ge=1, le=300)
    GEOAPIFY_SEARCH_LIMIT: int = Field(default=20, ge=1, le=500)
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
    LOG_FORMAT: str = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT is Environment.production

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

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
    @field_validator("CORS_ORIGINS", mode="before")
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
    def _known_dialect(cls, value: str) -> str:
        allowed = ("sqlite", "postgresql", "postgres")

        if not value.startswith(allowed):
            raise ValueError(
                f"unsupported database dialect: {value!r}. "
                "Expected a sqlite:// or postgresql:// URL."
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
            # Scanning is the product; booting production without a key would
            # only surface at the first scan, long after deploy.
            if not self.GEOAPIFY_API_KEY:
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

        return self


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
except ValidationError as exc:  # pragma: no cover - startup path
    _fail(exc)
