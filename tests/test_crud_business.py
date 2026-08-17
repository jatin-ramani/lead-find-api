"""Business CRUD: pagination, search, filtering, sorting, bulk delete."""

import pytest

from database import crud
from database.models import Business, WebsiteData


class TestSaveBusiness:
    def test_saves_and_returns_true(self, db):
        saved = crud.save_business(
            db, "Acme", "123", "a@b.test", "https://acme.test",
            "Ahmedabad", "commercial", "1 Road", "Has Website", "pid-1",
        )

        assert saved is True
        assert db.query(Business).count() == 1

    def test_duplicate_place_id_is_rejected(self, db):
        args = ("Acme", "123", None, None, "Ahmedabad", "commercial",
                "1 Road", "No Website", "pid-1")

        assert crud.save_business(db, *args) is True
        assert crud.save_business(db, *args) is False
        assert db.query(Business).count() == 1

    def test_null_place_id_is_not_deduplicated(self, db):
        """
        Documented behaviour: `WHERE place_id = NULL` never matches, so rows
        without a place_id are inserted every time. Guarded explicitly so a
        future change to this rule is a conscious one.
        """

        args = ("Acme", None, None, None, "X", "c", "a", "No Website", None)

        assert crud.save_business(db, *args) is True
        assert crud.save_business(db, *args) is True
        assert db.query(Business).count() == 2


class TestGetBusinessesPagination:
    def test_envelope_shape(self, db, sample_businesses):
        result = crud.get_businesses(db)

        assert set(result) == {"success", "data", "pagination"}
        assert set(result["pagination"]) == {
            "page", "pageSize", "totalItems", "totalPages",
        }
        assert result["success"] is True

    def test_pages_partition_the_result_set(self, db, sample_businesses):
        seen = []

        for page in (1, 2, 3, 4):
            seen += [b.id for b in crud.get_businesses(
                db, page=page, page_size=3)["data"]]

        assert sorted(seen) == sorted(b.id for b in sample_businesses)
        assert len(seen) == len(set(seen)), "a row appeared on two pages"

    def test_total_pages_arithmetic(self, db, sample_businesses):
        page = crud.get_businesses(db, page=1, page_size=3)["pagination"]

        assert page["totalItems"] == 10
        assert page["totalPages"] == 4  # ceil(10 / 3)

    def test_page_past_the_end_is_empty_but_not_an_error(self, db, sample_businesses):
        result = crud.get_businesses(db, page=99, page_size=5)

        assert result["data"] == []
        assert result["pagination"]["totalItems"] == 10

    def test_empty_table_reports_one_page(self, db):
        result = crud.get_businesses(db)

        assert result["data"] == []
        assert result["pagination"]["totalItems"] == 0
        assert result["pagination"]["totalPages"] == 1

    @pytest.mark.parametrize("page,expected", [(0, 1), (-5, 1), (1, 1)])
    def test_page_is_clamped_to_at_least_one(self, db, sample_businesses, page, expected):
        assert crud.get_businesses(db, page=page)["pagination"]["page"] == expected

    def test_page_size_is_clamped_to_the_maximum(self, db, sample_businesses):
        result = crud.get_businesses(db, page_size=99_999)

        assert result["pagination"]["pageSize"] == crud.MAX_PAGE_SIZE

    def test_zero_page_size_is_clamped_to_one(self, db, sample_businesses):
        assert crud.get_businesses(db, page_size=0)["pagination"]["pageSize"] == 1


class TestSearch:
    def test_matches_name_case_insensitively(self, db, sample_businesses):
        assert crud.get_businesses(db, search="BUSINESS 01")["pagination"]["totalItems"] == 1

    def test_matches_phone_email_and_website(self, db, business_factory):
        business_factory(
            name="Findable", phone="+91 55555 00000",
            email="unique@needle.test", website="https://needle.test",
            place_id="needle",
        )

        for term in ("55555", "needle.test", "unique@needle.test"):
            found = crud.get_businesses(db, search=term)["data"]
            assert [b.name for b in found] == ["Findable"], term

    def test_no_match_returns_empty(self, db, sample_businesses):
        assert crud.get_businesses(db, search="zzz-nothing")["data"] == []

    @pytest.mark.parametrize("term", ["%", "_", "100%", "a_b"])
    def test_like_wildcards_are_escaped(self, db, business_factory, term):
        """A literal % or _ must not behave as a SQL LIKE wildcard."""

        business_factory(name=f"Exact {term} Match", place_id=f"pid-{term}")
        business_factory(name="Unrelated", place_id="pid-other")

        found = crud.get_businesses(db, search=term)["data"]

        assert [b.name for b in found] == [f"Exact {term} Match"]

    def test_blank_search_is_ignored(self, db, sample_businesses):
        assert crud.get_businesses(db, search="   ")["pagination"]["totalItems"] == 10


class TestFilters:
    def test_city(self, db, sample_businesses):
        result = crud.get_businesses(db, city="Surat")

        assert {b.city for b in result["data"]} == {"Surat"}

    def test_category(self, db, sample_businesses):
        result = crud.get_businesses(db, category="retail")

        assert {b.category for b in result["data"]} == {"retail"}

    def test_status(self, db, sample_businesses):
        result = crud.get_businesses(db, status="No Website")

        assert {b.status for b in result["data"]} == {"No Website"}

    def test_filters_combine_as_and(self, db, sample_businesses):
        result = crud.get_businesses(db, city="Surat", category="retail")

        for business in result["data"]:
            assert business.city == "Surat"
            assert business.category == "retail"

    def test_unknown_filter_value_returns_nothing(self, db, sample_businesses):
        assert crud.get_businesses(db, city="Atlantis")["data"] == []


