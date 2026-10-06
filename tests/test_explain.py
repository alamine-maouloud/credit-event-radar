"""The explain renderer: everything an analyst needs to contest or accept a priority."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from radar.config import CONFIG_DIR, load_rules
from radar.materiality.explain import render_explanation
from radar.models import RawDocument
from tests.materiality_helpers import candidate, decide, ev, rating_event, state

HEX = "7" * 64


@pytest.fixture(scope="module")
def rules():
    return load_rules(CONFIG_DIR / "rules.yaml")


def document() -> RawDocument:
    return RawDocument(
        doc_id=HEX, source_type="edgar", url="https://www.sec.gov/Archives/edgar/data/1/x.htm", title="10-Q 0000000001-26-000003",
        published_at=datetime(2026, 8, 5, tzinfo=UTC), retrieved_at=datetime(2026, 10, 6, tzinfo=UTC), content_hash=HEX,
        raw_size_bytes=10, normalizer_version="sec-html-1.0", text="x" * 10, raw_path="p", extra={"form": "10-Q"},
    )  # fmt: skip


def test_explanation_for_split_rating_p1(rules, scales):
    st = state(
        rules,
        scales,
        candidate("MOODYS", "Baa3"),
        candidate("FITCH", "BBB"),
        candidate("DBRS", "BBB (low)"),
    )
    event = rating_event("SP", "BBB-", "BB+", new_outlook="stable")
    text = render_explanation(decide(rules, scales, event, st), event, {"doc_test": document()})
    assert "FINAL PRIORITY: P1" in text
    assert "RAT-01  TRUE" in text and "RAT-02  TRUE" in text
    assert "BBB- -> BB+" in text and "IG -> HY" in text
    assert (
        "Moody's" in text
        and "Baa3" in text
        and "as_of: 2026-06-30" in text
        and "age: 8 days" in text
    )
    assert "Fitch" in text and "BBB" in text
    assert "DBRS" in text and "IGNORED" in text and "non-admissible" in text
    assert "Not triggered" in text and "RAT-03  FALSE" in text and "RAT-10  FALSE" in text
    assert "MOD-01" in text and "MOD-02" in text and "MOD-03" in text
    assert "Agency ratings used: YES" in text
    assert "Composite rating used: NO" in text
    assert "LLM used: NO" in text
    assert "Rules version: 1.5" in text
    assert (
        "https://www.sec.gov/Archives/edgar/data/1/x.htm" in text
        and HEX in text
        and "2026-08-05" in text
    )


def test_explanation_shows_as_of_basis(rules, scales):
    from datetime import timedelta

    from tests.materiality_helpers import D

    st = state(
        rules,
        scales,
        candidate("MOODYS", "Baa3"),
        candidate(
            "FITCH",
            "BBB",
            as_of=D - timedelta(days=2),
            as_of_basis="retrieval",
            verification="structured_table_current",
        ),
    )
    event = rating_event("SP", "BBB-", "BB+")
    text = render_explanation(decide(rules, scales, event, st), event, {})
    assert "as_of: 2026-06-30 (stated by the source)" in text
    assert (
        "as_of: 2026-07-06 (retrieval: observed on the source that day, prospective use only)"
        in text
    )


def test_explanation_without_priority(rules, scales):
    event = ev("other", "management_change", edgar_item="5.02")
    text = render_explanation(decide(rules, scales, event, None), event, {})
    assert "FINAL PRIORITY: NONE" in text and "NO_APPLICABLE_RULE" in text
    assert "Triggered\n---------\n(none)" in text


def test_explanation_mentions_modifier_effect(rules, scales):
    st = state(rules, scales, candidate("SP", "BBB"))
    event = rating_event("SP", "BBB", "BBB-")
    text = render_explanation(decide(rules, scales, event, st), event, {})
    assert "Base priority: P2" in text and "FINAL PRIORITY: P1" in text
    assert "MOD-01  APPLIED" in text and "+1" in text


def test_explanation_reports_llm_role_when_llm_extracted(rules, scales):
    event = rating_event("SP", "BBB-", "BB+", method="llm_validated")
    text = render_explanation(decide(rules, scales, event, None), event, {})
    assert "LLM used: field extraction (validated against source text)" in text


def _enriched_event():
    event = ev(
        "earnings",
        "earnings_release",
        guidance_status="cut",
        guidance_metric="fcf",
        guidance_old=4.5,
        guidance_new=2.5,
        guidance_change_pct=-44.4,
    )
    source = {
        "method": "llm_validated",
        "enrichment_version": "guidance-enrichment-1.0",
        "statement_ids": ["s" * 64],
        "llm_call_ids": [7],
        "model_id": "gpt-5.6-terra",
        "resolved_model": "gpt-5.6-terra",
        "prompt_version": "1.1.0",
        "schema_version": "guidance-1.0",
    }
    return event.model_copy(
        update={
            "enrichment_method": "llm_validated",
            "fields": {
                **event.fields,
                "guidance_source": source,
                "llm_guidance": [{"statement_id": "s" * 64, "metric": "fcf", "status": "cut"}],
            },
        }
    )


def test_explanation_separates_detection_enrichment_and_decision(rules, scales):
    event = _enriched_event()
    decision = decide(rules, scales, event, None)
    text = render_explanation(decision, event, {})
    assert "Detected by: deterministic extractor (structured)" in text
    assert (
        "Enriched by: validated LLM statements (gpt-5.6-terra, prompt 1.1.0, "
        "schema guidance-1.0, 1 statement applied, 1 recorded)"
    ) in text
    assert "Priority decided by: deterministic rules engine (rules.yaml 1.5)" in text
    assert decision.provenance.llm_used != "none"
    assert "ERN-02  TRUE" in text


def test_explanation_names_no_enrichment_on_a_plain_event(rules, scales):
    event = ev("earnings", "earnings_release", guidance_status="reaffirmed")
    text = render_explanation(decide(rules, scales, event, None), event, {})
    assert "Enriched by: none (deterministic fields only)" in text
    assert "Detected by: deterministic extractor (structured)" in text
