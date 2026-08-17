# Lead Finder — Backend

FastAPI + SQLAlchemy service that scans local businesses, works out which have
no website, and scrapes the ones that do.

---

## Setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

---

## Configuration

Every tunable value lives in **`config.py`**. No other module reads the
environment; they all import `settings`. Values resolve in this order:

1. real environment variables (containers, CI)
2. `backend/.env` (developer convenience, gitignored)
3. the defaults in `config.py`, which reproduce historical behaviour

```bash
cp .env.example .env
# then set GEOAPIFY_API_KEY
```

`.env.example` documents every setting and contains **placeholders only**. The
defaults are enough to run locally — only `GEOAPIFY_API_KEY` is genuinely
needed, and only for scanning.

Inside a container image, set `LEADFINDER_IGNORE_ENV_FILE=1` so a stray `.env`
that got copied in cannot silently override the platform's real environment.

### Environments

`ENVIRONMENT` is one of `development`, `testing`, `production`. Setting it to
`production` turns on extra checks that **refuse to start** if violated:

| Rule | Why |
|---|---|
| `GEOAPIFY_API_KEY` must be set | scanning is the product; a missing key would only surface at the first scan |
| `DATABASE_URL` must not be SQLite | SQLite allows one writer, and background scrape jobs contend with API requests |
| `CORS_ORIGINS` must not contain `*` | credentialed requests from any origin |
| Geoapify URLs must be `https://` | the key travels as a query parameter and would otherwise be cleartext on the wire |
| `/docs` and `/redoc` are hidden | unless `DEBUG=true` |

These apply in **every** environment:

| Rule | Why |
|---|---|
| `GEOAPIFY_API_KEY` may not be the `.env.example` placeholder | a copied-and-forgotten file otherwise reaches production and fails as a fake outage |
| `DATABASE_URL` must be `sqlite://` or `postgresql://` | nothing else is tested, and a typo should not reach `create_engine` |
| Each `CORS_ORIGINS` entry must be `scheme://host[:port]`, no path | a path matches nothing; the browser blocks the frontend while the server logs look healthy |
| `MAX_PAGE_SIZE >= DEFAULT_PAGE_SIZE` | otherwise the default page is unrequestable |

### Startup warnings

Some settings are legal but risky. They do not stop the process — each has a
legitimate use — but they are logged at `WARNING` on every boot:

| Warning | Risk |
|---|---|
| `DEBUG=true` in production | exception types and messages appear in error responses, and `/docs` is served |
| `DATABASE_ECHO=true` | every SQL statement, **including bound parameters**, is written to the log |
| `GEOAPIFY_API_KEY` missing | scanning fails with 502 |
| Many credentialed CORS origins | broader than it probably needs to be |

### Fail-fast

Configuration is validated when `config.py` is imported — before the app binds
a port. An invalid value prints a readable summary and exits `1`:

```
====================================================================
 Lead Finder failed to start: invalid configuration
====================================================================
  GEOAPIFY_API_KEY: Value error, GEOAPIFY_API_KEY is required when ENVIRONMENT=production

  Set these in the environment or in backend/.env
  See backend/.env.example for the full list.
====================================================================
```

### Settings reference

| Group | Keys |
|---|---|
| Environment | `ENVIRONMENT`, `DEBUG` |
| App | `APP_NAME`, `APP_VERSION` |
| Database | `DATABASE_URL`, `DATABASE_ECHO`, `DATABASE_POOL_RECYCLE_SECONDS` |
| CORS | `CORS_ORIGINS`, `CORS_ALLOW_CREDENTIALS` |
| Geoapify | `GEOAPIFY_API_KEY`, `GEOAPIFY_PLACES_URL`, `GEOAPIFY_GEOCODE_URL`, `GEOAPIFY_TIMEOUT_SECONDS`, `GEOAPIFY_SEARCH_LIMIT`, `GEOAPIFY_SEARCH_RADIUS_METRES` |
| Scraper | `SCRAPER_TIMEOUT_SECONDS`, `SCRAPER_MAX_RESPONSE_BYTES`, `SCRAPER_USER_AGENT` |
| Pagination | `DEFAULT_PAGE_SIZE`, `MAX_PAGE_SIZE` |
| Logging | `LOG_LEVEL`, `LOG_FORMAT`, `LOG_JSON`, `LOG_ACCESS_EXCLUDE_PATHS`, `LOG_UVICORN_ACCESS`, `LOG_TRUST_PROXY_HEADERS` |

`CORS_ORIGINS` accepts either form:

```ini
CORS_ORIGINS=https://app.example.com,https://admin.example.com
CORS_ORIGINS=["https://app.example.com","https://admin.example.com"]
```

### Adding a setting

Add the field to `Settings` in `config.py` with a default, document it in
`.env.example`, and import `settings` where it is used. Do not call
`os.getenv` anywhere else — that is the thing this module exists to prevent.

