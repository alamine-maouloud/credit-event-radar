"""Deterministic earnings release classification (structured-earnings-1.0): explicit statements only."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from radar.extract.earnings import EARNINGS_EXTRACTOR_VERSION, extract_earnings_events
from radar.extract.spans import verify_span
from radar.models import RawDocument
from radar.snapshot import sha256_hex


def doc(text: str, title: str | None, published: date | None = date(2026, 4, 30)) -> RawDocument:
    return RawDocument(
        doc_id=sha256_hex(text), source_type="ir_feed", url="https://example.invalid/pr", title=title,
        published_at=datetime(published.year, published.month, published.day, tzinfo=UTC) if published else None,
        retrieved_at=datetime(2026, 10, 6, tzinfo=UTC), content_hash=sha256_hex(text.encode()), raw_size_bytes=len(text),
        normalizer_version="t", text=text, raw_path="p", extra={"document_type": "press_release"},
    )  # fmt: skip


TITLE = "First Quarter: Issuer Test A makes progress in a challenging environment"


def only(text: str, title: str | None = TITLE):
    result = extract_earnings_events(doc(text, title), "ISSUER_TEST_A")
    assert len(result.events) == 1, (result.events, result.skipped)
    return result.events[0]


def test_results_release_with_reaffirmed_guidance():
    text = "Outlook for 2026\nIssuer Test A confirms its full-year outlook for 2026. Sales revenue is expected to develop within a range of 0 and +3 percent."
    ev = only(text)
    assert ev.family == "earnings" and ev.event_type == "earnings_release"
    assert ev.fields["guidance_status"] == "reaffirmed" and ev.fields["period"] == "First Quarter"
    assert ev.effective_date == date(2026, 4, 30)
    assert any(
        s.field == "guidance_status" and "confirms its full-year outlook" in s.quote
        for s in ev.evidence
    )
    assert all(verify_span(doc(text, TITLE), s) for s in ev.evidence)
    assert ev.fields["extractor_version"] == EARNINGS_EXTRACTOR_VERSION


def test_results_release_without_statement_has_no_status():
    ev = only("Issuer Test A delivered solid results. Sales revenue rose by 2 percent.")
    assert ev.fields["guidance_status"] is None and ev.fields["flags"] == []


@pytest.mark.parametrize(
    "sentence,status",
    [
        ("Results were in line with the guidance given in March.", "in_line"),
        ("Issuer Test A raises its guidance for the full year.", "raised"),
        ("Issuer Test A lowers its outlook for fiscal year 2026.", "cut"),
        ("The company withdraws its guidance for 2026.", "withdrawn"),
        ("Issuer Test A reiterates its targets for 2026.", "reaffirmed"),
    ],
)
def test_guidance_statements(sentence, status):
    assert only(f"Highlights. {sentence}").fields["guidance_status"] == status


def test_conflicting_statements_give_no_status():
    text = "Issuer Test A raises its revenue guidance. Issuer Test A lowers its margin outlook."
    result = extract_earnings_events(doc(text, TITLE), "ISSUER_TEST_A")
    assert result.events[0].fields["guidance_status"] is None
    assert [s.reason for s in result.skipped] == ["conflicting_guidance_statements"]


@pytest.mark.parametrize(
    "sentence,flag",
    [
        (
            "There is substantial doubt about the company's ability to continue as a going concern.",
            "going_concern",
        ),
        ("The company obtained a covenant waiver from its lenders.", "covenant"),
        ("A material impairment of EUR 2 billion was recognised.", "impairment"),
        ("Management notes liquidity constraints in the second half.", "liquidity"),
    ],
)
def test_flags(sentence, flag):
    ev = only(f"Results. {sentence}")
    assert ev.fields["flags"] == [flag]
    assert any(s.field == f"flag:{flag}" for s in ev.evidence)


def test_non_results_release_gives_nothing():
    result = extract_earnings_events(
        doc("Issuer Test A confirms its outlook.", "Issuer Test A opens a new plant"),
        "ISSUER_TEST_A",
    )
    assert result.events == []


@pytest.mark.parametrize(
    "title",
    [
        "Half-year results 2026",
        "Q3 2026 trading update",
        "Issuer Test A with solid FY 2025 results",
        "Nine months 2026: revenue up",
    ],
)
def test_results_title_patterns(title):
    assert extract_earnings_events(doc("Text.", title), "ISSUER_TEST_A").events


def test_title_from_first_line_when_missing():
    ev = only("Second quarter 2026 results\nIssuer Test A confirms its guidance.", title=None)
    assert ev.fields["period"] == "Second quarter"
