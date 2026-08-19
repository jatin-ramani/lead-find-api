"""
Deployment assets.

These are contract tests, not integration tests: they assert the properties of
the Dockerfile, the compose file and the migration that are easy to break and
expensive to discover in a deploy. They do not build an image — that needs a
Docker daemon and belongs in CI.

Every assertion here corresponds to a specific way a container deployment goes
wrong; the docstrings say which.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

BACKEND = Path(__file__).resolve().parent.parent

DOCKERFILE = BACKEND / "Dockerfile"
COMPOSE = BACKEND / "docker-compose.yml"
DOCKERIGNORE = BACKEND / ".dockerignore"
ENTRYPOINT = BACKEND / "docker-entrypoint.sh"


@pytest.fixture(scope="module")
def dockerfile():
    return DOCKERFILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entrypoint():
    return ENTRYPOINT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def runtime_stage(dockerfile):
    """
    Only the final stage. The builder and test stages run as root and never
    reach the published image, so their instructions are not the contract.
    """

    return dockerfile.split("AS runtime", 1)[1]


@pytest.fixture(scope="module")
def rendered_sql():
    """
    The migration rendered as PostgreSQL DDL, without a server — as far as
    this can be taken off a Docker host. Applying it is CI's job.
    """

    import os

    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+psycopg://leadfinder:pw@db:5432/leadfinder",
        "ENVIRONMENT": "development",
        "GEOAPIFY_API_KEY": "a-real-looking-key",
        "LEADFINDER_IGNORE_ENV_FILE": "1",
    }

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr

    return result.stdout


class TestFilesExist:
    @pytest.mark.parametrize(
        "path", [DOCKERFILE, COMPOSE, DOCKERIGNORE, ENTRYPOINT]
    )
    def test_present(self, path):
        assert path.is_file(), f"{path.name} is missing"


class TestDockerfileStructure:
    def test_build_is_multi_stage(self, dockerfile):
        """A single-stage build ships pip, the wheel cache and the compilers."""

        stages = re.findall(r"^FROM\s+\S+\s+AS\s+(\w+)", dockerfile, re.M | re.I)

        assert len(stages) >= 2
        assert "builder" in stages
        assert "runtime" in stages

    def test_runtime_is_the_last_stage(self, dockerfile):
        """`docker build` with no --target takes the last stage."""

        stages = re.findall(r"^FROM\s+\S+\s+AS\s+(\w+)", dockerfile, re.M | re.I)

        assert stages[-1] == "runtime"

    def test_the_venv_is_copied_rather_than_rebuilt(self, dockerfile):
        assert "COPY --from=builder /opt/venv /opt/venv" in dockerfile

    def test_a_test_stage_exists_to_gate_a_release(self, dockerfile):
        assert re.search(r"^FROM\s+builder\s+AS\s+test", dockerfile, re.M | re.I)
        assert re.search(r"^RUN\s+pytest", dockerfile, re.M)


class TestNonRoot:
    def test_the_container_does_not_run_as_root(self, dockerfile):
        """
        Root in a container is root on the host if anything escapes, and it
        lets a compromised process write to every mounted volume.
        """

        users = re.findall(r"^USER\s+(\S+)", dockerfile, re.M)

        assert users, "no USER directive — the image would run as root"
        assert users[-1] not in ("root", "0")

    def test_the_user_is_created_with_a_fixed_uid(self, dockerfile):
        """A fixed uid keeps bind-mount ownership predictable across hosts."""

        assert re.search(r"useradd\s+.*--uid\s+\d+", dockerfile)
        assert re.search(r"groupadd\s+.*--gid\s+\d+", dockerfile)

    def test_application_files_are_owned_by_that_user(self, runtime_stage):
        """
        Files copied as root and left that way are unwritable by the app user
        — which only shows up when something tries to write one.
        """

        copies = re.findall(r"^COPY\s+(?!--from)(.*)$", runtime_stage, re.M)

        assert copies, "the runtime stage copies no application files"

        unowned = [c for c in copies if "--chown=app:app" not in c]

        assert unowned == [], f"COPY without --chown in the runtime stage: {unowned}"


class TestHealthcheck:
    def test_the_image_declares_one(self, dockerfile):
        assert "HEALTHCHECK" in dockerfile

    def test_it_probes_the_health_endpoint(self, dockerfile):
        assert "/health" in dockerfile

    def test_it_has_a_start_period(self, dockerfile):
        """
        Without one, the first probes fire during migrations and the
        orchestrator kills the container before it ever comes up.
        """

        assert re.search(r"--start-period=\d+s", dockerfile)

    def test_it_probes_with_the_interpreter_not_an_installed_tool(
        self, runtime_stage
    ):
        """
        The slim base has no curl or wget. Installing one adds bytes and CVE
        surface for something the interpreter already there does for free.
        """

        healthcheck = re.search(
            r"HEALTHCHECK.*?(?=\nENTRYPOINT|\nCMD|\Z)", runtime_stage, re.S
        )

        assert healthcheck
        assert '"python"' in healthcheck.group(0)
        assert not re.search(
            r"apt-get\s+install|apk\s+add", runtime_stage
        ), "the runtime stage installs OS packages — keep them in the builder"


class TestNoSecretsInTheImage:
    SECRET_NAMES = ("GEOAPIFY_API_KEY", "DATABASE_URL", "POSTGRES_PASSWORD")

    @pytest.mark.parametrize("name", SECRET_NAMES)
    def test_no_secret_is_assigned_a_value_at_build_time(self, dockerfile, name):
        """
        An ARG or ENV value is baked into the image history and readable with
        `docker history` forever, even if a later layer unsets it.
        """

        assigned = re.search(
            rf"^\s*(?:ENV|ARG)\s+.*\b{name}\s*=\s*(\S+)", dockerfile, re.M
        )

        assert assigned is None, (
            f"{name} is given a build-time value: {assigned.group(0).strip()!r}"
        )

    def test_the_image_refuses_to_read_a_dotenv_file(self, dockerfile):
        """Belt to .dockerignore's braces: a copied .env must not be honoured."""

        assert "LEADFINDER_IGNORE_ENV_FILE=1" in dockerfile

    def test_the_real_api_key_is_in_none_of_the_assets(self):
        """The live key must never reach a file that gets committed."""

        env_file = BACKEND / ".env"

        if not env_file.exists():
            pytest.skip("no .env on this machine")

        match = re.search(
            r"^GEOAPIFY_API_KEY=(.+)$", env_file.read_text(encoding="utf-8"), re.M
        )

        if not match:
            pytest.skip("no key configured")

        key = match.group(1).strip()

        if len(key) < 8:
            pytest.skip("key too short to search for safely")

        for path in (DOCKERFILE, COMPOSE, DOCKERIGNORE, ENTRYPOINT):
            assert key not in path.read_text(encoding="utf-8"), (
                f"the live API key appears in {path.name}"
            )


