from database.db import SessionLocal
from database.crud import (
    save_business,
    create_scan_job,
    update_scan_job,
)

from providers.geoapify import search_businesses


def scan_city(city: str, category: str):

    db = SessionLocal()

    job_id = create_scan_job(
        db=db,
        city=city,
        category=category,
    )

    try:

        businesses = search_businesses(
            city=city,
            category=category,
        )

        print(f"Found {len(businesses)} businesses")

        total = len(businesses)
        added = 0

        for index, business in enumerate(businesses):

            p = business.get("properties", {})
            contact = p.get("contact", {})

            print("Business:", p.get("name"))

            website = p.get("website")
            status = "Has Website" if website else "No Website"

            saved = save_business(
                db=db,
                name=p.get("name"),
                phone=contact.get("phone"),
                email=contact.get("email"),
                website=website,
                city=city,
                category=category,
                address=p.get("formatted"),
                status=status,
                place_id=p.get("place_id"),
            )

            print("Saved:", saved)

            if saved:
                added += 1

            progress = int(((index + 1) / total) * 100) if total else 100

            update_scan_job(
                db=db,
                job_id=job_id,
                progress=progress,
                total_businesses=total,
                new_businesses=added,
                status="Running",
            )

        update_scan_job(
            db=db,
            job_id=job_id,
            progress=100,
            total_businesses=total,
            new_businesses=added,
            status="Completed",
        )

        print(f"Scan Completed. Added {added} businesses.")

    except Exception as e:

        print(f"Scan Failed: {e}")

        update_scan_job(
            db=db,
            job_id=job_id,
            progress=0,
            total_businesses=0,
            new_businesses=0,
            status="Failed",
        )

    finally:

        db.close()

    return job_id