### Secrets

Two settings are secret: **`GEOAPIFY_API_KEY`** and **`DATABASE_URL`** (a
PostgreSQL DSN embeds the database password). Both are declared as pydantic
`SecretStr`, which means every accidental route out of the process is closed:

```python
>>> repr(settings)
Settings(..., DATABASE_URL=SecretStr('**********'), GEOAPIFY_API_KEY=SecretStr('**********'), ...)

>>> settings.model_dump_json()
{"DATABASE_URL":"**********","GEOAPIFY_API_KEY":"**********", ...}

>>> f"connecting to {settings.DATABASE_URL}"
'connecting to **********'
```

That covers `repr`, `str`, f-strings, `model_dump`, JSON serialisation, and any
traceback or crash reporter that renders the settings object.

To *use* a secret, go through an accessor. These are the only places a value is
unwrapped, so `grep -rn get_secret_value` lists every path a secret can take
out of `config.py`:

| Accessor | Returns |
|---|---|
| `settings.database_url` | the real DSN — for `create_engine`, not for printing |
| `settings.safe_database_url` | the DSN with the password masked — safe to log |
| `settings.geoapify_api_key` | the key, or `""` when unset |
| `settings.has_geoapify_key` | `True`/`False` — for logs and health output |

**Rules of thumb**

- `.env` is gitignored and must never be committed. `.env.example` holds
  placeholders only, and the placeholder key is rejected at startup.
- In production, supply configuration through the platform's secret store
  (ECS task definition, Kubernetes Secret, Fly secrets, Render environment
  group) rather than a file on disk.
- Never log a raw setting. Use `safe_database_url` and `has_geoapify_key`;
  that is what the startup line does.
- `/system` reports the database **dialect**, never the URL.
- Error responses never contain configuration. A failed Geoapify call returns
  a generic 502 — the upstream body can echo the key back and is not
  forwarded.

**One gap worth knowing.** pydantic puts the offending value in
`str(ValidationError)`, including for a `SecretStr` field. `config._fail`
prints `err["msg"]` only and never `err["input"]`, so the startup path is
safe — but a `Settings(...)` call in your own code that lets a `ValidationError`
escape will print the bad value. Catch it.

---

## Logging

Defined in [`logging_config.py`](logging_config.py). One record shape, two
renderings — switching between them is a config change, never a code change.

### What every record carries

| Field | Source |
|---|---|
| timestamp, level, logger, message | the record |
| `request_id` | `errors.request_id_ctx`, set per request, matches `X-Request-ID` |
| `method`, `path`, `client_ip` | context vars set by `RequestLogMiddleware` |
| `status`, `duration_ms` | only knowable at the end, so only on the access record |

### Human output (default)

```
11:39:12 INFO     [-]        app     | Lead Finder API 1.0.0 starting | environment=production …
11:39:15 INFO     [505d0b94] errors  | Validation failed on POST /scan: [...] | method=POST path=/scan client_ip=127.0.0.1
11:39:15 WARNING  [505d0b94] access  | POST /scan -> 422 in 2.82ms | client_ip=127.0.0.1
11:39:18 ERROR    [7c1a90ff] errors  | Unhandled RuntimeError on GET /businesses | method=GET path=/businesses client_ip=10.0.0.4
11:39:18 ERROR    [7c1a90ff] access  | GET /businesses -> 500 in 12.4ms | client_ip=10.0.0.4
11:39:22 INFO     [b2c48e01] access  | GET /businesses/export/csv -> 200 in 1.5ms (completed in 840.2ms) | client_ip=10.0.0.4
```

Every line for one request shares its id, so `grep 505d0b94 app.log` is the
whole story of that request. The access record repeats nothing it already
states in prose — the key=value suffix carries only what the message does not.

### JSON output — `LOG_JSON=true`

```json
{"timestamp":"2026-08-07T11:39:15.244Z","level":"WARNING","logger":"access","requestId":"505d0b94…","message":"POST /scan -> 422 in 2.82ms","method":"POST","path":"/scan","status":422,"duration_ms":2.82,"client_ip":"127.0.0.1"}
```

Same records, same fields, one object per line. Tracebacks become an
`exception` field rather than breaking the line into many.

### Two timings, deliberately

`duration_ms` is time to response headers — what the caller waited. `total_ms`
is time until the request was completely finished, which for a streamed CSV or
a backgrounded bulk scrape is far longer and is *not* the caller's wait.
Reporting the total as "duration" would blame the endpoint for the download.
The human line shows the second only when it differs materially.

### Levels follow the status

`5xx` → `ERROR`, `4xx` → `WARNING`, everything else → `INFO`. So
`grep -w ERROR` finds the failures and nothing else.

### Secrets

