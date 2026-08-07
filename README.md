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

Create a `.env` alongside `app.py`:

```ini
GEOAPIFY_API_KEY=your-key-here

# Optional. Defaults to the bundled SQLite file.
# DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/leadfinder
```

> `.env` holds a live API key — it must never be committed.

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

```bash
pip install "psycopg[binary]"
export DATABASE_URL="postgresql+psycopg://user:password@localhost:5432/leadfinder"
alembic upgrade head
```

Nothing else changes. `database/db.py` selects connection settings from the
URL: SQLite gets `check_same_thread=False`, everything else gets
`pool_pre_ping` and connection recycling.

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

## Deploying

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app:app --host 0.0.0.0 --port 8000
```

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
├── database/
│   ├── db.py                  # engine, session, Base
│   ├── models.py              # SQLAlchemy models (source of truth)
│   └── crud.py                # all queries live here
├── providers/                 # third-party APIs (Geoapify)
├── schemas/                   # Pydantic request/response models
├── services/                  # scanning and scraping logic
└── app.py                     # FastAPI app and router registration
```