class TestDockerignore:
    @pytest.mark.parametrize(
        "pattern,why",
        [
            (".env", "would bake live secrets into the image"),
            ("venv/", "host-built, platform-specific, hundreds of megabytes"),
            ("*.db", "a developer's SQLite file would ship and could be used"),
            (".git/", "carries the entire history, including anything ever committed"),
            ("__pycache__/", "stale bytecode from a different interpreter"),
        ],
    )
    def test_excluded(self, pattern, why):
        rules = {
            line.strip()
            for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        }

        assert pattern in rules, f"{pattern} is not ignored — {why}"

    def test_the_example_env_is_re_included(self):
        rules = DOCKERIGNORE.read_text(encoding="utf-8")

        assert "!.env.example" in rules

    def test_tests_are_available_to_the_test_stage(self):
        """Ignoring tests/ would silently turn `--target test` into a no-op."""

        rules = {
            line.strip()
            for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        }

        assert "tests/" not in rules
        assert "requirements-dev.txt" not in rules


class TestEntrypoint:
    def test_it_has_a_posix_shebang(self, entrypoint):
        assert entrypoint.startswith("#!/bin/sh")

    def test_it_uses_lf_line_endings(self):
        """
        A CRLF script fails with `no such file or directory` naming a file that
        is plainly there — the trailing \\r becomes part of the interpreter
        path. The commonest way a Windows-authored image fails on Linux.
        """

        assert b"\r\n" not in ENTRYPOINT.read_bytes()

    def test_the_shell_accepts_it(self):
        """Catches a syntax error that would otherwise appear only on deploy."""
        import shutil
        if shutil.which("sh") is None:
            pytest.skip("sh executable not available on this platform")

        result = subprocess.run(
            ["sh", "-n", str(ENTRYPOINT)], capture_output=True, text=True
        )

        assert result.returncode == 0, result.stderr

    def test_the_embedded_wait_script_is_valid_python(self, entrypoint):
        import ast

        block = re.search(r"<<'PY'\n(.*?)\nPY\n", entrypoint, re.S)

        assert block, "the wait-for-database heredoc is missing"

        ast.parse(block.group(1))

    def test_the_server_is_exec_ed_so_it_receives_sigterm(self, entrypoint):
        """
        Without exec, the shell stays PID 1, ignores SIGTERM, and every deploy
        waits out the kill timeout instead of draining connections.
        """

        assert re.search(r"^\s*exec uvicorn", entrypoint, re.M)

    def test_it_fails_fast(self, entrypoint):
        """`set -e`: a failed migration must not be followed by a served app."""

        assert re.search(r"^set -eu?", entrypoint, re.M)

    def test_it_runs_migrations_before_serving(self, entrypoint):
        assert "alembic upgrade head" in entrypoint
        assert entrypoint.index("alembic upgrade head") < entrypoint.index(
            "exec uvicorn"
        )

    def test_migrations_can_be_turned_off(self, entrypoint):
        """Multiple replicas should migrate once, as a job, not N times."""

        assert "RUN_MIGRATIONS" in entrypoint

    def test_it_never_prints_the_raw_dsn(self, entrypoint):
        """A container log is collected and often widely readable."""

        assert "safe_database_url" in entrypoint
        assert "settings.database_url" not in entrypoint