Both formatters run every line — message, arguments, and rendered traceback —
through `redact()`, replacing the live API key and the database password with
`***REDACTED***`. This is a backstop, not the control: `SecretStr` and the
settings accessors are. Only the DSN *password* is redacted, not the whole
URL, so a SQLite path — which has no secret and is the one thing worth knowing
when a process opens the wrong file — stays readable.

### Middleware order

Outermost first: **CORS → RequestID → RequestLog**.

- CORS stays outermost so the error responses built by `RequestIDMiddleware`
  still get `Access-Control-Allow-Origin`. Without that a browser sees an
  opaque failure instead of the 500 body.
- RequestID wraps RequestLog so the id exists before the access record is
  written.

The cost of that ordering: a **CORS preflight answered by `CORSMiddleware`
never reaches the access log** and has no request id. Losing CORS headers on
error responses would be worse.

### If a key is ever exposed

1. **Rotate first.** Revoke at <https://myprojects.geoapify.com/> and issue a
   new key. History rewriting is pointless while the old key still works.
2. Update the secret store and `.env`; restart.
3. Then purge it from history if the repository was ever pushed or shared —
   `git filter-repo --path backend/.env --invert-paths`, force-push, and have
   every clone re-clone. Rewriting alone is not remediation; step 1 is.

---

## Database migrations (Alembic)

The schema is owned by Alembic. `Base.metadata.create_all()` is **not** used
anywhere: it can create missing tables but never alters existing ones, so the
database silently drifts from the models as columns are added.

All commands run from the `backend/` directory.

### Apply migrations — the command you need most

```bash
alembic upgrade head
```

Run this after every `git pull` and as part of every deploy, **before** the app
starts. It is safe to run repeatedly; if the database is already current it
does nothing.

### Check where the database stands

```bash
alembic current           # revision the database is on
alembic history --verbose # every revision, newest first
alembic heads             # latest available revision
```

### Create a migration after changing a model

```bash
# 1. edit database/models.py
# 2. autogenerate the diff
alembic revision --autogenerate -m "add created_at to businesses"

# 3. READ the generated file in alembic/versions/ before applying it
# 4. apply
alembic upgrade head
```

Autogenerate is a starting point, not an oracle. It reliably detects added and
removed tables, columns and indexes; it does **not** detect renames (it emits a
drop plus an add, which loses data) and it cannot infer how to backfill a new
`NOT NULL` column. Always read the file.

### Roll back

```bash
alembic downgrade -1      # undo the last migration
alembic downgrade base    # undo everything
alembic downgrade 0001    # go to a specific revision
```

### Preview the SQL without touching the database

```bash
alembic upgrade head --sql
```

Useful for a DBA review, or for applying a change by hand in an environment
where the app has no DDL permissions.

---

## Migration conventions

- **One logical change per migration.** Easier to review and to roll back.
- **Never edit a migration that has been applied anywhere but your own
  machine.** Add a new one instead.
- **Migrations must be deterministic.** `0001_initial_schema` is conditional —
  it creates each table only if absent — purely because it had to adopt
  databases that predate Alembic and were in mixed states. Nothing after it
  should do that.
- **Data migrations belong in the migration**, not in application startup.
- SQLite cannot `ALTER` a column. `env.py` sets `render_as_batch` automatically
  when the URL is SQLite, so Alembic rewrites the table instead; the same
  migration file then runs unchanged on PostgreSQL.

---

## Switching to PostgreSQL

The driver ships in `requirements.txt`, so this is one variable:

```bash
export DATABASE_URL="postgresql+psycopg://user:password@localhost:5432/leadfinder"
alembic upgrade head
```

Nothing else changes. `database/db.py` selects connection settings from the
URL: SQLite gets `check_same_thread=False`, everything else gets
`pool_pre_ping` and connection recycling.

To see the DDL before running it against anything:

```bash
DATABASE_URL="postgresql+psycopg://…" alembic upgrade head --sql
```

---

## Running

```bash
alembic upgrade head          # always first
uvicorn app:app --reload
```

- API — http://127.0.0.1:8000
- Swagger — http://127.0.0.1:8000/docs
- ReDoc — http://127.0.0.1:8000/redoc
- Health — http://127.0.0.1:8000/health

---

## Docker

```bash
cp .env.example .env        # then set GEOAPIFY_API_KEY
docker compose up --build
```

That brings up PostgreSQL, waits for it to accept connections, runs
`alembic upgrade head`, and serves the API on <http://localhost:8000>.

| | |
|---|---|
| API | <http://localhost:8000> |
| Swagger | <http://localhost:8000/docs> |
| Health | <http://localhost:8000/health> |
| PostgreSQL | `localhost:5432`, user/db `leadfinder` |

```bash
docker compose logs -f backend      # follow the API
docker compose ps                   # health status of both services
docker compose exec db psql -U leadfinder leadfinder
docker compose down                 # stop, keep the data
docker compose down -v              # stop and DESTROY the database volume
```

