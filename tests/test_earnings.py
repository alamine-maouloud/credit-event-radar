"""Deterministic earnings release classification (structured-earnings-1.2): explicit statements only."""

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


def only(text: str, title: str | None = TITLE, published: date | None = date(2026, 4, 30)):
    result = extract_earnings_events(doc(text, title, published), "ISSUER_TEST_A")
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
        (
            "The company obtained a covenant waiver from its lenders after breaching the leverage covenant.",
            "covenant",
        ),
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


def test_forward_looking_first_half_title_is_not_a_results_release():
    title = "Neptun Deep advances towards first gas, expected in the first half of 2027"
    assert extract_earnings_events(doc("Text.", title), "ISSUER_TEST_A").events == []
    title2 = "Issuer Test A improves profitability and increases incoming orders by 30% in the first half of 2026"
    assert extract_earnings_events(doc("Text.", title2), "ISSUER_TEST_A").events


@pytest.mark.parametrize(
    "sentence",
    [
        # OMV Q4 2024 report, basis of preparation: the mention reassures, it does not warn.
        "From today's perspective, we assume that the Company's ability to continue as a going concern is not impacted.",
        "The financial statements have been prepared on a going concern basis.",
        "Management concluded that no material uncertainty exists in relation to going concern.",
    ],
)
def test_going_concern_flag_needs_doubt_wording(sentence):
    assert only(f"Results. {sentence}").fields["flags"] == []


@pytest.mark.parametrize(
    "sentence",
    [
        "These conditions indicate that a material uncertainty exists that may cast significant doubt on the Group's ability to continue as a going concern.",
        "Management has concluded that the company may be unable to continue as a going concern.",
    ],
)
def test_going_concern_flag_on_doubt_wording(sentence):
    assert only(f"Results. {sentence}").fields["flags"] == ["going_concern"]


@pytest.mark.parametrize(
    "sentence",
    [
        "We have no liquidity concerns for the coming twelve months.",
        "Liquidity risk management is described in the risk report.",
        "The liquidity risk factors are unchanged from the annual report.",
        "The Group is not exposed to liquidity constraints.",
    ],
)
def test_liquidity_flag_ignores_negations_and_generic_risk_sections(sentence):
    assert only(f"Results. {sentence}").fields["flags"] == []


@pytest.mark.parametrize(
    "sentence",
    [
        "Management notes liquidity constraints in the second half.",
        "Liquidity has become constrained following the recall provisions.",
        "The company faces liquidity pressure as covenant headroom narrows.",
    ],
)
def test_liquidity_flag_on_explicit_deterioration(sentence):
    assert only(f"Results. {sentence}").fields["flags"] == ["liquidity"]


@pytest.mark.parametrize(
    "sentence",
    [
        "As of June 30, 2026, we were in compliance with all material terms and covenants under our loan agreements.",
        "We obtained an amendment to the Credit Agreement to provide additional covenant headroom.",
        "The credit agreement contains customary covenants and events of default.",
        "There was no breach of any financial covenant during the period.",
    ],
)
def test_covenant_flag_needs_a_breach_not_a_mention_or_a_preventive_amendment(sentence):
    assert only(f"Results. {sentence}").fields["flags"] == []


@pytest.mark.parametrize(
    "sentence",
    [
        "The company obtained a covenant waiver from its lenders after breaching the leverage covenant.",
        "As of March 31, 2026, the Company was not in compliance with the minimum net worth covenant.",
        "The lender waived this covenant violation on May 6, 2026.",
    ],
)
def test_covenant_flag_on_a_stated_breach(sentence):
    assert only(f"Results. {sentence}").fields["flags"] == ["covenant"]


def test_flags_ignore_sentences_dated_more_than_a_year_before_the_release():
    """A 2026 release recalling a 2025 covenant breach does not raise the flag again."""
    old = "In the second quarter of 2025, the company was not in compliance with its leverage covenant and obtained a waiver."
    recent = "As of June 30, 2026, the company was not in compliance with its leverage covenant."
    assert only(f"Results. {old}", published=date(2026, 8, 5)).fields["flags"] == []
    assert only(f"Results. {recent}", published=date(2026, 8, 5)).fields["flags"] == ["covenant"]


@pytest.mark.parametrize(
    "sentence,reason",
    [
        # the liquidity lesson applied to the deterministic flag (structured-earnings-1.2):
        # a hypothetical risk factor is not a doubt stated
        (
            "If we are unable to raise additional capital, there could be substantial doubt about our ability to continue as a going concern.",
            "hypothetical",
        ),
        (
            "Management is required to evaluate whether there are conditions and events that raise substantial doubt about the Company's ability to continue as a going concern.",
            "hypothetical",
        ),
        # plans that alleviate the doubt: the ASC 205-40 conclusion, no current flag
        (
            "Management believes its plans alleviate the substantial doubt about the Company's ability to continue as a going concern.",
            "alleviated",
        ),
    ],
)
def test_going_concern_flag_skips_hypotheticals_and_alleviated_doubts(sentence, reason):
    extraction = extract_earnings_events(doc(f"Results. {sentence}", TITLE), "ISSUER_TEST_A")
    assert extraction.events[0].fields["flags"] == []
    assert any(s.reason == f"{reason}_flag:going_concern" for s in extraction.skipped)


@pytest.mark.parametrize(
    "sentence",
    [
        "Management's plans may not alleviate the substantial doubt about the Company's ability to continue as a going concern.",
        "These plans do not alleviate the substantial doubt about the Company's ability to continue as a going concern.",
        "Management has concluded that there is substantial doubt about our ability to continue as a going concern during the next year.",
    ],
)
def test_going_concern_flag_stays_when_the_doubt_is_not_alleviated(sentence):
    assert only(f"Results. {sentence}").fields["flags"] == ["going_concern"]
