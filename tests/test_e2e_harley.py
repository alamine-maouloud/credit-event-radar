"""Phase 2 exit criterion: the real Harley-Davidson 10-Q through the CLI, offline, no LLM.

Ingest the golden fixture, keep raw bytes with URL, timestamps and hashes, resolve the
issuer by CIK, extract the rating action deterministically, store the event with exact
evidence spans and an audit trail, and trace it back to the source passage.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from radar.cli import app
from radar.connectors.fixture import load_fixture
from radar.db import Database
from radar.extract.spans import verify_span

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "edgar"
HARLEY = FIXTURES / "harley_davidson_inc_10q_2026q2"
SYNTHETIC = FIXTURES / "synthetic_issuer_test_a_10q"
runner = CliRunner()


def run(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("e2e")
    db = str(tmp / "radar.db")
    raw = str(tmp / "raw")
    common = ("--db", db)
    out = {
        "init": run("init-db", *common),
        "seed": run("seed", *common),
        "ingest": run(
            "ingest",
            "--since",
            "2026-01-01",
            "--source",
            "fixtures",
            "--fixtures-dir",
            str(FIXTURES),
            "--raw-dir",
            raw,
            *common,
        ),
        "ingest_url": run(
            "ingest",
            "--since",
            "2026-01-01",
            "--source",
            "fixtures",
            "--fixtures-dir",
            str(FIXTURES),
            "--raw-dir",
            raw,
            "--url",
            load_fixture(SYNTHETIC).manifest["source_url"],
            *common,
        ),
        "process": run("process", *common),
        "events": run("events", *common),
    }
    out["db"] = db
    out["raw"] = raw
    out["common"] = common
    return out


def test_cli_outputs(pipeline):
    assert "schema 2 ready" in pipeline["init"]
    assert "issuers" in pipeline["seed"]
    assert "fetched 1, stored 1, duplicates 0" in pipeline["ingest"]
    assert "fetched 1, stored 1, duplicates 0" in pipeline["ingest_url"]
    assert "resolved 1, unresolved 1" in pipeline["process"]
    assert "events new 1" in pipeline["process"]
    assert "HARLEY_DAVIDSON_INC  rating/downgrade  2026-07-08  SP BBB- to BB+" in pipeline["events"]


def test_document_provenance_matches_the_manifest(pipeline):
    manifest = load_fixture(HARLEY).manifest
    db = Database(pipeline["db"])
    doc = db.get_document(manifest["normalized_sha256"])
    assert doc is not None
    assert doc.content_hash == manifest["raw_sha256"]
    assert doc.raw_size_bytes == manifest["raw_size_bytes"]
    assert str(doc.url) == manifest["source_url"]
    assert doc.retrieved_at.isoformat() == manifest["retrieved_at"]
    assert doc.normalizer_version == manifest["normalizer_version"]
    assert (
        Path(doc.raw_path).exists()
        and Path(doc.raw_path).stat().st_size == manifest["raw_size_bytes"]
    )
    assert doc.extra["accession_number"] == "0000793952-26-000061"
    status = db.document_status(doc.doc_id)
    assert (status["issuer_id"], status["resolution"]) == ("HARLEY_DAVIDSON_INC", "cik")
    db.close()


def test_event_is_the_fallen_angel_without_llm(pipeline):
    db = Database(pipeline["db"])
    events = db.list_events("HARLEY_DAVIDSON_INC")
    assert len(events) == 1
    ev = events[0]
    assert (ev.family, ev.event_type, ev.effective_date) == (
        "rating",
        "downgrade",
        date(2026, 7, 8),
    )
    f = ev.fields
    assert (f["agency"], f["old_rating"], f["new_rating"], f["new_outlook"]) == (
        "SP",
        "BBB-",
        "BB+",
        "stable",
    )
    assert f["old_category"] == "IG" and f["new_category"] == "HY" and f["crosses_ig_to_hy"] is True
    assert ev.extraction_method == "structured"
    doc = db.get_document(ev.source_doc_ids[0])
    assert all(verify_span(doc, s) for s in ev.evidence)
    sentence = next(s for s in ev.evidence if s.field is None)
    assert (
        "S&P Global Ratings lowered the Company's short-term credit rating from A-3 to B, lowered the Company's long-term credit rating from BBB- to BB+"
        in sentence.quote
    )
    manifest = load_fixture(HARLEY).manifest
    anchor = manifest["anchors"][0]
    assert sentence.char_start <= anchor["char_start"] and anchor["char_end"] <= sentence.char_end
    db.close()


def test_audit_trail_covers_every_step(pipeline):
    db = Database(pipeline["db"])
    ev = db.list_events("HARLEY_DAVIDSON_INC")[0]
    doc_id = ev.source_doc_ids[0]
    steps = [(e["step"], e["status"]) for e in db.audit_entries(doc_id=doc_id)]
    assert steps == [("ingest", "ok"), ("resolve", "ok"), ("extract", "ok"), ("process", "ok")]
    entries = db.audit_entries(event_id=ev.event_id)
    assert entries and entries[0]["outputs_hash"] and entries[0]["model_id"] is None
    db.close()


def test_unknown_issuer_fixture_produces_no_event(pipeline):
    db = Database(pipeline["db"])
    assert db.count("events") == 1
    unresolved = [
        e for e in db.audit_entries() if e["step"] == "resolve" and e["status"] == "skipped"
    ]
    assert len(unresolved) == 1 and "ISSUER_TEST_A" in unresolved[0]["message"]
    db.close()


def test_show_event_traces_back_to_source(pipeline):
    db = Database(pipeline["db"])
    ev = db.list_events("HARLEY_DAVIDSON_INC")[0]
    db.close()
    out = run("show-event", ev.event_id, *pipeline["common"])
    assert "Issuer: HARLEY_DAVIDSON_INC" in out
    assert "[old_rating] sentence" in out and "BBB-" in out
    assert "[new_rating] sentence" in out
    assert (
        "https://www.sec.gov/Archives/edgar/data/793952/000079395226000061/hog-20260630.htm" in out
    )
    assert "raw sha256 " + load_fixture(HARLEY).manifest["raw_sha256"] in out
    assert "LLM used for: none" in out


def test_second_run_is_idempotent(pipeline):
    out = run(
        "ingest",
        "--since",
        "2026-01-01",
        "--source",
        "fixtures",
        "--fixtures-dir",
        str(FIXTURES),
        "--raw-dir",
        pipeline["raw"],
        *pipeline["common"],
    )
    assert "fetched 1, stored 0, duplicates 1" in out
    out = run("process", *pipeline["common"])
    assert "documents 0" in out
    db = Database(pipeline["db"])
    assert db.count("events") == 1 and db.count("documents") == 2
    db.close()


def test_ingest_by_url_dedups_against_existing(pipeline):
    url = load_fixture(HARLEY).manifest["source_url"]
    out = run(
        "ingest",
        "--since",
        "2026-01-01",
        "--source",
        "fixtures",
        "--fixtures-dir",
        str(FIXTURES),
        "--raw-dir",
        pipeline["raw"],
        "--url",
        url,
        *pipeline["common"],
    )
    assert "duplicates 1" in out


def test_manifest_is_json(pipeline):
    assert json.loads((HARLEY / "manifest.json").read_text())["issuer_id"] == "HARLEY_DAVIDSON_INC"
