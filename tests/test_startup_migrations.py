"""Production startup migration enforcement."""

import pytest

from config import Environment, settings
from database import migrations


def test_non_production_startup_does_not_run_alembic(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", Environment.testing)
    monkeypatch.setattr(settings, "RUN_MIGRATIONS", True)
    called = False

    def unexpected_upgrade(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(migrations.command, "upgrade", unexpected_upgrade)

    migrations.run_startup_migrations()

    assert called is False


def test_production_startup_upgrades_to_head(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
    monkeypatch.setattr(settings, "RUN_MIGRATIONS", True)
    calls = []

    monkeypatch.setattr(
        migrations.command,
        "upgrade",
        lambda config, revision: calls.append((config, revision)),
    )

    migrations.run_startup_migrations()

    assert len(calls) == 1
    assert calls[0][1] == "head"
    assert calls[0][0].get_main_option("script_location") == str(
        migrations.BACKEND_DIR / "alembic"
    )


def test_migrations_can_be_disabled_after_an_external_upgrade(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
    monkeypatch.setattr(settings, "RUN_MIGRATIONS", False)
    monkeypatch.setattr(
        migrations.command,
        "upgrade",
        lambda *_args, **_kwargs: pytest.fail("Alembic ran twice"),
    )

    migrations.run_startup_migrations()


def test_migration_failure_aborts_startup(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
    monkeypatch.setattr(settings, "RUN_MIGRATIONS", True)

    def fail(*_args, **_kwargs):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(migrations.command, "upgrade", fail)

    with pytest.raises(RuntimeError, match="migration failed"):
        migrations.run_startup_migrations()

def test_real_startup_upgrade_reaches_session_schema(monkeypatch):
    from pydantic import SecretStr
    from sqlalchemy import create_engine, inspect, text

    database_path = migrations.BACKEND_DIR / "startup_migration_test.db"
    database_path.unlink(missing_ok=True)
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = None

    try:
        monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
        monkeypatch.setattr(settings, "RUN_MIGRATIONS", True)
        monkeypatch.setattr(settings, "DATABASE_URL", SecretStr(database_url))

        migrations.run_startup_migrations()

        engine = create_engine(database_url)
        inspector = inspect(engine)

        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()

        assert revision == "0014"
        assert "admin_sessions" in inspector.get_table_names()
        assert "business_follow_ups" in inspector.get_table_names()
        assert "email_automations" in inspector.get_table_names()
        assert "email_automation_executions" in inspector.get_table_names()
        assert inspector.get_pk_constraint("admin_sessions")["constrained_columns"] == [
            "token_hash"
        ]
        assert "ix_admin_sessions_expires_at" in {
            index["name"] for index in inspector.get_indexes("admin_sessions")
        }
        follow_up_indexes = {
            index["name"] for index in inspector.get_indexes("business_follow_ups")
        }
        assert "ix_business_follow_ups_biz_due" in follow_up_indexes
        assert "ix_business_follow_ups_biz_status" in follow_up_indexes

        exec_indexes = {
            index["name"] for index in inspector.get_indexes("email_automation_executions")
        }
        assert "ix_email_automation_executions_trigger_key" in exec_indexes
    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)


def test_migration_0014_downgrade_and_reupgrade(monkeypatch):
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr
    from sqlalchemy import create_engine, inspect, text

    database_path = migrations.BACKEND_DIR / "downgrade_0014_test.db"
    database_path.unlink(missing_ok=True)
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = None

    try:
        monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
        monkeypatch.setattr(settings, "RUN_MIGRATIONS", True)
        monkeypatch.setattr(settings, "DATABASE_URL", SecretStr(database_url))

        # 1. Upgrade to 0014 (head)
        migrations.run_startup_migrations()

        engine = create_engine(database_url)
        inspector = inspect(engine)
        assert "email_automations" in inspector.get_table_names()
        assert "email_automation_executions" in inspector.get_table_names()

        # 2. Downgrade from 0014 to 0013
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)
        command.downgrade(alembic_cfg, "0013")

        inspector = inspect(engine)
        assert "email_automations" not in inspector.get_table_names()
        assert "email_automation_executions" not in inspector.get_table_names()
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0013"

        # 3. Re-upgrade from 0013 to 0014
        command.upgrade(alembic_cfg, "0014")
        inspector = inspect(engine)
        assert "email_automations" in inspector.get_table_names()
        assert "email_automation_executions" in inspector.get_table_names()
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0014"

    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)