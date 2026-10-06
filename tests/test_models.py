"""Schema validation tests (SPEC 7)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from radar.models import (
    PRIORITY_ORDER,
    AgencyRating,
    Claim,
    CreditEvent,
    EvidenceSpan,
    PriorityDecision,
    RawDocument,
)


def span(**overrides):
    base = dict(
        doc_id="doc",
        char_start=10,
        char_end=20,
        quote="some text",
        match_score=95.0,
        extractor_version="test-0",
    )
    base.update(overrides)
    return EvidenceSpan(**base)


def test_evidence_span_valid():
    s = span()
    assert s.char_end > s.char_start


@pytest.mark.parametrize("start,end", [(10, 10), (10, 5), (-1, 5)])
def test_evidence_span_rejects_bad_offsets(start, end):
    with pytest.raises(ValidationError):
        span(char_start=start, char_end=end)


@pytest.mark.parametrize("score", [-0.1, 100.1])
def test_evidence_span_rejects_out_of_range_score(score):
    with pytest.raises(ValidationError):
        span(match_score=score)


def test_evidence_span_rejects_empty_quote():
    with pytest.raises(ValidationError):
        span(quote="")


HEX64 = "0" * 64


def raw_doc(**overrides):
    base = dict(
        doc_id=HEX64,
        source_type="edgar",
        url="https://example.invalid/doc",
        retrieved_at=datetime(2026, 1, 1),
        content_hash=HEX64,
        raw_size_bytes=1,
        normalizer_version="test-0",
        text="t",
        raw_path="p",
    )
    base.update(overrides)
    return RawDocument(**base)


def test_raw_document_requires_valid_url():
    with pytest.raises(ValidationError):
        raw_doc(url="not a url")


def test_raw_document_rejects_unknown_source_type():
    with pytest.raises(ValidationError):
        raw_doc(source_type="bloomberg")


def test_raw_document_hashes_are_sha256_hex():
    with pytest.raises(ValidationError):
        raw_doc(content_hash="abc")
    with pytest.raises(ValidationError):
        raw_doc(doc_id="abc")


def test_raw_document_extra_is_json_only():
    doc = raw_doc(extra={"accession_number": "0000793952-26-000061", "items": ["2.02"]})
    assert doc.extra["items"] == ["2.02"]
    with pytest.raises(ValidationError):
        raw_doc(extra={"when": datetime(2026, 1, 1)})


def test_evidence_span_types():
    assert span().evidence_type == "sentence"
    assert span(evidence_type="footnote").evidence_type == "footnote"
    with pytest.raises(ValidationError):
        span(evidence_type="paragraph")


def test_credit_event_defaults():
    ev = CreditEvent(
        event_id="e1",
        issuer_id="ISSUER_TEST_A",
        family="rating",
        event_type="downgrade",
        extraction_method="structured",
    )
    assert ev.fields == {}
    assert ev.evidence == []
    assert ev.effective_date is None


def test_priority_decision_literal():
    with pytest.raises(ValidationError):
        PriorityDecision(event_id="e", priority="P0", rules_version="1.0", llm_role="none")


def test_priority_order():
    assert PRIORITY_ORDER["P1"] > PRIORITY_ORDER["P2"] > PRIORITY_ORDER["P3"]


def test_claim_status_literal():
    with pytest.raises(ValidationError):
        Claim(claim_id="c", text="t", lang="fr", status="CONFIDENT")


def test_agency_rating_rejects_unknown_agency():
    with pytest.raises(ValidationError):
        AgencyRating(
            issuer_id="ISSUER_TEST_A",
            agency="EGAN_JONES",
            rating_type="long_term_issuer",
            rating="A",
            source_url="https://example.invalid/r",
            retrieved_at=date(2026, 1, 1),
            verification_status="GOLDEN",
        )


def test_agency_rating_rejects_unknown_rating_type_and_level():
    base = dict(
        issuer_id="ISSUER_TEST_A",
        agency="SP",
        rating="A",
        source_url="https://example.invalid/r",
        retrieved_at=date(2026, 1, 1),
    )
    with pytest.raises(ValidationError):
        AgencyRating(**base, rating_type="senior_unsecured_bond", verification_status="GOLDEN")
    with pytest.raises(ValidationError):
        AgencyRating(**base, rating_type="long_term_issuer", verification_status="VERIFIED")
