"""Configuration validation — the fail-fast contract."""

import pytest
from pydantic import ValidationError

from config import Environment, Settings

# Variables conftest sets for the suite. Config tests need a clean slate so
# they are asserting against declared defaults, not the harness environment.
_HARNESS_VARS = (
    "DATABASE_URL",
    "ENVIRONMENT",
    "GEOAPIFY_API_KEY",
    "LOG_LEVEL",
    "DEBUG",
    "CORS_ORIGINS",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in _HARNESS_VARS:
        monkeypatch.delenv(name, raising=False)

    return monkeypatch


def build(clean_env, **overrides) -> Settings:
    """Construct Settings from explicit values only, ignoring the .env file."""

    for key, value in overrides.items():
        clean_env.setenv(key, str(value))

    return Settings(_env_file=None)


class TestDefaults:
    def test_defaults_reproduce_historical_behaviour(self, clean_env):
        settings = build(clean_env)

        assert settings.ENVIRONMENT is Environment.development
        assert settings.DEBUG is False
        assert settings.GEOAPIFY_TIMEOUT_SECONDS == 30
        assert settings.GEOAPIFY_SEARCH_LIMIT == 500
        assert settings.GEOAPIFY_SEARCH_RADIUS_METRES == 5000
        assert settings.SCRAPER_TIMEOUT_SECONDS == 20
        assert settings.SCRAPER_MAX_RESPONSE_BYTES == 5 * 1024 * 1024
        assert settings.DEFAULT_PAGE_SIZE == 20
        assert settings.MAX_PAGE_SIZE == 100
        assert settings.LOG_LEVEL == "INFO"

    def test_database_defaults_to_absolute_sqlite_path(self, clean_env):
        settings = build(clean_env)

        assert settings.database_url.startswith("sqlite:///")
        assert settings.is_sqlite is True
        # Absolute, so the file does not move with the working directory.
        assert "/backend/database/leadfinder.db" in settings.database_url

    def test_default_cors_origins(self, clean_env):
        settings = build(clean_env)

        assert settings.CORS_ORIGINS == [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]


class TestCorsParsing:
    def test_comma_separated(self, clean_env):
        settings = build(
            clean_env, CORS_ORIGINS="https://a.test,https://b.test"
        )

        assert settings.CORS_ORIGINS == ["https://a.test", "https://b.test"]

    def test_json_array(self, clean_env):
        settings = build(
            clean_env, CORS_ORIGINS='["https://a.test","https://b.test"]'
        )

        assert settings.CORS_ORIGINS == ["https://a.test", "https://b.test"]

    def test_single_value(self, clean_env):
        settings = build(clean_env, CORS_ORIGINS="https://only.test")

        assert settings.CORS_ORIGINS == ["https://only.test"]

    def test_whitespace_is_trimmed_and_blanks_dropped(self, clean_env):
        settings = build(clean_env, CORS_ORIGINS=" https://a.test , ,https://b.test ")

        assert settings.CORS_ORIGINS == ["https://a.test", "https://b.test"]


class TestFieldValidation:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("LOG_LEVEL", "CHATTY"),
            ("ENVIRONMENT", "staging"),
            ("DEBUG", "maybe"),
            ("SCRAPER_TIMEOUT_SECONDS", 0),
            ("SCRAPER_TIMEOUT_SECONDS", 9999),
            ("GEOAPIFY_TIMEOUT_SECONDS", 0),
            ("GEOAPIFY_SEARCH_LIMIT", 0),
            ("GEOAPIFY_SEARCH_LIMIT", 9999),
            ("GEOAPIFY_SEARCH_RADIUS_METRES", 10),
            ("DEFAULT_PAGE_SIZE", 0),
            ("MAX_PAGE_SIZE", 0),
            ("SCRAPER_MAX_RESPONSE_BYTES", 10),
        ],
    )
    def test_out_of_range_values_are_rejected(self, clean_env, field, value):
        with pytest.raises(ValidationError) as exc:
            build(clean_env, **{field: value})

        assert field in str(exc.value)

    @pytest.mark.parametrize(
        "url",
        ["mysql://u:p@h/d", "oracle://u:p@h/d", "not-a-url", "redis://localhost"],
    )
    def test_unsupported_database_dialect_rejected(self, clean_env, url):
        with pytest.raises(ValidationError) as exc:
            build(clean_env, DATABASE_URL=url)

        assert "unsupported database dialect" in str(exc.value)

    @pytest.mark.parametrize(
        "url",
        [
            "sqlite:///./x.db",
            "postgresql://u:p@h/d",
            "postgresql+psycopg://u:p@h:5432/d",
        ],
    )
    def test_supported_dialects_accepted(self, clean_env, url):
        assert build(clean_env, DATABASE_URL=url).database_url == url

    def test_max_page_size_below_default_rejected(self, clean_env):
        with pytest.raises(ValidationError) as exc:
            build(clean_env, DEFAULT_PAGE_SIZE=50, MAX_PAGE_SIZE=10)

        assert "MAX_PAGE_SIZE" in str(exc.value)

    def test_equal_page_sizes_allowed(self, clean_env):
        settings = build(clean_env, DEFAULT_PAGE_SIZE=25, MAX_PAGE_SIZE=25)

        assert settings.MAX_PAGE_SIZE == 25


