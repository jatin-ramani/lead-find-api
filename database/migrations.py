"""Fail-closed database migration enforcement for production startup."""

import logging

from alembic import command
from alembic.config import Config as AlembicConfig

from config import BACKEND_DIR, settings

logger = logging.getLogger(__name__)


def run_startup_migrations() -> None:
    """Upgrade the configured production database before requests are served."""

    if not settings.is_production or not settings.RUN_MIGRATIONS:
        return

    logger.info("applying database migrations through Alembic head")

    config = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")

    logger.info("database migrations are at Alembic head")
