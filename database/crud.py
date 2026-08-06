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


def get_businesses(db: Session):
    return db.query(Business).all()


def get_business_by_id(db: Session, business_id: int):
    return (
        db.query(Business)
        .filter(Business.id == business_id)
        .first()
    )


def delete_business(db: Session, business_id: int):

    business = get_business_by_id(db, business_id)

    if not business:
        return False

    db.delete(business)
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

    return job.id


def update_scan_job(
    db: Session,
    job_id: int,
    progress: int = None,
    total_businesses: int = None,
    new_businesses: int = None,
    status: str = None,
):

    job = (
        db.query(ScanJob)
        .filter(ScanJob.id == job_id)
        .first()
    )

    if not job:
        return None

    if progress is not None:
        job.progress = progress

    if total_businesses is not None:
        job.total_businesses = total_businesses

    if new_businesses is not None:
        job.new_businesses = new_businesses

    if status is not None:
        job.status = status

    db.commit()
    db.refresh(job)

    return job


def get_scan_jobs(db: Session):

    return (
        db.query(ScanJob)
        .order_by(ScanJob.id.desc())
        .all()
    )


def get_scan_job(db: Session, job_id: int):

    return (
        db.query(ScanJob)
        .filter(ScanJob.id == job_id)
        .first()
    )


def delete_scan_job(db: Session, job_id: int):

    job = get_scan_job(db, job_id)

    if not job:
        return False

    db.delete(job)
    db.commit()

    return True


from sqlalchemy import func


# ======================================================
# DASHBOARD STATS
# ======================================================

def get_dashboard_stats(db: Session):

    total_businesses = db.query(Business).count()

    with_website = (
        db.query(Business)
        .filter(Business.website.isnot(None))
        .count()
    )

    without_website = (
        db.query(Business)
        .filter(Business.website.is_(None))
        .count()
    )

    emails_found = (
        db.query(Business)
        .filter(Business.email.isnot(None))
        .count()
    )

    running_scans = (
        db.query(ScanJob)
        .filter(ScanJob.status == "Running")
        .count()
    )

    completed_scans = (
        db.query(ScanJob)
        .filter(ScanJob.status == "Completed")
        .count()
    )

    return {
        "totalBusinesses": total_businesses,
        "withWebsite": with_website,
        "withoutWebsite": without_website,
        "emailsFound": emails_found,
        "runningScans": running_scans,
        "completedScans": completed_scans,
    }

def get_latest_scan_job(db: Session):
    return (
        db.query(ScanJob)
        .order_by(ScanJob.id.desc())
        .first()
    )