class TestProductionRules:
    """Production adds constraints that must stop the process, not warn."""

    def _production(self, clean_env, **overrides):
        base = {
            "ENVIRONMENT": "production",
            "GEOAPIFY_API_KEY": "key",
            "DATABASE_URL": "postgresql://u:p@h/d",
            "CORS_ORIGINS": "https://app.test",
        }
        base.update(overrides)

        return build(clean_env, **base)

    def test_valid_production_config_builds(self, clean_env):
        settings = self._production(clean_env)

        assert settings.is_production is True
        assert settings.is_sqlite is False

    def test_missing_api_key_rejected(self, clean_env):
        with pytest.raises(ValidationError) as exc:
            self._production(clean_env, GEOAPIFY_API_KEY="")

        assert "GEOAPIFY_API_KEY is required" in str(exc.value)

    def test_sqlite_rejected(self, clean_env):
        with pytest.raises(ValidationError) as exc:
            self._production(clean_env, DATABASE_URL="sqlite:///./x.db")

        assert "SQLite is not supported in production" in str(exc.value)

    def test_wildcard_cors_rejected(self, clean_env):
        with pytest.raises(ValidationError) as exc:
            self._production(clean_env, CORS_ORIGINS="*")

        assert "CORS_ORIGINS may not be" in str(exc.value)

    def test_docs_hidden_in_production(self, clean_env):
        settings = self._production(clean_env)

        assert settings.docs_url is None
        assert settings.redoc_url is None

    def test_docs_visible_in_production_when_debug_on(self, clean_env):
        settings = self._production(clean_env, DEBUG="true")

        assert settings.docs_url == "/docs"
        assert settings.redoc_url == "/redoc"

    def test_docs_visible_in_development(self, clean_env):
        settings = build(clean_env)

        assert settings.docs_url == "/docs"

    @pytest.mark.parametrize("env", ["development", "testing"])
    def test_non_production_tolerates_missing_key_and_sqlite(self, clean_env, env):
        """Developers must not need a real key or PostgreSQL to run locally."""

        settings = build(
            clean_env,
            ENVIRONMENT=env,
            DATABASE_URL="sqlite:///./x.db",
        )

        assert settings.GEOAPIFY_API_KEY is None
        assert settings.is_production is False

    def test_http_geoapify_urls_rejected_in_production(self, clean_env):
        """The key rides in the query string; http would put it in cleartext."""

        with pytest.raises(ValidationError) as exc:
            self._production(
                clean_env,
                GEOAPIFY_PLACES_URL="http://api.geoapify.com/v2/places",
            )

        assert "must use https in production" in str(exc.value)

    def test_http_geoapify_urls_allowed_outside_production(self, clean_env):
        """Local stubs and recorded fixtures are served over http."""

        settings = build(
            clean_env, GEOAPIFY_PLACES_URL="http://127.0.0.1:8299/places"
        )

        assert settings.GEOAPIFY_PLACES_URL.startswith("http://")


