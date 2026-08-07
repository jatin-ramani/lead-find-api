import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

load_dotenv()

# Absolute, so the database is the same file no matter which directory the
# process was started from. The previous relative URL resolved against the
# current working directory and silently created a second, empty database when
# uvicorn or alembic was launched from elsewhere.
BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = BACKEND_DIR / "database" / "leadfinder.db"

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}",
)

IS_SQLITE = DATABASE_URL.startswith("sqlite")


def _engine_options() -> Dict[str, Any]:
    """Connection settings differ per backend; keep the branch in one place."""

    if IS_SQLITE:
        return {
            # FastAPI hands requests to a threadpool and background jobs run on
            # their own threads, so a connection is not confined to one thread.
            "connect_args": {"check_same_thread": False},
        }

    # PostgreSQL and friends: recycle stale connections rather than handing a
    # dead socket to a request after the database or a proxy has idled it out.
    return {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }


engine = create_engine(
    DATABASE_URL,
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
