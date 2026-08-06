from sqlalchemy.orm import Session
from database.models import Business, ScanJob


# ======================================================
# BUSINESS CRUD
# ======================================================

def save_business(
    db: Session,
    name,
    phone,
    email,
    website,
    city,
    category,
    address,
    status,
    place_id,
):
    existing = (
        db.query(Business)
        .filter(Business.place_id == place_id)
        .first()
    )

    if existing:
        return False

    business = Business(
        name=name,
        phone=phone,
        email=email,
        website=website,
        city=city,
        category=category,
        address=address,
        status=status,
        place_id=place_id,
    )

    db.add(business)
    db.commit()

    return True


# ======================================================
# SCAN JOB CRUD
# ======================================================

def create_scan_job(db: Session, city: str, category: str):

    job = ScanJob(
        city=city,
        category=category,
        status="Running",
        progress=0,
        total_businesses=0,
        new_businesses=0,
    )

    db.add(job)
    db.commit()
    db.refresh(job)

    return job


def update_scan_job(
    db: Session,
    job_id: int,
    progress: int = None,
    total_businesses: int = None,
    new_businesses: int = None,
    status: str = None,
):

    job = db.query(ScanJob).filter(ScanJob.id == job_id).first()

    if not job:
        return

    if progress is not None:
        job.progress = progress

    if total_businesses is not None:
        job.total_businesses = total_businesses

    if new_businesses is not None:
        job.new_businesses = new_businesses

    if status is not None:
        job.status = status

    db.commit()

    return job


def get_scan_jobs(db: Session):
    return (
        db.query(ScanJob)
        .order_by(ScanJob.id.desc())
        .all()
    )