class TestCorsOriginFormat:
    @pytest.mark.parametrize(
        "value",
        ["localhost:3000", "app.test", "//app.test", "ftp://app.test"],
    )
    def test_an_origin_without_a_scheme_is_rejected(self, clean_env, value):
        with pytest.raises(ValidationError) as exc:
            build(clean_env, CORS_ORIGINS=value)

        assert "must start with http:// or https://" in str(exc.value)

    def test_an_origin_with_a_path_is_rejected(self, clean_env):
        """
        A path silently matches nothing: the browser blocks the frontend and
        the server logs look perfectly healthy.
        """

        with pytest.raises(ValidationError) as exc:
            build(clean_env, CORS_ORIGINS="https://app.test/api")

        assert "must not contain a path" in str(exc.value)

    @pytest.mark.parametrize(
        "value",
        [
            "https://app.test",
            "http://localhost:3000",
            "https://app.test/",
            "https://a.test,https://b.test",
        ],
    )
    def test_well_formed_origins_are_accepted(self, clean_env, value):
        assert build(clean_env, CORS_ORIGINS=value).CORS_ORIGINS

    def test_wildcard_still_allowed_outside_production(self, clean_env):
        assert build(clean_env, CORS_ORIGINS="*").CORS_ORIGINS == ["*"]


class TestConfigurationWarnings:
    """Legal but dangerous. Logged at startup rather than rejected."""

    def test_debug_in_production_is_flagged(self, clean_env):
        settings = build(
            clean_env,
            ENVIRONMENT="production",
            DEBUG="true",
            GEOAPIFY_API_KEY="real-key",
            DATABASE_URL="postgresql://u:p@h/d",
            CORS_ORIGINS="https://app.test",
        )

        warnings = " ".join(settings.configuration_warnings())

        assert "DEBUG=true in production" in warnings

    def test_missing_key_is_flagged(self, clean_env):
        warnings = " ".join(build(clean_env).configuration_warnings())

        assert "GEOAPIFY_API_KEY is not set" in warnings

    def test_sql_echo_is_flagged(self, clean_env):
        """Bound parameters include whatever a user submitted."""

        settings = build(clean_env, DATABASE_ECHO="true")

        assert any(
            "DATABASE_ECHO" in w for w in settings.configuration_warnings()
        )

    def test_a_sane_development_config_warns_only_about_the_key(self, clean_env):
        warnings = build(clean_env, GEOAPIFY_API_KEY="real-key")

        assert warnings.configuration_warnings() == []

    def test_warnings_never_contain_a_secret(self, clean_env):
        settings = build(
            clean_env, GEOAPIFY_API_KEY=SECRET_KEY, DATABASE_URL=SECRET_DSN,
            DATABASE_ECHO="true",
        )

        text = " ".join(settings.configuration_warnings())

        assert SECRET_KEY not in text
        assert "hunter2" not in text


class TestStartupValidation:
    """
    Validation must happen at import, before a port is bound.

    Run in a subprocess because that is the only honest way to observe a
    process that is supposed to die: `config` is already imported here.
    """

    def _run(self, code, **env):
        import os
        import subprocess
        import sys
        from pathlib import Path

        backend = Path(__file__).resolve().parent.parent

        environ = {**os.environ, **{k: str(v) for k, v in env.items()}}
        # The developer's own .env would otherwise supply the very values
        # these cases are meant to be missing.
        environ["LEADFINDER_IGNORE_ENV_FILE"] = "1"

        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=backend,
            env=environ,
            capture_output=True,
            text=True,
        )

    def test_a_valid_configuration_imports_the_app(self):
        result = self._run("import app; print('booted')")

        assert result.returncode == 0, result.stderr
        assert "booted" in result.stdout

    def test_an_invalid_value_stops_the_process_before_the_app_loads(self):
        result = self._run(
            "import app; print('SHOULD NOT REACH HERE')",
            DATABASE_URL="mysql://user:hunter2@host/db",
        )

        assert result.returncode == 1
        assert "SHOULD NOT REACH HERE" not in result.stdout
        assert "failed to start: invalid configuration" in result.stderr
        assert "unsupported database dialect" in result.stderr
        assert "hunter2" not in result.stderr, "the DSN leaked into stderr"

    def test_a_missing_required_variable_stops_production(self):
        result = self._run(
            "import app",
            ENVIRONMENT="production",
            GEOAPIFY_API_KEY="",
            DATABASE_URL="postgresql://u:p@h/d",
            CORS_ORIGINS="https://app.test",
        )

        assert result.returncode == 1
        assert "GEOAPIFY_API_KEY is required" in result.stderr

    def test_the_failure_message_says_where_to_set_things(self):
        result = self._run("import app", MAX_PAGE_SIZE=1, DEFAULT_PAGE_SIZE=50)

        assert result.returncode == 1
        assert ".env.example" in result.stderr
        assert "MAX_PAGE_SIZE" in result.stderr

    def test_no_traceback_is_shown_for_a_configuration_error(self):
        """A stack trace here tells the operator nothing they can act on."""

        result = self._run("import app", LOG_LEVEL="VERBOSE")

        assert result.returncode == 1
        assert "Traceback" not in result.stderr
        assert "LOG_LEVEL" in result.stderr


