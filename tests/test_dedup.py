"""Event deduplication against the database."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from radar.db import Database
from radar.dedup import business_key, find_duplicate
from radar.models import CreditEvent


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "r.db")
    database.init_schema()
    yield database
    database.close()


def event(event_id: str, effective: date | None, **fields) -> CreditEvent:
    base = {"agency": "SP", "old_rating": "BBB-", "new_rating": "BB+"}
    base.update(fields)
    return CreditEvent(
        event_id=event_id, issuer_id="ISSUER_TEST_A", family="rating", event_type="downgrade",
        effective_date=effective, fields=base, extraction_method="structured", source_doc_ids=["d1"],
    )  # fmt: skip


def test_same_id_is_duplicate(db):
    db.insert_event(event("e1", date(2026, 7, 8)))
    assert find_duplicate(db, event("e1", date(2026, 7, 8))).event_id == "e1"


def test_date_window(db):
    db.insert_event(event("e1", date(2026, 7, 8)))
    assert find_duplicate(db, event("e2", date(2026, 7, 10))) is not None
    assert find_duplicate(db, event("e3", date(2026, 7, 11))) is None
    assert find_duplicate(db, event("e4", None)) is None


def test_both_dates_unknown_match(db):
    db.insert_event(event("e1", None))
    assert find_duplicate(db, event("e2", None)) is not None


def test_different_key_fields_are_distinct(db):
    db.insert_event(event("e1", date(2026, 7, 8)))
    assert find_duplicate(db, event("e2", date(2026, 7, 8), new_rating="BB")) is None
    assert find_duplicate(db, event("e3", date(2026, 7, 8), agency="MOODYS")) is None
    assert business_key(event("x", None))[:3] == ("ISSUER_TEST_A", "rating", "downgrade")
