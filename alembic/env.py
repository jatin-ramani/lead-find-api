from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# The application's own settings are the single source of truth for the
# connection URL, so `alembic upgrade` can never target a different database
# than the running app.
from config import settings
from database.db import Base

# Importing the models registers every table on Base.metadata. Without this
# line autogenerate sees an empty schema and would happily emit a migration
# that drops all four tables.
from database import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option(
    "sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%")
)

target_metadata = Base.metadata


def _common_options() -> dict:
    return {
        "target_metadata": target_metadata,
        # Pick up column type changes, not just added/removed columns.
        "compare_type": True,
        "compare_server_default": True,
        # SQLite cannot ALTER a column. Batch mode rewrites the table instead,
        # so the same migration script runs on SQLite and PostgreSQL alike.
        "render_as_batch": settings.is_sqlite,
    }


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it — `alembic upgrade head --sql`."""

    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_common_options(),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, **_common_options())

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
