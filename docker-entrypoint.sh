#!/bin/sh
# =============================================================================
# Container entrypoint.
#
#   serve            wait for the database, migrate, then run the API (default)
#   migrate          run migrations and exit — for a deploy job
#   <anything else>  executed as-is, so `docker run … sh` still works
# =============================================================================

set -eu

log() {
    # Same shape as the application's own lines, so `docker logs` reads as one
    # stream rather than two.
    printf '%s %-8s [-] entrypoint | %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S')" "$1" "$2"
}

wait_for_database() {
    # Compose's `depends_on: condition: service_healthy` already handles this,
    # but nothing outside compose does — a Kubernetes pod or an ECS task can
    # start well before its database accepts connections.
    if [ "${DB_WAIT_SECONDS:-60}" -le 0 ]; then
        return 0
    fi

    log INFO "waiting up to ${DB_WAIT_SECONDS}s for the database"

    python - "$DB_WAIT_SECONDS" <<'PY'
import sys, time

from sqlalchemy import text

from config import settings
from database.db import engine

deadline = time.monotonic() + float(sys.argv[1])
attempt = 0

while True:
    attempt += 1

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        print(f"database reachable at {settings.safe_database_url}", flush=True)
        break

    except Exception as exc:
        if time.monotonic() >= deadline:
            # safe_database_url, never the raw DSN: this message goes to the
            # container log, which is collected and often widely readable.
            print(
                f"database unreachable after {attempt} attempts "
                f"({settings.safe_database_url}): {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
            raise SystemExit(1)

        time.sleep(2)
PY
}

run_migrations() {
    log INFO "running alembic upgrade head"
    alembic upgrade head
    log INFO "migrations complete"
}

case "${1:-serve}" in
    serve)
        wait_for_database

        if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
            run_migrations
            export RUN_MIGRATIONS=false
        else
            log INFO "RUN_MIGRATIONS is not 'true' — skipping migrations"
        fi

        log INFO "starting uvicorn on ${APP_HOST:-0.0.0.0}:${APP_PORT:-8000}"

        # exec so uvicorn becomes PID 1 and receives SIGTERM directly. Without
        # it the shell holds PID 1, ignores the signal, and every deploy waits
        # out the 10-second kill timeout instead of draining gracefully.
        exec uvicorn app:app \
            --host "${APP_HOST:-0.0.0.0}" \
            --port "${APP_PORT:-8000}" \
            --workers "${WEB_CONCURRENCY:-1}" \
            --no-access-log
        ;;

    migrate)
        wait_for_database
        run_migrations
        ;;

    *)
        exec "$@"
        ;;
esac