class TestCompose:
    def test_it_defines_the_backend_and_a_database(self, compose):
        assert set(compose["services"]) == {"backend", "db"}

    def test_the_database_has_a_persistent_named_volume(self, compose):
        """An anonymous volume is discarded on recreate, taking the data."""

        assert "pgdata" in (compose.get("volumes") or {})
        assert any(
            v.startswith("pgdata:") for v in compose["services"]["db"]["volumes"]
        )

    @pytest.mark.parametrize("service", ["db", "backend"])
    def test_both_services_have_healthchecks(self, compose, service):
        assert "healthcheck" in compose["services"][service]

    def test_the_backend_waits_for_a_healthy_database(self, compose):
        """
        `depends_on` alone only waits for the container to start, not for
        PostgreSQL to accept connections.
        """

        assert compose["services"]["backend"]["depends_on"]["db"][
            "condition"
        ] == "service_healthy"

    def test_the_database_probe_names_the_user_and_database(self, compose):
        """
        Bare `pg_isready` reports ready while the init scripts are still
        running, and the API connects to a half-built database.
        """

        probe = " ".join(compose["services"]["db"]["healthcheck"]["test"])

        assert "pg_isready" in probe
        assert "-U" in probe and "-d" in probe

    def test_the_api_key_has_no_default(self, compose):
        """
        `:?` makes compose refuse to start with a clear message, instead of
        booting a stack whose every scan fails with 502.
        """

        value = compose["services"]["backend"]["environment"]["GEOAPIFY_API_KEY"]

        assert value.startswith("${GEOAPIFY_API_KEY:?")

    def test_no_secret_is_hardcoded(self, compose):
        """Every sensitive value must come from the environment.."""

        env = compose["services"]["backend"]["environment"]

        for name in ("GEOAPIFY_API_KEY", "DATABASE_URL"):
            assert "${" in str(env[name]), f"{name} is a literal in compose"

    def test_the_dsn_points_at_the_compose_service(self, compose):
        dsn = compose["services"]["backend"]["environment"]["DATABASE_URL"]

        assert dsn.startswith("postgresql+psycopg://"), (
            "the driver must match the one in requirements.txt"
        )
        assert "@db:5432/" in dsn

    def test_the_obsolete_version_key_is_absent(self, compose):
        assert "version" not in compose