### The image

Three stages. `builder` compiles dependencies into `/opt/venv`; `test` runs the
suite; `runtime` carries the interpreter, that venv and the source — no
compilers, no test tooling, no package cache.

```bash
docker build --target test .              # gate a release on the suite
docker build -t leadfinder-api .          # build the runtime image
```

**Security properties**, each pinned by a test in
[`tests/test_deployment.py`](tests/test_deployment.py):

- Runs as **uid 10001**, never root. The uid is fixed so bind-mount ownership
  is predictable across hosts.
- **No secret is baked in.** No `ARG` or `ENV` carries one — a build argument
  lands in the image history and stays readable via `docker history` even if a
  later layer unsets it. `GEOAPIFY_API_KEY` and `DATABASE_URL` arrive at run
  time only.
- `.dockerignore` excludes `.env`, `venv/`, `*.db` and `.git/`, and the runtime
  stage `COPY`s only the paths it names — two independent locks.
- `LEADFINDER_IGNORE_ENV_FILE=1` so a `.env` that somehow lands in the image is
  ignored rather than silently overriding the platform's configuration.
- The healthcheck uses the interpreter, not `curl`: no extra bytes, no extra
  CVE surface. It has a `--start-period` so probes during migration don't kill
  the container before it starts.

### Entrypoint

```bash
docker run … leadfinder-api            # serve (default): wait, migrate, run
docker run … leadfinder-api migrate    # migrate and exit — for a deploy job
docker run … leadfinder-api sh         # anything else is exec'd as-is
```

`uvicorn` is `exec`'d so it becomes PID 1 and receives `SIGTERM` directly.
Without that the shell holds PID 1, ignores the signal, and every deploy waits
out the kill timeout instead of draining connections.

### Runtime environment

Everything from the [Settings reference](#settings-reference), plus:

| Variable | Default | Meaning |
|---|---|---|
| `RUN_MIGRATIONS` | `true` | Run `alembic upgrade head` before serving |
| `DB_WAIT_SECONDS` | `60` | How long to wait for the database; `0` disables |
| `WEB_CONCURRENCY` | `1` | uvicorn worker processes |
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `8000` | Bind address |

The image defaults to `ENVIRONMENT=production` and `LOG_JSON=true`; compose
overrides both for local work.

---

## Deploying

### Without Docker

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app:app --host 0.0.0.0 --port 8000
```

### With Docker

```bash
docker build -t leadfinder-api:$(git rev-parse --short HEAD) .
```

Supply at run time — never at build time:

```
DATABASE_URL=postgresql+psycopg://user:password@host:5432/leadfinder
GEOAPIFY_API_KEY=…
ENVIRONMENT=production
CORS_ORIGINS=https://your-frontend.example.com
```

Production refuses to start without a real key, on SQLite, with `CORS_ORIGINS=*`
or with plain-HTTP Geoapify URLs — see [Environments](#environments).

### Migrations with more than one replica

`RUN_MIGRATIONS=true` is right for a single container. With several replicas
they all migrate at once and race. Run migrations as their own step and turn
them off in the serving containers:

```bash
docker run --rm -e DATABASE_URL=… leadfinder-api migrate   # once
# then serve with RUN_MIGRATIONS=false
```

That maps onto a Kubernetes `initContainer`, an ECS one-off task, or a
release-phase command.

### Before going live

- Put TLS in front. The API speaks plain HTTP; the Geoapify key would
  otherwise be the only thing encrypted on the wire.
- Set `LOG_TRUST_PROXY_HEADERS=true` **only** behind a load balancer that
  overwrites `X-Forwarded-For`, and pass `--proxy-headers` to uvicorn.
- Pin the base image by digest (`python:3.14-slim@sha256:…`) so a rebuild
  cannot silently change the interpreter.
- Take database backups. The compose volume is not one.

Run `alembic upgrade head` as a release step that must succeed before the new
version starts serving. If it fails, do not start the app — a process running
against a schema it does not expect fails in far more confusing ways than a
failed migration does.

---

## Layout

```
backend/
├── alembic/
│   ├── env.py                 # reads Base + DATABASE_URL from the app
│   ├── script.py.mako         # template for new migrations
│   └── versions/              # migration history
├── alembic.ini
├── api/                       # routers — thin, no business logic
├── config.py                  # ALL configuration; the only reader of env
├── database/
│   ├── db.py                  # engine, session, Base
│   ├── models.py              # SQLAlchemy models (source of truth)
│   └── crud.py                # all queries live here
├── providers/                 # third-party APIs (Geoapify)
├── schemas/                   # Pydantic request/response models
├── services/                  # scanning and scraping logic
└── app.py                     # FastAPI app and router registration
```
