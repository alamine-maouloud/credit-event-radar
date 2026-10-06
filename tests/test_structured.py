"""Deterministic rating-action and EDGAR item extraction, including the Harley fixture."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from radar.connectors.fixture import load_fixture
from radar.extract.spans import verify_span
from radar.extract.structured import (
    EDGAR_EXTRACTOR_VERSION,
    EXTRACTOR_VERSION,
    extract_edgar_items,
    extract_rating_actions,
    parse_us_date,
)
from radar.models import RawDocument
from radar.normalize import SecHtmlNormalizer
from radar.snapshot import build_raw_document, sha256_hex

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "edgar"


def doc(text: str, **extra) -> RawDocument:
    return RawDocument(
        doc_id=sha256_hex(text), source_type="edgar" if extra else "manual", url="https://example.invalid/d",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC), content_hash=sha256_hex(text.encode()),
        raw_size_bytes=len(text), normalizer_version="t", text=text, raw_path="p", extra=extra,
    )  # fmt: skip


def only_event(text: str, scales):
    result = extract_rating_actions(doc(text), "ISSUER_TEST_A", scales)
    assert len(result.events) == 1, (result.events, result.skipped)
    return result.events[0]


# ------------------------------------------------------------- rating --- #


def test_downgrade_with_date_outlook_and_watch(scales):
    text = (
        "Intro line.\nOn July 8, 2026, subsequent to June 30, 2026, S&P Global Ratings lowered the "
        "Company's long-term credit rating from BBB- to BB+, and revised the outlook to Stable."
    )
    ev = only_event(text, scales)
    assert ev.family == "rating" and ev.event_type == "downgrade"
    assert ev.effective_date == date(2026, 7, 8)
    f = ev.fields
    assert (f["agency"], f["old_rating"], f["new_rating"]) == ("SP", "BBB-", "BB+")
    assert (f["old_notch"], f["new_notch"], f["notch_delta"]) == (10, 11, 1)
    assert f["crosses_ig_to_hy"] is True and f["crosses_hy_to_ig"] is False
    assert f["old_at_boundary"] is True
    assert f["new_outlook"] == "stable" and f["watch"] is None
    assert f["scope"] == "issuer" and f["action_verb"] == "lowered"
    assert ev.extraction_method == "structured"
    fields = {s.field for s in ev.evidence}
    assert fields == {None, "old_rating", "new_rating", "agency", "effective_date", "new_outlook"}
    d = doc(text)
    assert all(verify_span(d, s) for s in ev.evidence)
    assert all(
        s.extractor_version == EXTRACTOR_VERSION and s.evidence_type == "sentence"
        for s in ev.evidence
    )
    assert next(s for s in ev.evidence if s.field == "new_rating").quote == "BB+"
    assert ev.event_id.startswith("evt_")


def test_event_id_is_deterministic_across_documents(scales):
    text = "On July 8, 2026, S&P Global Ratings lowered its rating from BBB- to BB+."
    a = extract_rating_actions(doc(text), "ISSUER_TEST_A", scales).events[0]
    b = extract_rating_actions(doc("Prefix.\n" + text), "ISSUER_TEST_A", scales).events[0]
    assert a.event_id == b.event_id
    c = extract_rating_actions(doc(text), "ISSUER_TEST_B", scales).events[0]
    assert c.event_id != a.event_id


def test_short_term_transition_is_skipped_but_long_term_kept(scales):
    text = (
        "S&P Global Ratings lowered the Company's short-term credit rating from A-3 to B, "
        "lowered the Company's long-term credit rating from BBB- to BB+."
    )
    result = extract_rating_actions(doc(text), "ISSUER_TEST_A", scales)
    assert [e.fields["new_rating"] for e in result.events] == ["BB+"]
    assert result.skipped == []  # "from A-3 to B" never matches long-term labels


def test_short_term_wording_before_a_long_term_label_is_skipped(scales):
    text = "Moody's lowered the short-term rating from Baa3 to Ba1."
    result = extract_rating_actions(doc(text), "ISSUER_TEST_A", scales)
    assert result.events == []
    assert [s.reason for s in result.skipped] == ["short_term_rating"]


def test_moodys_upgrade(scales):
    ev = only_event(
        "On March 3, 2026, Moody's Ratings upgraded the issuer rating from Ba1 to Baa3 with a stable outlook.",
        scales,
    )
    assert ev.event_type == "upgrade"
    assert (ev.fields["old_rating"], ev.fields["new_rating"]) == ("Ba1", "Baa3")
    assert ev.fields["crosses_hy_to_ig"] is True
    assert ev.fields["new_outlook"] == "stable"


def test_verb_direction_mismatch_is_skipped(scales):
    result = extract_rating_actions(
        doc("Fitch Ratings raised the rating from A to BBB."), "ISSUER_TEST_A", scales
    )
    assert result.events == [] and result.skipped[0].reason == "verb_direction_mismatch"


def test_transition_without_verb_is_skipped(scales):
    result = extract_rating_actions(
        doc("Fitch Ratings moved the rating from A to BBB."), "ISSUER_TEST_A", scales
    )
    assert result.events == [] and result.skipped[0].reason == "no_action_verb"


def test_no_agency_no_event(scales):
    result = extract_rating_actions(
        doc("The bank lowered its rating from BBB- to BB+."), "ISSUER_TEST_A", scales
    )
    assert result.events == [] and result.skipped == []


def test_transition_across_sentences_is_not_extracted(scales):
    text = "S&P Global Ratings acted on the Company. It lowered the rating from BBB- to BB+."
    assert extract_rating_actions(doc(text), "ISSUER_TEST_A", scales).events == []


def test_two_agencies_in_one_sentence(scales):
    text = "S&P Global Ratings lowered the rating from BBB- to BB+ and Moody's lowered its rating from Baa3 to Ba1."
    result = extract_rating_actions(doc(text), "ISSUER_TEST_A", scales)
    assert sorted((e.fields["agency"], e.fields["new_rating"]) for e in result.events) == [
        ("MOODYS", "Ba1"),
        ("SP", "BB+"),
    ]


def test_label_boundaries(scales):
    text = "S&P Global Ratings lowered the rating from BBB- to BB+1 for fun."
    assert extract_rating_actions(doc(text), "ISSUER_TEST_A", scales).events == []


def test_creditwatch_is_captured(scales):
    ev = only_event(
        "S&P Global Ratings lowered the rating from BBB to BBB- and placed it on CreditWatch with negative implications.",
        scales,
    )
    assert ev.fields["watch"] == "negative"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("On July 8, 2026, S&P", date(2026, 7, 8)),
        ("as of February 29, 2025 was", None),
        ("no date", None),
    ],
)
def test_parse_us_date(text, expected):
    found = parse_us_date(text)
    assert (found[0] if found else None) == expected


# ---------------------------------------------------------- fixtures --- #


def fixture_doc(name: str, tmp_path: Path) -> RawDocument:
    fx = load_fixture(FIXTURES / name)
    return build_raw_document(
        fx.fetched,
        source_type="edgar",
        raw_dir=tmp_path,
        normalizer=SecHtmlNormalizer(),
        extra=fx.manifest["extra"],
    )


def test_synthetic_fixture_extraction(scales, tmp_path):
    d = fixture_doc("synthetic_issuer_test_a_10q", tmp_path)
    result = extract_rating_actions(d, "ISSUER_TEST_A", scales)
    assert len(result.events) == 1
    ev = result.events[0]
    assert (ev.fields["old_rating"], ev.fields["new_rating"], ev.effective_date) == (
        "BBB-",
        "BB+",
        date(2026, 7, 8),
    )


def test_harley_davidson_10q_extraction(scales, tmp_path):
    """Golden integration fixture: the real 10-Q, no LLM, every span verifiable."""
    d = fixture_doc("harley_davidson_inc_10q_2026q2", tmp_path)
    result = extract_rating_actions(d, "HARLEY_DAVIDSON_INC", scales)
    assert len(result.events) == 1, result.skipped
    ev = result.events[0]
    assert ev.issuer_id == "HARLEY_DAVIDSON_INC"
    assert ev.event_type == "downgrade"
    assert ev.effective_date == date(2026, 7, 8)
    f = ev.fields
    assert (f["agency"], f["old_rating"], f["new_rating"]) == ("SP", "BBB-", "BB+")
    assert f["old_category"] == "IG" and f["new_category"] == "HY"
    assert f["crosses_ig_to_hy"] is True
    assert f["new_outlook"] == "stable"
    assert f["notch_delta"] == 1
    assert all(verify_span(d, s) for s in ev.evidence)
    sentence = next(s for s in ev.evidence if s.field is None)
    assert sentence.quote.startswith(
        "(a)On July 8, 2026, subsequent to June 30, 2026, S&P Global Ratings lowered"
    )
    assert (
        sentence.char_start == 228452
        or d.text[sentence.char_start : sentence.char_end] == sentence.quote
    )
    assert ev.source_doc_ids == [d.doc_id]
    assert result.skipped == []


# ------------------------------------------------------------- edgar --- #


def test_edgar_items_mapping():
    d = doc(
        "Item 2.02 Results of Operations.\nItem 9.01 Exhibits.",
        form="8-K",
        items=["2.02", "9.01"],
        report_date="2026-07-23",
        filing_date="2026-07-23",
        accession_number="0000000001-26-000002",
    )
    result = extract_edgar_items(d, "ISSUER_TEST_A")
    assert [(e.family, e.event_type) for e in result.events] == [("earnings", "earnings_release")]
    ev = result.events[0]
    assert ev.effective_date == date(2026, 7, 23)
    assert (
        ev.evidence[0].quote == "Item 2.02"
        and ev.evidence[0].extractor_version == EDGAR_EXTRACTOR_VERSION
    )
    assert ev.fields["edgar_item"] == "2.02"


def test_edgar_item_2_04_and_prospectus():
    d = doc("text", form="8-K", items=["2.04"], filing_date="2026-07-23")
    assert extract_edgar_items(d, "ISSUER_TEST_A").events[0].event_type == "obligation_acceleration"
    p = doc("text", form="424B5", items=[], filing_date="2026-07-23")
    ev = extract_edgar_items(p, "ISSUER_TEST_A").events[0]
    assert (ev.family, ev.event_type, ev.evidence) == ("issuance", "prospectus_supplement", [])


def test_edgar_items_ignore_other_sources_and_forms():
    assert extract_edgar_items(doc("text"), "ISSUER_TEST_A").events == []
    assert (
        extract_edgar_items(
            doc("text", form="10-Q", items=[], filing_date="2026-08-05"), "ISSUER_TEST_A"
        ).events
        == []
    )