SECRET_KEY = "gk-super-secret-value-9f2c1b7e"
SECRET_DSN = "postgresql://app:hunter2@db.internal:5432/leadfinder"


class TestSecretMasking:
    """
    Every route a settings object can take into a log, a traceback or a
    crash reporter. A secret must survive none of them.
    """

    @pytest.fixture
    def settings(self, clean_env):
        return build(
            clean_env, GEOAPIFY_API_KEY=SECRET_KEY, DATABASE_URL=SECRET_DSN
        )

    def test_repr_masks_both_secrets(self, settings):
        text = repr(settings)

        assert SECRET_KEY not in text
        assert "hunter2" not in text
        assert text.count("SecretStr('**********')") == 2

    def test_str_masks_both_secrets(self, settings):
        assert SECRET_KEY not in str(settings)
        assert "hunter2" not in str(settings)

    def test_f_string_interpolation_masks(self, settings):
        """The likeliest accident: someone drops a field into a log message."""

        assert SECRET_KEY not in f"{settings.GEOAPIFY_API_KEY}"
        assert "hunter2" not in f"{settings.DATABASE_URL}"

    def test_model_dump_masks(self, settings):
        assert SECRET_KEY not in str(settings.model_dump())
        assert "hunter2" not in str(settings.model_dump())

    def test_json_serialisation_masks(self, settings):
        """What a crash reporter or a debug endpoint would send.."""

        payload = settings.model_dump_json()

        assert SECRET_KEY not in payload
        assert "hunter2" not in payload
        assert payload.count('"**********"') == 2

    def test_the_accessors_still_return_the_real_values(self, settings):
        """Masking is worthless if it also breaks the thing being configured."""

        assert settings.geoapify_api_key == SECRET_KEY
        assert settings.database_url == SECRET_DSN
        assert settings.has_geoapify_key is True

    def test_safe_database_url_masks_the_password_but_keeps_the_target(
        self, settings
    ):
        safe = settings.safe_database_url

        assert "hunter2" not in safe
        assert "db.internal:5432/leadfinder" in safe
        assert "app" in safe, "the user is not a secret and identifies the role"

    def test_safe_database_url_is_harmless_for_sqlite(self, clean_env):
        settings = build(clean_env, DATABASE_URL="sqlite:///./x.db")

        assert settings.safe_database_url.startswith("sqlite:")

    def test_masking_never_raises_on_a_url_it_cannot_parse(self):
        from config import mask_url_password

        assert mask_url_password("::::not a url::::") == (
            "<unparseable database url>"
        )

    def test_missing_key_reads_as_empty_not_none(self, clean_env):
        """`str(None)` in a query string would send the literal "None"."""

        settings = build(clean_env)

        assert settings.GEOAPIFY_API_KEY is None
        assert settings.geoapify_api_key == ""
        assert settings.has_geoapify_key is False

    def test_a_blank_key_is_treated_as_absent(self, clean_env):
        """`GEOAPIFY_API_KEY=` in a .env file must not read as configured."""

        settings = build(clean_env, GEOAPIFY_API_KEY="   ")

        assert settings.has_geoapify_key is False

    def test_the_key_is_stripped(self, clean_env):
        """A trailing newline from `echo` into .env would 401 every call."""

        settings = build(clean_env, GEOAPIFY_API_KEY="  real-key  ")

        assert settings.geoapify_api_key == "real-key"