class TestSorting:
    @pytest.mark.parametrize(
        "sort_by", ["id", "name", "city", "category", "status"]
    )
    @pytest.mark.parametrize("sort_order", ["asc", "desc"])
    def test_every_allowed_sort(self, db, sample_businesses, sort_by, sort_order):
        rows = crud.get_businesses(
            db, page_size=100, sort_by=sort_by, sort_order=sort_order
        )["data"]

        values = [getattr(r, sort_by) or "" for r in rows]

        assert values == sorted(values, reverse=(sort_order == "desc"))

    def test_default_is_newest_first(self, db, sample_businesses):
        ids = [b.id for b in crud.get_businesses(db, page_size=100)["data"]]

        assert ids == sorted(ids, reverse=True)

    @pytest.mark.parametrize(
        "bad", ["place_id", "phone", "", "   ", None, "DROP TABLE", "id; --"]
    )
    def test_unknown_sort_column_falls_back_to_default(self, db, sample_businesses, bad):
        expected = [b.id for b in crud.get_businesses(db, page_size=100)["data"]]
        actual = [
            b.id for b in crud.get_businesses(db, page_size=100, sort_by=bad)["data"]
        ]

        assert actual == expected

    @pytest.mark.parametrize("bad", ["ascending", "up", "", None, "DESC; --"])
    def test_unknown_sort_order_falls_back_to_desc(self, db, sample_businesses, bad):
        ids = [
            b.id for b in crud.get_businesses(db, page_size=100, sort_order=bad)["data"]
        ]

        assert ids == sorted(ids, reverse=True)

    def test_non_unique_sort_is_stable_across_pages(self, db, sample_businesses):
        """City repeats, so paging needs a tiebreaker or rows shift between pages."""

        paged = []
        for page in (1, 2, 3, 4):
            paged += [
                b.id for b in crud.get_businesses(
                    db, page=page, page_size=3, sort_by="city", sort_order="asc"
                )["data"]
            ]

        full = [
            b.id for b in crud.get_businesses(
                db, page_size=100, sort_by="city", sort_order="asc"
            )["data"]
        ]

        assert paged == full
        assert len(paged) == len(set(paged))

    def test_case_sensitivity_of_text_sort_is_documented(self, db, business_factory):
        """
        SQLite sorts text with BINARY collation, so uppercase precedes
        lowercase. Pinned so a future move to case-insensitive ordering is
        deliberate rather than accidental.
        """

        business_factory(name="apple", place_id="p-lower")
        business_factory(name="Zebra", place_id="p-upper")

        names = [
            b.name for b in crud.get_businesses(
                db, sort_by="name", sort_order="asc"
            )["data"]
        ]

        assert names == ["Zebra", "apple"]


class TestGetAndDelete:
    def test_get_by_id(self, db, business_factory):
        business = business_factory(name="Target")

        assert crud.get_business_by_id(db, business.id).name == "Target"

    def test_get_missing_id_returns_none(self, db):
        assert crud.get_business_by_id(db, 9999) is None

    def test_delete_one(self, db, business_factory):
        business = business_factory()

        assert crud.delete_business(db, business.id) is True
        assert crud.delete_business(db, business.id) is False

    def test_delete_cascades_to_website_data(self, db, business_factory):
        business = business_factory()
        db.add(WebsiteData(business_id=business.id, status="Completed"))
        db.commit()

        crud.delete_business(db, business.id)

        assert db.query(WebsiteData).count() == 0, "orphaned website_data row"

    def test_get_businesses_by_ids_deduplicates_and_ignores_unknown(
        self, db, sample_businesses
    ):
        found = crud.get_businesses_by_ids(db, [1, 1, 2, 9999])

        assert [b.id for b in found] == [1, 2]

    def test_get_businesses_by_ids_empty_input(self, db, sample_businesses):
        assert crud.get_businesses_by_ids(db, []) == []


class TestBulkDelete:
    def test_deletes_and_counts(self, db, sample_businesses):
        assert crud.delete_businesses(db, [1, 2, 3]) == 3
        assert db.query(Business).count() == 7

    def test_duplicates_collapse_and_unknown_ids_ignored(self, db, sample_businesses):
        assert crud.delete_businesses(db, [1, 1, 2, 9999, 10000]) == 2

    def test_is_idempotent(self, db, sample_businesses):
        assert crud.delete_businesses(db, [1]) == 1
        assert crud.delete_businesses(db, [1]) == 0

    def test_empty_and_all_unknown_return_zero(self, db, sample_businesses):
        assert crud.delete_businesses(db, []) == 0
        assert crud.delete_businesses(db, [500, 501]) == 0
        assert db.query(Business).count() == 10

    def test_cascades_to_website_data(self, db, sample_businesses):
        for business in sample_businesses[:3]:
            db.add(WebsiteData(business_id=business.id, status="Completed"))
        db.commit()

        crud.delete_businesses(db, [b.id for b in sample_businesses[:3]])

        assert db.query(WebsiteData).count() == 0

    def test_failure_part_way_leaves_nothing_deleted(self, db, sample_businesses, monkeypatch):
        """The whole batch must be one transaction."""

        ids = [b.id for b in sample_businesses]
        original = db.delete
        calls = {"n": 0}

        def exploding(obj):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("simulated failure mid-batch")
            return original(obj)

        monkeypatch.setattr(db, "delete", exploding)

        with pytest.raises(RuntimeError):
            crud.delete_businesses(db, ids)

        db.rollback()

        assert db.query(Business).count() == len(ids), "partial delete committed"
