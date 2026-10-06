"""Pipeline dispatch by document kind, outcomes and NO_EVENT, on in-memory documents."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from radar.audit import AuditEntry
from radar.config import CONFIG_DIR, Issuer, Universe, load_rules
from radar.db import Database
from radar.models import RawDocument
from radar.pipeline import process
from radar.snapshot import sha256_hex

ISSUER = Issuer(
    id="ISSUER_TEST_A",
    name="Issuer Test A",
    legal_entity="Issuer Test A AG",
    aliases=["ITA"],
    sector="t",
    country="DE",
)
UNIVERSE = Universe(
    name="t", disclosure="fictional", retrieved_as_of=date(2026, 1, 1), issuers=[ISSUER]
)


def doc(
    text: str,
    *,
    document_type: str = "press_release",
    title: str | None = None,
    published: date | None = date(2026, 5, 6),
    **extra,
) -> RawDocument:
    extra = {"document_type": document_type, "source_id": "src", "kind": "rss", **extra}
    return RawDocument(
        doc_id=sha256_hex(text), source_type="ir_feed", url=f"https://example.invalid/{sha256_hex(text)[:8]}", title=title,
        published_at=datetime(published.year, published.month, published.day, tzinfo=UTC) if published else None,
        retrieved_at=datetime(2026, 10, 6, tzinfo=UTC), content_hash=sha256_hex(text.encode()), raw_size_bytes=len(text),
        normalizer_version="t", text=text, raw_path="p", issuer_hint="ISSUER_TEST_A", extra=extra,
    )  # fmt: skip


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "r.db")
    database.init_schema()
    database.replace_universe(UNIVERSE)
    yield database
    database.close()


def run(db, scales, *docs):
    for d in docs:
        assert db.insert_document(d)
    return process(db, UNIVERSE, scales, load_rules(CONFIG_DIR / "rules.yaml"))


def test_press_release_with_outlook_change_is_p2(db, scales):
    text = "Issuer Test A AG: Fitch Ratings has revised the Outlook on Issuer Test A AG's Long-Term IDR to Negative from Stable and affirmed the IDR at 'A-'."
    s = run(db, scales, doc(text, title="Fitch revises outlook"))
    assert (s.resolved, s.events_new, s.decisions) == (1, 1, {"P2": 1})
    ev = db.list_events("ISSUER_TEST_A")[0]
    assert ev.event_type == "outlook_change"
    d = db.get_decision(ev.event_id)
    assert d.triggered_ids() == ["RAT-06"] and d.final_priority == "P2"
    assert db.document_status(ev.source_doc_ids[0])["outcome"] == "EVENTS"


def test_ratings_page_gives_observations_only(db, scales):
    text = "Ratings\nFitch | Short-Term | Long-Term | Outlook\nIssuer Test A AG | F-1 | A- | Negative\nMoody's | Short-Term | Long-Term | Outlook\nIssuer Test A AG | P-2 | Baa1 | Stable"
    s = run(
        db,
        scales,
        doc(
            text,
            document_type="ratings_page",
            table_profile="current_by_agency",
            published=None,
            kind="page",
        ),
    )
    assert (s.events_new, s.observations_new, s.no_event) == (0, 2, 0)
    obs = db.observations_for("ISSUER_TEST_A")
    assert {o.agency for o in obs} == {"FITCH", "MOODYS"}
    assert all(o.as_of_basis == "retrieval" and o.observed_at == date(2026, 10, 6) for o in obs)
    status = db.list_document_status("ISSUER_TEST_A")[0]
    assert status["outcome"] == "OBSERVATIONS_ONLY"


def test_press_release_without_event_is_no_event(db, scales):
    text = "Issuer Test A AG opens a new plant in Lower Saxony. The site will employ 500 people."
    s = run(db, scales, doc(text, title="Issuer Test A opens a new plant"))
    assert (s.events_new, s.no_event) == (0, 1)
    status = db.list_document_status()[0]
    assert status["outcome"] == "NO_EVENT"
    entries = [e for e in db.audit_entries(doc_id=status["doc_id"]) if e["step"] == "no_event"]
    assert entries and "nothing inferred" in entries[0]["message"]
    assert db.count("events") == 0 and db.count("priority_decisions") == 0


def test_results_release_without_guidance_statement_has_null_priority(db, scales):
    text = "First quarter 2026\nIssuer Test A AG delivered solid results. Sales revenue rose by 2 percent."
    s = run(
        db,
        scales,
        doc(
            text,
            title="First quarter 2026: Issuer Test A delivers solid results",
            published=date(2026, 4, 30),
        ),
    )
    assert s.events_new == 1 and s.decisions == {"NONE": 1}
    ev = db.list_events("ISSUER_TEST_A")[0]
    assert ev.event_type == "earnings_release"
    d = db.get_decision(ev.event_id)
    assert d.final_priority is None and d.decision_status == "NO_APPLICABLE_RULE"


def test_green_bond_press_release_is_iss04(db, scales):
    text = "Issuer Test A AG successfully placed its first green bond of EUR 500 million with a term of six years."
    s = run(db, scales, doc(text, title="Issuer Test A places green bond"))
    assert s.decisions == {"P3": 1}
    ev = db.list_events("ISSUER_TEST_A")[0]
    assert ev.family == "issuance" and ev.fields["amount_eur_equiv"] == 500_000_000.0
    assert db.get_decision(ev.event_id).triggered_ids() == ["ISS-04"]


def test_hybrid_bond_is_iss02(db, scales):
    text = "Issuer Test A AG has placed EUR 750 million perpetual subordinated hybrid notes."
    s = run(db, scales, doc(text, title="Hybrid notes"))
    assert s.decisions == {"P2": 1}


def test_rating_report_pdf_kind_uses_sentence_extractor(db, scales):
    text = "Fitch Ratings - Frankfurt - 07 Apr 2025: Fitch Ratings has affirmed Issuer Test A AG's Long-Term IDR at 'A-' with a Stable Outlook."
    s = run(db, scales, doc(text, document_type="rating_report", kind="page_links", published=None))
    assert s.events_new == 1
    ev = db.list_events("ISSUER_TEST_A")[0]
    assert ev.event_type == "affirmation" and ev.effective_date == date(2025, 4, 7)
    assert db.get_decision(ev.event_id).triggered_ids() == ["RAT-10"]


def test_unresolved_document_outcome(db, scales):
    text = "Some other company reports."
    d = doc(text, title="Other").model_copy(update={"issuer_hint": "UNKNOWN_ISSUER"})
    s = run(db, scales, d)
    assert s.unresolved == 1
    assert db.list_document_status()[0]["outcome"] == "UNRESOLVED"


def test_audit_entry_helper_roundtrip(db):
    db.audit(AuditEntry(step="discover", status="skipped", message="x: disallowed by robots.txt"))
    assert db.audit_entries()[-1]["step"] == "discover"


def test_unreadable_text_is_reported_not_inferred(db, scales):
    garbled = "9;A =PT-Q=98&' -AYA:9? 9;A :&Q,='[I? -&SN?9 :=,89=B ?9-N:9N-A " * 20
    s = run(
        db, scales, doc(garbled, document_type="rating_report", kind="page_links", published=None)
    )
    assert s.events_new == 0 and s.no_event == 0
    status = db.list_document_status()[0]
    assert status["outcome"] == "UNREADABLE_TEXT"
    assert any("UNREADABLE_TEXT" in e["message"] for e in db.audit_entries(doc_id=status["doc_id"]))


def test_results_release_recap_of_a_bond_is_not_a_new_issuance(db, scales):
    text = (
        "First half 2026\nIssuer Test A AG confirms its outlook for 2026. "
        "In May, Issuer Test A placed its first green bond with a volume of EUR 500 million."
    )
    s = run(
        db,
        scales,
        doc(
            text,
            title="Issuer Test A improves profitability in the first half of 2026",
            published=date(2026, 7, 22),
        ),
    )
    events = db.list_events("ISSUER_TEST_A")
    assert [e.event_type for e in events] == ["earnings_release"]
    assert s.decisions == {"P3": 1}
    assert db.get_decision(events[0].event_id).triggered_ids() == ["ERN-04"]
    skipped = [
        e
        for e in db.audit_entries()
        if "issuance_mentioned_in_results_release" in (e["message"] or "")
    ]
    assert skipped


def test_agency_report_without_agency_in_sentence_uses_document_agency(db, scales):
    text = (
        "Research Update: Issuer Test A AG Outlook Revised To Negative; Affirmed At 'BBB+/A-2'\n"
        "December 17, 2025\n"
        "We therefore revised our outlook on Issuer Test A to negative from stable, and affirmed our 'BBB+/A-2' long- and short-term issuer credit ratings.\n"
        "Source: S&P Global Ratings. Copyright 2025 by Standard & Poor's Financial Services LLC."
    )
    s = run(db, scales, doc(text, document_type="rating_report", kind="page_links", published=None))
    events = db.list_events("ISSUER_TEST_A")
    assert s.events_new == 1 and len(events) == 1
    ev = events[0]
    assert ev.event_type == "outlook_change" and ev.fields["agency"] == "SP"
    assert ev.fields["new_outlook"] == "negative" and ev.fields["rating"] == "BBB+"
    assert ev.effective_date == date(2025, 12, 17)
    assert db.get_decision(ev.event_id).final_priority == "P2"
