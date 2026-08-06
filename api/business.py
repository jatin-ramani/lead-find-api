from fastapi import APIRouter
from sqlalchemy.orm import Session

from database.db import SessionLocal
from database.models import Business

router = APIRouter()


@router.get("/businesses")
def get_businesses():

    db: Session = SessionLocal()

    businesses = db.query(Business).all()

    result = []

    for b in businesses:
        result.append({
            "id": b.id,
            "name": b.name,
            "phone": b.phone,
            "email": b.email,
            "website": b.website,
            "city": b.city,
            "status": b.status,
        })

    db.close()

    return result