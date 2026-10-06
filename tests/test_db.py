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


def test_observation_and_decision_roundtrip(db: Database, scales):
    from radar.config import CONFIG_DIR, load_rules
    from radar.materiality.engine import MaterialityEngine
    from radar.models import RatingObservation
    from tests.materiality_helpers import candidate, rating_event, state

    span = EvidenceSpan(
        doc_id="d" * 64,
        char_start=0,
        char_end=5,
        quote="Hello",
        evidence_type="table_row",
        extractor_version="t",
        match_score=100.0,
        field="rating_row",
    )
    obs = RatingObservation(
        observation_id="obs_1", issuer_id="ISSUER_TEST_A", agency="MOODYS", rating="Baa3", outlook="stable", watch="none",
        rating_type="long_term_issuer", scope="issuer", as_of=date(2026, 6, 30), doc_id="d" * 64, evidence_span_id="span_1",
        evidence=span, extractor_version="t", verification_method="structured_table",
    )  # fmt: skip
    assert db.insert_observation(obs) is True and db.insert_observation(obs) is False
    assert db.observations_for("ISSUER_TEST_A") == [obs]

    rules = load_rules(CONFIG_DIR / "rules.yaml")
    decision = MaterialityEngine(rules, scales).evaluate(
        rating_event("SP", "BBB-", "BB+"), state(rules, scales, candidate("MOODYS", "Baa3"))
    )
    db.upsert_decision(decision)
    assert db.get_decision(decision.event_id) == decision
    assert db.priority_of(decision.event_id) == ("P1", "DECIDED")
    assert db.decisions_for("ISSUER_TEST_A")[0].event_id == decision.event_id
    db.upsert_decision(decision)  # idempotent replace
    assert db.count("priority_decisions") == 1


def test_count_rejects_unknown_table(db: Database):
    with pytest.raises(ValueError):
        db.count("sqlite_master")
