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

        assert revision == "0023"
        assert "admin_sessions" in inspector.get_table_names()
        assert "business_follow_ups" in inspector.get_table_names()
        assert "email_automations" in inspector.get_table_names()
        assert "email_automation_executions" in inspector.get_table_names()
        assert "email_templates" in inspector.get_table_names()
        assert "email_campaigns" in inspector.get_table_names()
        assert "email_campaign_recipients" in inspector.get_table_names()
        assert "gmail_oauth_credentials" in inspector.get_table_names()
        assert "scan_search_units" in inspector.get_table_names()

        # Check 0022 columns on scan_jobs
        scan_job_cols = [c["name"] for c in inspector.get_columns("scan_jobs")]
        assert "category_family" in scan_job_cols
        assert "scan_radius_km" in scan_job_cols
        assert "total_search_units" in scan_job_cols
        assert "businesses_stored" in scan_job_cols
        assert "businesses_skipped_no_contact" in scan_job_cols

        # Check 0019 columns
        assert "next_attempt_at" in [c["name"] for c in inspector.get_columns("email_campaign_recipients")]
        assert "paused_reason" in [c["name"] for c in inspector.get_columns("email_campaigns")]

        # Verify default templates were seeded by migration 0017
        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT COUNT(*) FROM email_templates WHERE name LIKE 'Grade %'")
            ).scalar_one()
            assert count == 4

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



def test_migration_0016_downgrade_and_reupgrade(monkeypatch):
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr
    from sqlalchemy import create_engine, inspect, text

    database_path = migrations.BACKEND_DIR / "downgrade_0016_test.db"
    database_path.unlink(missing_ok=True)
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = None

    try:
        monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
        monkeypatch.setattr(settings, "RUN_MIGRATIONS", True)
        monkeypatch.setattr(settings, "DATABASE_URL", SecretStr(database_url))

        # 1. Upgrade to 0017 (head)
        migrations.run_startup_migrations()

        engine = create_engine(database_url)
        inspector = inspect(engine)
        assert "gmail_oauth_credentials" in inspector.get_table_names()

        # 2. Downgrade from 0017 to 0015
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)
        command.downgrade(alembic_cfg, "0015")

        inspector = inspect(engine)
        assert "gmail_oauth_credentials" not in inspector.get_table_names()
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0015"

        # 3. Re-upgrade from 0015 to 0017
        command.upgrade(alembic_cfg, "0017")
        inspector = inspect(engine)
        assert "gmail_oauth_credentials" in inspector.get_table_names()
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0017"

    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)


def test_migration_0017_downgrade_and_reupgrade(monkeypatch):
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr
    from sqlalchemy import create_engine, text

    database_path = migrations.BACKEND_DIR / "downgrade_0017_test.db"
    database_path.unlink(missing_ok=True)
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = None

    try:
        monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
        monkeypatch.setattr(settings, "RUN_MIGRATIONS", True)
        monkeypatch.setattr(settings, "DATABASE_URL", SecretStr(database_url))

        migrations.run_startup_migrations()

        engine = create_engine(database_url)
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM email_templates WHERE name LIKE 'Grade %'")
            ).scalar_one()
            assert count == 4

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

        # Downgrade to 0016
        command.downgrade(alembic_cfg, "0016")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0016"
            count = conn.execute(
                text("SELECT COUNT(*) FROM email_templates WHERE name LIKE 'Grade %'")
            ).scalar_one()
            assert count == 0

        # Re-upgrade to 0017
        command.upgrade(alembic_cfg, "0017")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0017"
            count = conn.execute(
                text("SELECT COUNT(*) FROM email_templates WHERE name LIKE 'Grade %'")
            ).scalar_one()
            assert count == 4

    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)


def test_migration_0009_postgres_boolean_default_syntax(monkeypatch, capsys):
    """
    Verify migration 0009 generates valid PostgreSQL boolean default DDL.
    PostgreSQL requires 'DEFAULT false', not 'DEFAULT 0'.
    """
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr

    monkeypatch.setattr(
        settings, "DATABASE_URL", SecretStr("postgresql+psycopg://user:pass@localhost:5432/leadfinder")
    )

    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://user:pass@localhost:5432/leadfinder")

    # Render offline migration sql 0008 -> 0009
    command.upgrade(alembic_cfg, "0008:0009", sql=True)
    captured = capsys.readouterr()
    sql_output = captured.out

    # Must contain "DEFAULT false" or "DEFAULT FALSE", and NOT "DEFAULT 0"
    assert "default false" in sql_output.lower()
    assert "default 0" not in sql_output.lower()


