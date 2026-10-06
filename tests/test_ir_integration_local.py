"""Local integration tests on real issuer documents (private fixtures, ADR-010).

Each directory under tests/fixtures/ir holds a committed manifest; the bytes are fetched
locally with scripts/fetch_ir_fixture.py. When the bytes are absent the test is skipped,
so the public suite never depends on copyrighted material.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from radar.config import (
    CONFIG_DIR,
    SEEDS_DIR,
    load_rating_scales,
    load_ratings_seed,
    load_rules,
    load_universe,
)
from radar.connectors.fixture import (
    FixtureAdapter,
    fixture_bytes_available,
    list_fixtures,
    load_manifest,
)
from radar.db import Database
from radar.extract.spans import verify_span
from radar.normalize import SecHtmlNormalizer
from radar.pipeline import ingest, process

IR_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ir"
DIRS = list_fixtures(IR_FIXTURES) if IR_FIXTURES.exists() else []


@pytest.mark.parametrize("directory", DIRS, ids=[d.name for d in DIRS])
def test_real_document_replays_expected_extraction(directory: Path, tmp_path: Path):
    if not fixture_bytes_available(directory):
        pytest.skip(f"private fixture bytes not present for {directory.name}")
    manifest = load_manifest(directory)
    expected = manifest["expected"]
    universe = load_universe(CONFIG_DIR / "universe.yaml")
    scales = load_rating_scales(CONFIG_DIR / "rating_scales.yaml")
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    db = Database(tmp_path / "r.db")
    db.init_schema()
    db.replace_universe(universe)
    db.replace_ratings(load_ratings_seed(SEEDS_DIR / "ratings_seed.csv", scales, universe))
    adapter = FixtureAdapter(directory, tmp_path / "raw", SecHtmlNormalizer())
    from datetime import date

    summary = ingest(db, adapter, since=date(2000, 1, 1), issuers=universe.issuers)
    assert summary.stored == 1
    doc = db.get_document(summary.doc_ids[0])
    assert (
        doc.content_hash == manifest["raw_sha256"] and doc.doc_id == manifest["normalized_sha256"]
    )
    process(db, universe, scales, rules)

    issuer_id = manifest["issuer_id"]
    assert db.document_status(doc.doc_id)["outcome"] == expected["outcome"]
    observations = [
        {"agency": o.agency, "rating": o.rating, "outlook": o.outlook, "watch": o.watch,
         "rating_date": o.rating_date.isoformat() if o.rating_date else None, "as_of_basis": o.as_of_basis}
        for o in db.observations_for(issuer_id)
    ]  # fmt: skip
    assert observations == expected["observations"]
    events = db.list_events(issuer_id)
    assert [(e.family, e.event_type) for e in events] == [
        (e["family"], e["event_type"]) for e in expected["events"]
    ]
    for stored, exp in zip(events, expected["events"], strict=True):
        assert (stored.effective_date.isoformat() if stored.effective_date else None) == exp[
            "effective_date"
        ]
        for key, value in exp["fields"].items():
            assert stored.fields.get(key) == value, key
        assert all(verify_span(doc, s) for s in stored.evidence)
    decisions = [
        {"event_type": e.event_type, "final_priority": (db.get_decision(e.event_id) or None) and db.get_decision(e.event_id).final_priority,
         "triggered": db.get_decision(e.event_id).triggered_ids()}
        for e in events
    ]  # fmt: skip
    assert decisions == expected["decisions"]
    db.close()