class TestPostgresSupportIsActuallyShipped:
    def test_a_driver_is_in_requirements(self):
        """
        Regression guard. Production config *requires* PostgreSQL, but no
        driver was pinned — so production could never start at all. It failed
        at import with ModuleNotFoundError: No module named 'psycopg'.
        """

        text = (BACKEND / "requirements.txt").read_text(encoding="utf-8")

        assert re.search(r"^psycopg(\[binary\])?==", text, re.M), (
            "no PostgreSQL driver pinned"
        )

    def test_the_driver_is_importable(self):
        import psycopg  # noqa: F401

    def test_sqlalchemy_can_build_a_postgres_engine(self):
        from sqlalchemy import create_engine

        engine = create_engine("postgresql+psycopg://u:p@h:5432/d")

        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"

    def test_postgres_gets_pool_recycling_and_sqlite_does_not(self, monkeypatch):
        """
        A container sits behind a database or a proxy that closes idle
        connections. Without pre-ping the first request after an idle period
        gets a dead socket; without recycling it keeps getting them.
        """

        from pydantic import SecretStr

        from config import settings
        from database.db import _engine_options

        monkeypatch.setattr(
            settings, "DATABASE_URL", SecretStr("postgresql+psycopg://u:p@h/d")
        )
        options = _engine_options()

        assert options["pool_pre_ping"] is True
        assert options["pool_recycle"] == settings.DATABASE_POOL_RECYCLE_SECONDS
        assert "connect_args" not in options

        monkeypatch.setattr(settings, "DATABASE_URL", SecretStr("sqlite:///./x.db"))
        options = _engine_options()

        # SQLite instead needs cross-thread access: FastAPI hands requests to a
        # threadpool and background jobs run on their own threads.
        assert options["connect_args"] == {"check_same_thread": False}
        assert "pool_pre_ping" not in options

    def test_the_app_boots_with_a_production_postgres_configuration(self):
        """
        The whole import path — config validation, engine construction, router
        registration — against the exact settings the compose stack uses. This
        is what failed outright before the driver was added.
        """

        import os

        env = {
            **os.environ,
            "ENVIRONMENT": "production",
            "ADMIN_SECRET_KEY": "production-secret-not-default-1234567890",
            "DATABASE_URL": "postgresql+psycopg://leadfinder:pw@db:5432/leadfinder",
            "GEOAPIFY_API_KEY": "a-real-looking-key",
            "CORS_ORIGINS": "https://app.example.com",
            "LEADFINDER_IGNORE_ENV_FILE": "1",
        }

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app import app;"
                "from database.db import engine;"
                "schema = app.openapi();"
                "print(engine.dialect.name, engine.dialect.driver,"
                " len(schema['paths']), app.docs_url)",
            ],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr

        dialect, driver, paths, docs = result.stdout.split()

        assert (dialect, driver) == ("postgresql", "psycopg")
        assert int(paths) == 23, "the API surface differs under PostgreSQL"
        assert docs == "None", "docs must stay hidden in production"


class TestMigrationsTargetPostgres:
    """
    Renders the migration as PostgreSQL DDL without a server, which is as far
    as this can be taken off a Docker host. Applying it is CI's job.
    """

    def test_offline_rendering_works_at_all(self, rendered_sql):
        """
        The baseline migration inspects the database to stay idempotent, and
        offline mode has no connection to inspect. Without the guard this
        command dies on `sa.inspect(None)` — and it is the documented way to
        get DDL reviewed before a production change.
        """

        assert "CREATE TABLE" in rendered_sql

    @pytest.mark.parametrize(
        "table", ["businesses", "scan_jobs", "scrape_jobs", "website_data"]
    )
    def test_every_table_is_created(self, rendered_sql, table):
        assert f"CREATE TABLE {table}" in rendered_sql

    def test_identity_columns_use_the_postgres_form(self, rendered_sql):
        assert "SERIAL" in rendered_sql
        assert "AUTOINCREMENT" not in rendered_sql.upper(), "SQLite-only syntax"

    def test_the_cascade_is_preserved(self, rendered_sql):
        """
        website_data rows must go with their business. On SQLite the ORM does
        this; on PostgreSQL the constraint has to.
        """

        assert "REFERENCES businesses (id) ON DELETE CASCADE" in rendered_sql

    def test_it_is_one_transaction(self, rendered_sql):
        """PostgreSQL has transactional DDL: a failure must leave nothing."""

        assert "BEGIN;" in rendered_sql
        assert "COMMIT;" in rendered_sql

    def test_the_version_is_stamped(self, rendered_sql):
        assert "INSERT INTO alembic_version" in rendered_sql