def test_upgrade_from_0007_preserves_data_and_sets_is_favorite_false(monkeypatch):
    """
    Simulate upgrading a production database with existing records from 0007 to head (0019).
    Verify:
    1. Existing business records remain intact.
    2. is_favorite defaults to False / 0 without data loss.
    """
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr
    from sqlalchemy import create_engine, text

    database_path = migrations.BACKEND_DIR / "upgrade_0007_test.db"
    database_path.unlink(missing_ok=True)
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = None

    try:
        monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
        monkeypatch.setattr(settings, "RUN_MIGRATIONS", False)
        monkeypatch.setattr(settings, "DATABASE_URL", SecretStr(database_url))

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

        # 1. Upgrade to 0007 (before is_favorite was introduced in 0009)
        command.upgrade(alembic_cfg, "0007")

        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO businesses (name, city, category, status, place_id, lead_score, lead_grade) "
                    "VALUES ('Acme Plumbing', 'San Francisco', 'plumber', 'scraped', 'place_acme_1', 85, 'A')"
                )
            )

        # 2. Upgrade from 0007 all the way to head (0022)
        command.upgrade(alembic_cfg, "0022")

        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0022"

            row = conn.execute(
                text("SELECT name, is_favorite, lead_score, lead_grade FROM businesses WHERE place_id = 'place_acme_1'")
            ).mappings().one()

            assert row["name"] == "Acme Plumbing"
            assert row["lead_score"] == 85
            assert row["lead_grade"] == "A"
            assert row["is_favorite"] is False or row["is_favorite"] == 0

    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)


def test_migration_0022_downgrade_and_reupgrade(monkeypatch):
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr
    from sqlalchemy import create_engine, inspect, text

    database_path = migrations.BACKEND_DIR / "downgrade_0022_test.db"
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
        assert "scan_search_units" in inspector.get_table_names()

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

        # Downgrade to 0021
        command.downgrade(alembic_cfg, "0021")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0021"

        inspector = inspect(engine)
        assert "scan_search_units" not in inspector.get_table_names()

        # Re-upgrade to 0022
        command.upgrade(alembic_cfg, "0022")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0022"

        inspector = inspect(engine)
        assert "scan_search_units" in inspector.get_table_names()

    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)



def test_migration_0018_downgrade_and_reupgrade(monkeypatch):
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr
    from sqlalchemy import create_engine, text

    database_path = migrations.BACKEND_DIR / "downgrade_0018_test.db"
    database_path.unlink(missing_ok=True)
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = None

    try:
        monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
        monkeypatch.setattr(settings, "RUN_MIGRATIONS", True)
        monkeypatch.setattr(settings, "DATABASE_URL", SecretStr(database_url))

        migrations.run_startup_migrations()

        engine = create_engine(database_url)
        with engine.connect() as conn:
            grade_a = conn.execute(
                text("SELECT subject FROM email_templates WHERE name = 'Grade A — High Priority Lead'")
            ).scalar_one()
            assert grade_a == "Introduction regarding {{business_name}}"

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

        # Downgrade to 0017
        command.downgrade(alembic_cfg, "0017")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0017"
            grade_a_old = conn.execute(
                text("SELECT subject FROM email_templates WHERE name = 'Grade A — High Priority Lead'")
            ).scalar_one()
            assert grade_a_old == "Partnership opportunity for {{business_name}}"

        # Re-upgrade to 0018
        command.upgrade(alembic_cfg, "0018")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0018"
            grade_a_new = conn.execute(
                text("SELECT subject FROM email_templates WHERE name = 'Grade A — High Priority Lead'")
            ).scalar_one()
            assert grade_a_new == "Introduction regarding {{business_name}}"

    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)


