from typing import Any, Dict

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

from config import settings

# Re-exported for the modules and migrations that already import these names.
DATABASE_URL = settings.DATABASE_URL
IS_SQLITE = settings.is_sqlite


def _engine_options() -> Dict[str, Any]:
    """Connection settings differ per backend; keep the branch in one place."""

    options: Dict[str, Any] = {"echo": settings.DATABASE_ECHO}

    if settings.is_sqlite:
        # FastAPI hands requests to a threadpool and background jobs run on
        # their own threads, so a connection is not confined to one thread.
        options["connect_args"] = {"check_same_thread": False}
        return options

    # PostgreSQL and friends: recycle stale connections rather than handing a
    # dead socket to a request after the database or a proxy has idled it out.
    options["pool_pre_ping"] = True
    options["pool_recycle"] = settings.DATABASE_POOL_RECYCLE_SECONDS

    return options


engine = create_engine(
    settings.DATABASE_URL,
    **_engine_options(),
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()
