"""Deterministic issuance extraction (structured-issuance-1.0)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from radar.extract.issuance import ISSUANCE_EXTRACTOR_VERSION, extract_issuance_events
from radar.extract.spans import verify_span
from radar.models import RawDocument
from radar.snapshot import sha256_hex


def doc(text: str, title: str | None = None, published: date | None = None) -> RawDocument:
    return RawDocument(
        doc_id=sha256_hex(text), source_type="ir_feed", url="https://example.invalid/pr", title=title,
        published_at=datetime(published.year, published.month, published.day, tzinfo=UTC) if published else None,
        retrieved_at=datetime(2026, 10, 6, tzinfo=UTC), content_hash=sha256_hex(text.encode()), raw_size_bytes=len(text),
        normalizer_version="t", text=text, raw_path="p", extra={"document_type": "press_release"},
    )  # fmt: skip


def only(text: str, **kw):
    result = extract_issuance_events(doc(text, **kw), "ISSUER_TEST_A")
    assert len(result.events) == 1, (result.events, result.skipped)
    return result.events[0]


def test_eur_bond_with_coupon_and_maturity():
    text = "Issuer Test A successfully placed a EUR 1.5 billion senior unsecured bond with a coupon of 3.375% due June 16, 2031."
    ev = only(text, published=date(2026, 6, 15))
    assert ev.family == "issuance" and ev.event_type == "new_issue"
    f = ev.fields
    assert (f["amount"], f["currency"], f["amount_eur_equiv"]) == (
        1_500_000_000.0,
        "EUR",
        1_500_000_000.0,
    )
    assert f["coupon"] == 3.375 and f["maturity"] == "2031-06-16" and f["seniority"] == "senior"
    assert ev.effective_date == date(2026, 6, 15)
    assert f["extractor_version"] == ISSUANCE_EXTRACTOR_VERSION
    assert {s.field for s in ev.evidence} >= {None, "amount", "coupon", "maturity", "seniority"}
    assert all(verify_span(doc(text), s) for s in ev.evidence)


def test_euro_sign_and_million():
    ev = only(
        "Issuer Test A issues its first green bond of €500 million with a maturity of five years."
    )
    assert ev.fields["amount"] == 500_000_000.0 and ev.fields["currency"] == "EUR"
    assert (
        ev.fields["green"] is True
        and ev.fields["maturity"] is None
        and ev.fields["tenor_years"] == 5
    )


def test_usd_amount_has_no_eur_equivalent():
    ev = only("Issuer Test A priced USD 750 million of senior notes due 2031.")
    assert (
        ev.fields["currency"] == "USD"
        and ev.fields["amount_eur_equiv"] is None
        and ev.fields["maturity"] == "2031"
    )


@pytest.mark.parametrize(
    "text,seniority",
    [
        ("Issuer Test A placed EUR 750 million perpetual subordinated hybrid notes.", "hybrid"),
        ("Issuer Test A issued EUR 1 billion Additional Tier 1 notes.", "AT1"),
        ("Issuer Test A issued EUR 500 million Tier 2 subordinated notes.", "T2"),
        ("Issuer Test A issued EUR 500 million subordinated notes.", "subordinated"),
        ("Issuer Test A issued EUR 500 million senior preferred notes.", "senior"),
    ],
)
def test_seniority_detection(text, seniority):
    assert only(text).fields["seniority"] == seniority


def test_total_with_loan_is_ambiguous_then_body_wins():
    text = (
        "Issuer Test A issues its first Green Bond and Green Loan for a total of €850 million.\n"
        "The green bond has a volume of €500 million and a term of six years. The green loan of €350 million was provided by a bank."
    )
    result = extract_issuance_events(doc(text), "ISSUER_TEST_A")
    assert [e.fields["amount"] for e in result.events] == [500_000_000.0]
    assert [s.reason for s in result.skipped] == ["ambiguous_total_with_loan"]


def test_redemption_notice_is_a_redemption_event():
    text = "Issuer Test A announces the early redemption of its EUR 750,000,000 perpetual subordinated notes with a first call date 2026."
    ev = only(text)
    assert ev.event_type == "redemption" and ev.fields["seniority"] == "subordinated"
    assert ev.fields["amount"] == 750_000_000.0


def test_non_call_decision():
    ev = only(
        "Issuer Test A has decided not to call its EUR 500 million hybrid notes at the first call date."
    )
    assert ev.event_type == "non_call" and ev.fields["seniority"] == "hybrid"


def test_consideration_is_not_an_issuance():
    text = "Issuer Test A considers the issue of a new hybrid bond subject to market conditions."
    result = extract_issuance_events(doc(text), "ISSUER_TEST_A")
    assert result.events == [] and [s.reason for s in result.skipped] == ["intent_only"]


def test_no_amount_no_event():
    result = extract_issuance_events(doc("Issuer Test A issued new bonds today."), "ISSUER_TEST_A")
    assert result.events == [] and [s.reason for s in result.skipped] == ["no_amount"]


def test_one_event_per_document_keeps_the_most_specific_sentence():
    text = (
        "Issuer Test A placed a EUR 1 billion bond.\n"
        "The EUR 1 billion senior unsecured bond carries a 3.5% coupon and is due 2033."
    )
    result = extract_issuance_events(doc(text), "ISSUER_TEST_A")
    assert len(result.events) == 1 and result.events[0].fields["coupon"] == 3.5


def test_event_id_deterministic_across_documents():
    a = only("Issuer Test A placed a EUR 1 billion bond due 2033.", published=date(2026, 5, 6))
    b = only(
        "Prefix. Issuer Test A placed a EUR 1 billion bond due 2033.", published=date(2026, 5, 6)
    )
    assert a.event_id == b.event_id
