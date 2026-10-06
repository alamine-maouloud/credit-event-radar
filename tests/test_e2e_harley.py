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
            "--issuer",
            "HARLEY_DAVIDSON_INC",  # the directory also holds the private stress-case filings
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
            "--issuer",
            "HARLEY_DAVIDSON_INC",
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
    assert "schema 9 ready" in pipeline["init"]
    assert "issuers" in pipeline["seed"]
    assert "fetched 1, stored 1, duplicates 0" in pipeline["ingest"]
    assert "fetched 1, stored 1, duplicates 0" in pipeline["ingest_url"]
    assert "resolved 1, unresolved 1" in pipeline["process"]
    assert "events new 1" in pipeline["process"]
    assert "observations 3" in pipeline["process"]
    assert "decisions: P1 1" in pipeline["process"]
    assert (
        "P1         HARLEY_DAVIDSON_INC  rating/downgrade  2026-07-08  SP BBB- to BB+"
        in pipeline["events"]
    )


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
    assert steps == [
        ("ingest", "ok"), ("resolve", "ok"), ("observe", "ok"), ("observe", "ok"), ("observe", "ok"),
        ("extract", "ok"), ("process", "ok"),
    ]  # fmt: skip
    assert db.document_status(doc_id)["outcome"] == "EVENTS"
    entries = db.audit_entries(event_id=ev.event_id)
    assert [e["step"] for e in entries] == ["extract", "materiality"]
    assert entries[1]["rules_version"] == "1.5" and entries[1]["model_id"] is None
    assert "P1 (DECIDED) via RAT-01, RAT-02" in entries[1]["message"]
    db.close()


def test_observations_from_the_ratings_table(pipeline):
    db = Database(pipeline["db"])
    obs = {o.agency: o for o in db.observations_for("HARLEY_DAVIDSON_INC")}
    assert set(obs) == {"SP", "MOODYS", "FITCH"}
    assert all(
        o.as_of == date(2026, 6, 30) and o.verification_method == "structured_table"
        for o in obs.values()
    )
    assert all(
        o.as_of_basis == "stated" and o.observed_at == date(2026, 8, 5) for o in obs.values()
    )
    assert (obs["MOODYS"].rating, obs["FITCH"].rating, obs["SP"].rating) == ("Baa3", "BBB", "BBB-")
    assert obs["SP"].watch == "negative"
    doc = db.get_document(obs["SP"].doc_id)
    assert all(verify_span(doc, o.evidence) for o in obs.values())
    assert obs["SP"].evidence.evidence_type == "table_row" and obs[
        "SP"
    ].evidence_span_id.startswith("span_")
    db.close()


def test_decision_is_p1_by_rat01_and_rat02(pipeline):
    db = Database(pipeline["db"])
    ev = db.list_events("HARLEY_DAVIDSON_INC")[0]
    d = db.get_decision(ev.event_id)
    assert d is not None
    assert (d.final_priority, d.base_priority, d.decision_status) == ("P1", "P1", "DECIDED")
    assert d.triggered_ids() == ["RAT-01", "RAT-02"]
    assert d.effective_date == date(2026, 7, 8)
    after = d.state_after
    assert set(after.entries) == {"SP", "MOODYS", "FITCH"}
    assert (
        after.entries["SP"].rating,
        after.entries["SP"].category,
        after.entries["SP"].origin,
    ) == ("BB+", "HY", "event")
    for agency, rating in (("MOODYS", "Baa3"), ("FITCH", "BBB")):
        e = after.entries[agency]
        assert (e.rating, e.category, e.as_of, e.age_days, e.origin, e.verification) == (
            rating,
            "IG",
            date(2026, 6, 30),
            8,
            "observation",
            "structured_table",
        )
    rat02 = next(r for r in d.rules if r.id == "RAT-02")
    assert rat02.data["other_ig_agencies"] == ["FITCH", "MOODYS"]
    assert d.provenance.composite_used is False and d.provenance.llm_used == "none"
    assert not [m for m in d.modifiers if m.applied]
    db.close()