def test_migration_0019_downgrade_and_reupgrade(monkeypatch):
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr
    from sqlalchemy import create_engine, inspect, text

    database_path = migrations.BACKEND_DIR / "downgrade_0019_test.db"
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
        assert "next_attempt_at" in [c["name"] for c in inspector.get_columns("email_campaign_recipients")]
        assert "paused_reason" in [c["name"] for c in inspector.get_columns("email_campaigns")]

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

        # Downgrade to 0018
        command.downgrade(alembic_cfg, "0018")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0018"

        inspector = inspect(engine)
        assert "next_attempt_at" not in [c["name"] for c in inspector.get_columns("email_campaign_recipients")]
        assert "paused_reason" not in [c["name"] for c in inspector.get_columns("email_campaigns")]

        # Re-upgrade to 0019
        command.upgrade(alembic_cfg, "0019")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0019"

        inspector = inspect(engine)
        assert "next_attempt_at" in [c["name"] for c in inspector.get_columns("email_campaign_recipients")]
        assert "paused_reason" in [c["name"] for c in inspector.get_columns("email_campaigns")]

    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)


def test_migration_0020_downgrade_and_reupgrade(monkeypatch):
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr
    from sqlalchemy import create_engine, inspect, text

    database_path = migrations.BACKEND_DIR / "downgrade_0020_test.db"
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
        recip_indexes = [idx["name"] for idx in inspector.get_indexes("email_campaign_recipients")]
        assert "ix_recipient_business_status" in recip_indexes

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

        # Downgrade to 0019
        command.downgrade(alembic_cfg, "0019")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0019"

        inspector = inspect(engine)
        recip_indexes = [idx["name"] for idx in inspector.get_indexes("email_campaign_recipients")]
        assert "ix_recipient_business_status" not in recip_indexes

        # Re-upgrade to 0020
        command.upgrade(alembic_cfg, "0020")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0020"

        inspector = inspect(engine)
        recip_indexes = [idx["name"] for idx in inspector.get_indexes("email_campaign_recipients")]
        assert "ix_recipient_business_status" in recip_indexes

    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)


def test_migration_0021_downgrade_and_reupgrade(monkeypatch):
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr
    from sqlalchemy import create_engine, text

    database_path = migrations.BACKEND_DIR / "downgrade_0021_test.db"
    database_path.unlink(missing_ok=True)
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = None

    try:
        monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
        monkeypatch.setattr(settings, "RUN_MIGRATIONS", True)
        monkeypatch.setattr(settings, "DATABASE_URL", SecretStr(database_url))

        migrations.run_startup_migrations()

        engine = create_engine(database_url)
        with engine.connect() as conn:
            univ_body = conn.execute(
                text("SELECT body FROM email_templates WHERE name LIKE 'Universal Master Cold Email%'")
            ).scalar_one_or_none()
            if univ_body:
                assert "<p>" in univ_body
                assert "<strong>Codebait</strong>" in univ_body or "<strong>look more credible" in univ_body

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

        # Downgrade to 0020
        command.downgrade(alembic_cfg, "0020")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0020"

        # Re-upgrade to 0021
        command.upgrade(alembic_cfg, "0021")
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert rev == "0021"

    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)


