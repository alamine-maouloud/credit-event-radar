"""SQLite repository tests on a temporary database."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from radar.audit import AuditEntry, stable_hash
from radar.config import CONFIG_DIR, SEEDS_DIR, load_ratings_seed, load_universe
from radar.db import SCHEMA_VERSION, Database
from radar.models import CreditEvent, EvidenceSpan, RawDocument

HEX = "ab" * 32


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "radar.db")
    database.init_schema()
    yield database
    database.close()


def doc(text: str = "Hello world", content_hash: str = HEX, doc_id: str = "cd" * 32) -> RawDocument:
    return RawDocument(
        doc_id=doc_id,
        source_type="manual",
        url="https://example.invalid/doc",
        retrieved_at=datetime(2026, 10, 6, tzinfo=UTC),
        content_hash=content_hash,
        raw_size_bytes=len(text),
        normalizer_version="test-0",
        text=text,
        raw_path="/tmp/x",
        extra={"form": "10-Q"},
    )


def test_schema_version(db: Database):
    assert db.schema_version() == SCHEMA_VERSION
    db.init_schema()  # idempotent
    assert db.count("issuers") == 0


def test_universe_and_seed_roundtrip(db: Database, scales):
    universe = load_universe(CONFIG_DIR / "universe.yaml")
    seed = load_ratings_seed(SEEDS_DIR / "ratings_seed.csv", scales, universe)
    assert db.replace_universe(universe) == len(universe.issuers)
    assert db.replace_universe(universe) == len(universe.issuers)  # idempotent
    assert db.issuer_ids() == universe.ids
    assert db.count("issuer_aliases") >= len(universe.issuers)
    assert db.replace_ratings(seed) == len(seed)
    vw = db.ratings_for("VOLKSWAGEN")
    assert len(vw) == 4
    assert vw[0] == next(
        r for r in seed if r.issuer_id == "VOLKSWAGEN" and r.agency == vw[0].agency
    )


def test_document_insert_dedup_and_roundtrip(db: Database):
    d = doc()
    assert db.insert_document(d) is True
    assert db.insert_document(d) is False  # same raw bytes
    assert db.insert_document(doc(doc_id="ef" * 32)) is False  # same bytes, other normaliser
    assert db.count("documents") == 1
    stored = db.get_document(d.doc_id)
    assert stored == d
    assert [x.doc_id for x in db.unprocessed_documents()] == [d.doc_id]
    db.mark_resolved(d.doc_id, "ISSUER_TEST_A", "cik")
    db.mark_processed(d.doc_id)
    assert db.unprocessed_documents() == []
    status = db.document_status(d.doc_id)
    assert status["issuer_id"] == "ISSUER_TEST_A" and status["resolution"] == "cik"
    assert status["processed_at"]


def test_event_roundtrip_and_merge(db: Database):
    d = doc()
    db.insert_document(d)
    span = EvidenceSpan(
        doc_id=d.doc_id,
        char_start=0,
        char_end=5,
        quote="Hello",
        extractor_version="test-0",
        match_score=100.0,
        field="old_rating",
    )
    ev = CreditEvent(
        event_id="evt1",
        issuer_id="ISSUER_TEST_A",
        family="rating",
        event_type="downgrade",
        effective_date=date(2026, 7, 8),
        fields={"agency": "SP", "old_rating": "BBB-", "new_rating": "BB+"},
        evidence=[span],
        extraction_method="structured",
        source_doc_ids=[d.doc_id],
    )
    db.insert_event(ev)
    assert db.get_event("evt1") == ev
    assert db.list_events("ISSUER_TEST_A") == [ev]
    db.merge_event_sources("evt1", ["other"])
    assert db.get_event("evt1").source_doc_ids == [d.doc_id, "other"]
    with pytest.raises(KeyError):
        db.merge_event_sources("missing", ["x"])


def test_audit_log(db: Database):
    db.audit(AuditEntry(step="ingest", doc_id="d1", inputs_hash=stable_hash({"a": 1}), status="ok"))
    db.audit(AuditEntry(step="resolve", doc_id="d1", status="skipped", message="unresolved"))
    entries = db.audit_entries(doc_id="d1")
    assert [e["step"] for e in entries] == ["ingest", "resolve"]
    assert entries[1]["message"] == "unresolved"
    assert stable_hash({"b": 2, "a": 1}) == stable_hash({"a": 1, "b": 2})


def test_count_rejects_unknown_table(db: Database):
    with pytest.raises(ValueError):
        db.count("sqlite_master")
