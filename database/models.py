from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import backref, relationship

from database.db import Base


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    # The raw bearer token exists only in the HttpOnly cookie. A database leak
    # cannot be turned directly into a live browser session.
    token_hash = Column(String(64), primary_key=True)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)


class Business(Base):
    __tablename__ = "businesses"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=False)
    phone = Column(String)
    email = Column(String)
    website = Column(String)

    city = Column(String, index=True)
    category = Column(String, index=True)

    address = Column(String)

    status = Column(String)

    place_id = Column(String, unique=True)


class ScanJob(Base):
    __tablename__ = "scan_jobs"

    id = Column(Integer, primary_key=True, index=True)

    city = Column(String)
    category = Column(String)

    status = Column(String)

    progress = Column(Integer, default=0)

    total_businesses = Column(Integer, default=0)
    new_businesses = Column(Integer, default=0)

    # NEW
    total_cells = Column(Integer, default=0)
    completed_cells = Column(Integer, default=0)

    current_cell = Column(String)


class WebsiteData(Base):
    __tablename__ = "website_data"

    id = Column(Integer, primary_key=True, index=True)

    business_id = Column(
        Integer,
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title = Column(Text)
    meta_description = Column(Text)

    # Several addresses can be scraped from one page, so this holds a list
    # rather than a single value.
    emails = Column(Text, default="[]")

    facebook = Column(String)
    instagram = Column(String)
    linkedin = Column(String)
    youtube = Column(String)
    twitter = Column(String)
    whatsapp = Column(String)

    scraped_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )

    status = Column(
    String,
    default="Pending",
    )

    # `backref` builds the reverse accessor on Business at mapper-configuration
    # time, so the relationship exists on both sides without editing that class.
    #
    # The cascade matters because DELETE /businesses/{id} removes the parent:
    # without it SQLAlchemy would try to NULL out business_id, which is NOT NULL.
    #
    # `passive_deletes` is deliberately left off. It would hand the job to the
    # database's ON DELETE CASCADE, but SQLite only enforces foreign keys when
    # `PRAGMA foreign_keys=ON` is set per connection, which this app does not
    # do — so the children were silently orphaned. Letting the ORM load and
    # delete them works on every backend.
    business = relationship(
        "Business",
        backref=backref(
            "website_data",
            cascade="all, delete-orphan",
        ),
    )


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id = Column(Integer, primary_key=True, index=True)

    status = Column(String, default="Pending")

    progress = Column(Integer, default=0)

    total_websites = Column(Integer, default=0)
    completed = Column(Integer, default=0)

    success = Column(Integer, default=0)
    failed = Column(Integer, default=0)

    # A plain Integer rather than a ForeignKey: this is a progress pointer, and
    # a job record should survive the business it was last working on being
    # deleted mid-run.
    current_business_id = Column(
    Integer,
    ForeignKey("businesses.id", ondelete="SET NULL"),
    nullable=True,
)
    # No defaults — a job is created "Pending", so it has not started yet and
    # both timestamps are set by the service as the run progresses.
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "uq_scrape_jobs_single_active",
            text("(1)"),
            unique=True,
            postgresql_where=text("status IN ('Pending', 'Running')"),
            sqlite_where=text("status IN ('Pending', 'Running')"),
        ),
    )
