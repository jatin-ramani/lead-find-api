from sqlalchemy import Column, Integer, String
from database.db import Base


class Business(Base):
    __tablename__ = "businesses"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=False)
    phone = Column(String)
    email = Column(String)
    website = Column(String)

    city = Column(String)
    category = Column(String)

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