def test_migration_0021_preserves_custom_templates_and_upgrades_legacy_plain_text(monkeypatch):
    """
    Verify migration 0021:
    1. Repairs legacy unhardened Universal Master template that lacks <p> tags.
    2. Strictly PRESERVES intentionally customized user templates (does not overwrite custom copy).
    """
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr
    from sqlalchemy import create_engine, text

    database_path = migrations.BACKEND_DIR / "custom_template_0021_test.db"
    database_path.unlink(missing_ok=True)
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = None

    try:
        monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
        monkeypatch.setattr(settings, "RUN_MIGRATIONS", False)
        monkeypatch.setattr(settings, "DATABASE_URL", SecretStr(database_url))

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

        # 1. Upgrade to 0020
        command.upgrade(alembic_cfg, "0020")

        engine = create_engine(database_url)
        with engine.begin() as conn:
            # Insert a legacy plain-text Universal Master template
            conn.execute(
                text(
                    "INSERT INTO email_templates (name, description, subject, body, is_archived, created_at, updated_at) "
                    "VALUES ('Universal Master Cold Email — Website Mockup', 'Legacy', 'Legacy Subject', 'Plain text without html tags', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            # Insert a custom user template
            conn.execute(
                text(
                    "INSERT INTO email_templates (name, description, subject, body, is_archived, created_at, updated_at) "
                    "VALUES ('Custom Agency Pitch', 'User created', 'Custom Subject for {{business_name}}', 'Custom body content crafted by user', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        # 2. Upgrade to 0021
        command.upgrade(alembic_cfg, "0021")

        with engine.connect() as conn:
            # Universal master template was upgraded to HTML
            univ_row = conn.execute(
                text("SELECT subject, body FROM email_templates WHERE name = 'Universal Master Cold Email — Website Mockup'")
            ).mappings().one()
            assert "<p>" in univ_row["body"]
            assert "<strong>Codebait</strong>" in univ_row["body"]
            assert univ_row["subject"] == "A free website mockup for {{business_name}}?"

            # Custom user template was NOT touched/overwritten
            custom_row = conn.execute(
                text("SELECT subject, body FROM email_templates WHERE name = 'Custom Agency Pitch'")
            ).mappings().one()
            assert custom_row["subject"] == "Custom Subject for {{business_name}}"
            assert custom_row["body"] == "Custom body content crafted by user"

    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)


def test_migration_0023_updates_universal_master_template_preserving_custom_templates(monkeypatch):
    """
    Verify migration 0023:
    1. Updates Universal Master Cold Email templates to the new modern web & AI content.
    2. Strictly PRESERVES intentionally customized user templates (does not overwrite custom copy).
    """
    from alembic.config import Config
    from alembic import command
    from pydantic import SecretStr
    from sqlalchemy import create_engine, text

    database_path = migrations.BACKEND_DIR / "custom_template_0023_test.db"
    database_path.unlink(missing_ok=True)
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = None

    try:
        monkeypatch.setattr(settings, "ENVIRONMENT", Environment.production)
        monkeypatch.setattr(settings, "RUN_MIGRATIONS", False)
        monkeypatch.setattr(settings, "DATABASE_URL", SecretStr(database_url))

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(migrations.BACKEND_DIR / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

        # 1. Upgrade to 0022
        command.upgrade(alembic_cfg, "0022")

        engine = create_engine(database_url)
        with engine.begin() as conn:
            # Insert the previous Universal Master template
            conn.execute(
                text(
                    "INSERT INTO email_templates (name, description, subject, body, is_archived, created_at, updated_at) "
                    "VALUES ('Universal Master Cold Email — Website Mockup', 'Mockup offer', 'A free website mockup for {{business_name}}?', '<p>Hi {{business_name}} team,</p><p>mockup...</p>', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            # Insert a custom user template
            conn.execute(
                text(
                    "INSERT INTO email_templates (name, description, subject, body, is_archived, created_at, updated_at) "
                    "VALUES ('My Custom Sales Pitch', 'User created', 'Custom Subject for {{business_name}}', 'Custom body content crafted by user', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        # 2. Upgrade to 0023
        command.upgrade(alembic_cfg, "0023")

        with engine.connect() as conn:
            # Universal master template was updated to new approved content
            univ_row = conn.execute(
                text("SELECT subject, body FROM email_templates WHERE name = 'Universal Master Cold Email'")
            ).mappings().one()
            assert univ_row["subject"] == "Quick idea for {{Business Name}}"
            assert "Hi {{Contact Name}}," in univ_row["body"]
            assert "I came across {{Business Name}} in {{City}}." in univ_row["body"]
            assert "look more credible, capture more leads and turn visitors into customers." in univ_row["body"]
            assert "Jatin Ramani" in univ_row["body"]
            assert "7861035002" in univ_row["body"]

            # Custom user template was NOT touched/overwritten
            custom_row = conn.execute(
                text("SELECT subject, body FROM email_templates WHERE name = 'My Custom Sales Pitch'")
            ).mappings().one()
            assert custom_row["subject"] == "Custom Subject for {{business_name}}"
            assert custom_row["body"] == "Custom body content crafted by user"

    finally:
        if engine is not None:
            engine.dispose()
        database_path.unlink(missing_ok=True)