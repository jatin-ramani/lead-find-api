# =============================================================================
# Lead Finder API
#
# Three stages:
#
#   builder — compiles the dependencies into a virtualenv
#   test    — runs the suite; build with `--target test` to gate a release
#   runtime — the shipped image: interpreter, the venv, and the source. No
#             compilers, no test tooling, no package index cache.
#
# The runtime stage copies only what it names, so anything added to the build
# context that is not COPYed here cannot end up in the published image.
# =============================================================================

ARG PYTHON_VERSION=3.14-slim


# -----------------------------------------------------------------------------
# Stage 1 — builder
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Its own layer so a source change does not reinstall every dependency.
COPY requirements.txt .

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# psycopg ships a binary wheel, so no libpq-dev or gcc is needed here. If a
# dependency ever does need to compile, install the build tools in THIS stage
# only — they must not reach the runtime image.


# -----------------------------------------------------------------------------
# Stage 2 — test
#
#   docker build --target test -t leadfinder-test .
#
# Fails the build if the suite fails, so a broken image cannot be published.
# -----------------------------------------------------------------------------
FROM builder AS test

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt

COPY . .

# conftest.py points the suite at a throwaway SQLite file and refuses to run
# against anything else, so this needs no database service.
RUN pytest


# -----------------------------------------------------------------------------
# Stage 3 — runtime
# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS runtime

# PYTHONUNBUFFERED is not optional in a container: without it Python block-
# buffers stdout when it is a pipe, and `docker logs` shows nothing until the
# buffer fills — which for a quiet service can be hours.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    # A `.env` copied into an image is a leak waiting to happen. Refuse to read
    # one; the platform supplies configuration through real environment
    # variables. .dockerignore already excludes it — this is the second lock.
    LEADFINDER_IGNORE_ENV_FILE=1 \
    # Defaults only. Every one is overridable at run time, and none is a secret.
    ENVIRONMENT=production \
    LOG_LEVEL=INFO \
    LOG_JSON=true \
    APP_PORT=8000 \
    APP_HOST=0.0.0.0 \
    WEB_CONCURRENCY=1 \
    RUN_MIGRATIONS=true \
    DB_WAIT_SECONDS=60

# NOTE: no ARG or ENV here carries a secret. GEOAPIFY_API_KEY and DATABASE_URL
# are supplied at run time. A build argument would be baked into the image
# history and readable with `docker history`, even if later unset.

# Fixed uid/gid so a bind-mounted volume has predictable ownership across hosts.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --home-dir /app app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Named explicitly rather than `COPY . .` so a stray file in the build context
# cannot silently join the image.
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app api ./api
COPY --chown=app:app database ./database
COPY --chown=app:app providers ./providers
COPY --chown=app:app schemas ./schemas
COPY --chown=app:app services ./services
COPY --chown=app:app app.py config.py errors.py logging_config.py ./
COPY --chown=app:app docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER app

EXPOSE 8000

# Python rather than curl: the interpreter is already here, so this costs no
# extra bytes and no extra CVE surface. /health returns 503 when the database
# is unreachable, which urlopen raises on — so an unhealthy database marks the
# container unhealthy, which is the point.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request;\
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('APP_PORT','8000')+'/health', timeout=4).status==200 else 1)"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["serve"]
