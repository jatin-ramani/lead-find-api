import pytest
from sqlalchemy.orm import Session

from database.models import Business, BusinessTag, Tag
from services.tag_service import (
    attach_tag_to_business,
    bulk_attach_tag,
    bulk_remove_tag,
    create_tag,
    delete_tag,
    get_or_create_tag,
    get_tags,
    get_tag_by_id,
    get_tag_by_name_or_slug,
    remove_tag_from_business,
    rename_tag,
    slugify_tag,
    validate_tag_name,
)


def test_validate_tag_name():
    assert validate_tag_name("  Hot Lead  ") == "Hot Lead"
    assert validate_tag_name("Website\nNeeded") == "Website Needed"
    assert validate_tag_name("ગુજરાતી") == "ગુજરાતી"
    assert validate_tag_name("डेंटल क्लिनिक") == "डेंटल क्लिनिक"
    assert validate_tag_name("🔥 High Priority") == "🔥 High Priority"

    with pytest.raises(ValueError, match="empty"):
        validate_tag_name("")

    with pytest.raises(ValueError, match="empty"):
        validate_tag_name("   \n\t  ")

    with pytest.raises(ValueError, match="cannot exceed"):
        validate_tag_name("A" * 51)


def test_slugify_tag():
    assert slugify_tag("Hot Lead") == "hot-lead"
    assert slugify_tag("Website / SEO Needed") == "website-seo-needed"
    assert slugify_tag("  🔥 High Priority  ") == "🔥-high-priority"
    assert slugify_tag("ગુજરાતી") == "ગુજરાતી"


def test_create_and_duplicate_tag(db: Session):
    tag = create_tag(db, "Hot Lead")
    assert tag.id is not None
    assert tag.name == "Hot Lead"
    assert tag.slug == "hot-lead"

    # Case-insensitive duplicate check
    with pytest.raises(ValueError, match="already exists"):
        create_tag(db, "hot lead")

    with pytest.raises(ValueError, match="already exists"):
        create_tag(db, "HOT LEAD")


def test_get_tags_with_usage_count(db: Session):
    t1 = create_tag(db, "Dental Lead")
    t2 = create_tag(db, "Follow Up")

    b1 = Business(name="Clinic 1", lead_score=80, lead_grade="A")
    b2 = Business(name="Clinic 2", lead_score=70, lead_grade="B")
    db.add_all([b1, b2])
    db.commit()

    attach_tag_to_business(db, b1.id, tag_id=t1.id)
    attach_tag_to_business(db, b2.id, tag_id=t1.id)
    attach_tag_to_business(db, b1.id, tag_id=t2.id)

    tags = get_tags(db)
    t1_data = next((t for t in tags if t["id"] == t1.id), None)
    t2_data = next((t for t in tags if t["id"] == t2.id), None)

    assert t1_data is not None and t1_data["business_count"] == 2
    assert t2_data is not None and t2_data["business_count"] == 1


def test_rename_tag(db: Session):
    tag = create_tag(db, "Initial Name")
    renamed = rename_tag(db, tag.id, "Updated Name")
    assert renamed.name == "Updated Name"
    assert renamed.slug == "updated-name"

    # Collision test
    t2 = create_tag(db, "Other Tag")
    with pytest.raises(ValueError, match="already exists"):
        rename_tag(db, tag.id, "other tag")


def test_delete_tag_does_not_delete_businesses(db: Session):
    tag = create_tag(db, "Temporary Tag")
    b = Business(name="Safe Business", lead_score=90, lead_grade="A")
    db.add(b)
    db.commit()

    attach_tag_to_business(db, b.id, tag_id=tag.id)
    assert len(b.tags) == 1

    deleted = delete_tag(db, tag.id)
    assert deleted is True

    # Business should still exist
    reloaded_b = db.query(Business).filter(Business.id == b.id).first()
    assert reloaded_b is not None
    assert len(reloaded_b.tags) == 0


def test_attach_and_remove_tag(db: Session):
    b = Business(name="Tag Target", lead_score=60, lead_grade="B")
    db.add(b)
    db.commit()

    # Auto-create on attach by name
    t = attach_tag_to_business(db, b.id, name="New Created Tag")
    assert t.name == "New Created Tag"
    assert len(b.tags) == 1
    assert b.tags[0].name == "New Created Tag"

    # Duplicate attach is idempotent
    attach_tag_to_business(db, b.id, tag_id=t.id)
    assert len(b.tags) == 1

    # Remove tag
    removed = remove_tag_from_business(db, b.id, t.id)
    assert removed is True
    assert len(b.tags) == 0


def test_bulk_attach_and_bulk_remove(db: Session):
    t = create_tag(db, "Bulk Tag")
    b1 = Business(name="Bulk 1")
    b2 = Business(name="Bulk 2")
    b3 = Business(name="Bulk 3")
    db.add_all([b1, b2, b3])
    db.commit()

    res = bulk_attach_tag(db, [b1.id, b2.id, b3.id, 99999], tag_id=t.id)
    assert res["updated_count"] == 3
    assert res["total_requested"] == 4

    # Idempotent re-run
    res2 = bulk_attach_tag(db, [b1.id, b2.id], tag_id=t.id)
    assert res2["updated_count"] == 0

    # Bulk remove
    remove_res = bulk_remove_tag(db, [b1.id, b2.id], tag_id=t.id)
    assert remove_res["removed_count"] == 2

    # b3 should still have the tag
    db.refresh(b3)
    assert len(b3.tags) == 1