def test_explain_output(pipeline):
    db = Database(pipeline["db"])
    ev = db.list_events("HARLEY_DAVIDSON_INC")[0]
    db.close()
    out = run("show-event", ev.event_id, "--explain", *pipeline["common"])
    assert out.startswith("FINAL PRIORITY: P1\n")
    assert "RAT-01  TRUE" in out and "RAT-02  TRUE" in out
    assert "S&P Global Ratings BBB- -> BB+, IG -> HY" in out
    assert "Moody's\n  Baa3  IG" in out and "as_of: 2026-06-30" in out and "age: 8 days" in out
    assert "Fitch\n  BBB  IG" in out
    assert "RAT-03  FALSE" in out and "MOD-01  NOT APPLIED" in out and "already HY" in out
    assert (
        "Agency ratings used: YES" in out
        and "Composite rating used: NO" in out
        and "LLM used: NO" in out
    )
    assert (
        "https://www.sec.gov/Archives/edgar/data/793952/000079395226000061/hog-20260630.htm" in out
    )
    assert "raw sha256 " + load_fixture(HARLEY).manifest["raw_sha256"] in out
    assert "[new_rating] 228641-228644: BB+" in out


def test_decide_recomputes_identically(pipeline):
    db = Database(pipeline["db"])
    ev = db.list_events("HARLEY_DAVIDSON_INC")[0]
    before = db.get_decision(ev.event_id).model_dump(mode="json")
    db.close()
    out = run("decide", *pipeline["common"])
    assert "decisions: P1 1" in out
    db = Database(pipeline["db"])
    after = db.get_decision(ev.event_id).model_dump(mode="json")
    db.close()
    assert before == after


def test_unknown_issuer_fixture_produces_no_event(pipeline):
    db = Database(pipeline["db"])
    assert db.count("events") == 1
    unresolved = [
        e for e in db.audit_entries() if e["step"] == "resolve" and e["status"] == "skipped"
    ]
    assert len(unresolved) == 1 and "ISSUER_TEST_A" in unresolved[0]["message"]
    assert {d["outcome"] for d in db.list_document_status()} == {"EVENTS", "UNRESOLVED"}
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
    assert "Priority: P1 (DECIDED), deterministic rules engine." in out


def test_second_run_is_idempotent(pipeline):
    out = run(
        "ingest",
        "--since",
        "2026-01-01",
        "--source",
        "fixtures",
        "--fixtures-dir",
        str(FIXTURES),
        "--issuer",
        "HARLEY_DAVIDSON_INC",
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
        "--issuer",
        "HARLEY_DAVIDSON_INC",
        "--raw-dir",
        pipeline["raw"],
        "--url",
        url,
        *pipeline["common"],
    )
    assert "duplicates 1" in out


def test_manifest_is_json(pipeline):
    assert json.loads((HARLEY / "manifest.json").read_text())["issuer_id"] == "HARLEY_DAVIDSON_INC"


def test_alert_command_renders_the_p1_locally_without_sending(pipeline, tmp_path):
    """Phase P1: the Alert object of the real Harley P1, rendered as JSON, HTML and an
    Adaptive Card, nothing sent, the audit trail records the files and the route."""
    db = Database(pipeline["db"])
    ev = db.list_events("HARLEY_DAVIDSON_INC")[0]
    db.close()
    out = run("alert", ev.event_id, "--out", str(tmp_path / "alerts"), *pipeline["common"])
    assert "P1    Harley-Davidson" in out and "BBB- → BB+, IG → HY" in out
    assert "nothing sent (local rendering only)" in out and "1 alert(s) rendered" in out
    folder = next((tmp_path / "alerts").iterdir())
    files = sorted(p.name for p in folder.iterdir())
    assert files == [f"{ev.event_id}.card.json", f"{ev.event_id}.html", f"{ev.event_id}.json"]
    data = json.loads((folder / f"{ev.event_id}.json").read_text())
    assert data["priority"] == "P1" and data["universe"] == "historical_stress_case"
    assert [r["id"] for r in data["triggered_rules"]] == ["RAT-01", "RAT-02"]
    assert data["decision_provenance"]["composite_used"] is False
    assert data["sources"][0]["raw_sha256"] == load_fixture(HARLEY).manifest["raw_sha256"]
    assert any(
        "lowered the Company's long-term credit rating from BBB- to BB+" in f["text"]
        for f in data["facts"]
    )
    html = (folder / f"{ev.event_id}.html").read_text()
    assert "Why this priority?" in html and "RAT-02" in html and "Historical stress case" in html
    card = json.loads((folder / f"{ev.event_id}.card.json").read_text())
    assert card["type"] == "AdaptiveCard" and any(
        a["title"] == "Source [1]" for a in card["actions"]
    )
    db = Database(pipeline["db"])
    entries = [e for e in db.audit_entries(event_id=ev.event_id) if e["step"] == "alert"]
    db.close()
    assert (
        entries and "route teams, email, local (immediate); nothing sent" in entries[-1]["message"]
    )