class TestPlaceholderSecrets:
    @pytest.mark.parametrize(
        "value",
        ["your-geoapify-api-key-here", "changeme", "CHANGEME", "replace-me"],
    )
    def test_placeholder_keys_are_rejected(self, clean_env, value):
        """Copied .env.example, deployed, never filled in."""

        with pytest.raises(ValidationError) as exc:
            build(clean_env, GEOAPIFY_API_KEY=value)

        assert "placeholder" in str(exc.value)

    def test_the_shipped_example_file_would_be_rejected(self, clean_env):
        """Pins the check to the real file rather than a copy of its text."""

        import re
        from pathlib import Path

        example = (
            Path(__file__).resolve().parent.parent / ".env.example"
        ).read_text(encoding="utf-8")

        match = re.search(r"^GEOAPIFY_API_KEY=(.+)$", example, re.MULTILINE)

        assert match, "GEOAPIFY_API_KEY is not present in .env.example"

        with pytest.raises(ValidationError):
            build(clean_env, GEOAPIFY_API_KEY=match.group(1).strip())


class TestTheRepositoryHoldsNoSecrets:
    """
    A committed secret is the failure mode no amount of masking helps with.
    These run against the real files, so they fail the build if one appears.
    """

    @staticmethod
    def _backend():
        from pathlib import Path

        return Path(__file__).resolve().parent.parent

    def test_env_example_contains_no_real_looking_values(self):
        """
        Every assignment must be a placeholder, a default, or empty. A 32-char
        hex string here is a leaked key.
        """

        import re

        text = (self._backend() / ".env.example").read_text(encoding="utf-8")

        suspicious = []

        for line in text.splitlines():
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            name, _, value = line.partition("=")
            value = value.strip()

            if not value:
                continue

            if re.fullmatch(r"[0-9a-f]{24,}", value, re.IGNORECASE):
                suspicious.append(f"{name} looks like a live key")

            if re.search(r"://[^/\s:]+:[^/\s@]+@", value):
                suspicious.append(f"{name} contains inline credentials")

        assert suspicious == [], suspicious

    def test_dot_env_is_ignored_by_git(self):
        """The single control that keeps the real key out of every commit."""

        gitignore = (self._backend() / ".gitignore").read_text(encoding="utf-8")
        rules = {line.strip() for line in gitignore.splitlines()}

        assert ".env" in rules
        assert "!.env.example" in rules, (
            "the example file must stay committed despite the .env rule"
        )

    def test_no_source_file_hardcodes_a_secret(self):
        """`grep get_secret_value` must stay the complete list of unwrap sites."""

        allowed = {"config.py"}

        offenders = []

        for path in self._backend().rglob("*.py"):
            parts = path.parts

            if {"venv", "__pycache__", "tests"} & set(parts):
                continue

            if "get_secret_value" in path.read_text(encoding="utf-8"):
                if path.name not in allowed:
                    offenders.append(str(path.relative_to(self._backend())))

        assert offenders == [], (
            f"{offenders} unwrap a secret directly — go through a settings "
            "accessor so there stays one place to audit"
        )


class TestErrorsDoNotLeak:
    def test_the_startup_summary_never_prints_the_offending_value(self, capsys):
        """
        pydantic puts the raw input in `str(ValidationError)` — for a DSN that
        is the password. `_fail` prints `msg` only, and this is what pins it.
        """

        from config import Settings, _fail

        try:
            Settings(_env_file=None, DATABASE_URL="mysql://u:hunter2@h/d")
        except ValidationError as exc:
            assert "hunter2" in str(exc), (
                "if pydantic stops echoing the input this test is obsolete"
            )

            with pytest.raises(SystemExit) as raised:
                _fail(exc)

        printed = capsys.readouterr().err

        assert raised.value.code == 1
        assert "hunter2" not in printed
        assert "unsupported database dialect" in printed
        assert "DATABASE_URL" in printed

    def test_the_dialect_error_quotes_the_scheme_only(self, clean_env):
        with pytest.raises(ValidationError) as exc:
            build(clean_env, DATABASE_URL="mysql://user:hunter2@host/db")

        message = exc.value.errors()[0]["msg"]

        assert "'mysql'" in message
        assert "hunter2" not in message
