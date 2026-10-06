"""Span validator: text match plus semantic field validation. A perfect quote never suffices."""

from __future__ import annotations

from datetime import date

import pytest

from radar.llm.validate import validate_statement
from tests.llm_helpers import FULL_QUOTE, ISSUER_NAMES, SHORT_QUOTE, VW_LIKE, doc, statement

D = date(2026, 4, 30)


def validate(st, text=VW_LIKE, published=D):
    return validate_statement(st, doc(text, published), ISSUER_NAMES, document_date=published)


def test_valid_statement_passes_every_check():
    result = validate(statement())
    assert result.status == "VALID", result.reasons
    assert all(result.checks.values())
    assert result.match_kind == "exact" and result.match_score == 100.0
    assert (result.matched_start, result.matched_end) == (
        statement().start_offset,
        statement().end_offset,
    )


def test_wrong_offsets_but_unique_quote_is_relocated():
    st = statement(start_offset=5, end_offset=20)
    result = validate(st)
    assert result.status == "VALID" and result.match_kind == "exact"
    assert result.matched_start == VW_LIKE.index(st.evidence_quote)


def test_fuzzy_match_at_or_above_90_is_accepted_but_recorded():
    typo = "The Group's operating return on sales is expected to range betwen 4.0 and 5.5 percent."
    result = validate(
        statement(
            quote=typo,
            previous_lower=None,
            previous_upper=None,
            status="new",
            direction_claimed=None,
        )
    )
    assert result.status == "VALID" and result.match_kind == "fuzzy" and result.match_score >= 90


def test_fuzzy_below_90_is_rejected():
    result = validate(
        statement(
            quote="Operating return on sales will be 4.0 to 5.5 percent next year, says management.",
            previous_lower=None,
            previous_upper=None,
            status="new",
            direction_claimed=None,
        )
    )
    assert result.status == "INVALID" and result.checks["span_match"] is False


def test_metric_mismatch_with_exact_quote_is_rejected():
    result = validate(statement(metric="revenue", metric_label="sales revenue"))
    assert result.status == "INVALID" and result.checks["metric_match"] is False
    assert any("metric" in r for r in result.reasons)


def test_unit_mismatch_is_rejected():
    quote = "Net cash flow for the year 2026 is expected to range between EUR 3 billion and EUR 6 billion."
    good = statement(
        quote=quote,
        metric="fcf",
        metric_label="net cash flow",
        basis="absolute",
        unit="EUR_BN",
        previous_lower=None,
        previous_upper=None,
        current_lower=3.0,
        current_upper=6.0,
        status="new",
        direction_claimed=None,
    )
    assert validate(good).status == "VALID"
    bad = good.model_copy(update={"unit": "EUR_MN"})
    result = validate(bad)
    assert result.status == "INVALID" and result.checks["unit_match"] is False


def test_number_absent_from_quote_is_rejected():
    result = validate(statement(current_upper=5.0))
    assert result.status == "INVALID" and result.checks["numbers_match"] is False


def test_previous_bounds_may_come_from_elsewhere_in_the_document_but_must_exist():
    """Previous guidance (5.5 to 6.5) is in the next sentence, not in the quote: allowed only
    if the quote is extended or the numbers exist in the document near the metric."""
    assert validate(statement(quote=FULL_QUOTE)).status == "VALID"
    short = statement(quote=SHORT_QUOTE)  # quote holds only the current range
    assert validate(short).status == "INVALID"
    assert validate(short).checks["numbers_match"] is False


def test_numbers_must_attach_to_the_metric_sentence():
    quote = (
        "The Issuer Test A Group expects sales revenue in 2026 to develop within a range of 0 and +3 percent "
        "compared with the previous year. The Group's operating return on sales is expected to range between "
        "4.0 and 5.5 percent."
    )
    wrong = statement(
        quote=quote,
        metric="revenue",
        metric_label="sales revenue",
        basis="yoy_change_pct",
        previous_lower=None,
        previous_upper=None,
        current_lower=4.0,
        current_upper=5.5,
        status="new",
        direction_claimed=None,
    )
    result = validate(wrong)
    assert result.status == "INVALID" and result.checks["numbers_attached"] is False
    right = wrong.model_copy(update={"current_lower": 0.0, "current_upper": 3.0})
    assert validate(right).status == "VALID"


def test_entity_mismatch_is_rejected():
    quote = "ITA Financial Services AG expects its operating result to reach EUR 2.5 billion."
    st = statement(
        quote=quote,
        metric="ebit",
        metric_label="operating result",
        basis="absolute",
        unit="EUR_BN",
        previous_lower=None,
        previous_upper=None,
        current_lower=2.5,
        current_upper=2.5,
        status="new",
        direction_claimed=None,
    )
    result = validate(st)
    assert result.status == "INVALID" and result.checks["entity_match"] is False


def test_future_dated_quote_is_rejected():
    text = (
        VW_LIKE
        + "\nOn June 30, 2027 the Group's operating return on sales is expected to range between 4.0 and 5.5 percent."
    )
    quote = "On June 30, 2027 the Group's operating return on sales is expected to range between 4.0 and 5.5 percent."
    st = statement(
        text=text,
        quote=quote,
        previous_lower=None,
        previous_upper=None,
        status="new",
        direction_claimed=None,
    )
    result = validate(st, text=text)
    assert result.status == "INVALID" and result.checks["temporal_consistency"] is False


def test_period_far_in_the_past_is_rejected():
    st = statement(
        period="2019",
        previous_lower=None,
        previous_upper=None,
        status="new",
        direction_claimed=None,
    )
    result = validate(st)
    assert result.status == "INVALID" and result.checks["temporal_consistency"] is False


def test_percent_unit_requires_percent_in_quote():
    quote = "Net cash flow for the year 2026 is expected to range between EUR 3 billion and EUR 6 billion."
    st = statement(
        quote=quote,
        metric="fcf",
        metric_label="net cash flow",
        basis="absolute",
        unit="PCT",
        previous_lower=None,
        previous_upper=None,
        current_lower=3.0,
        current_upper=6.0,
        status="new",
        direction_claimed=None,
    )
    assert validate(st).checks["unit_match"] is False


def test_validation_result_is_serialisable_for_audit():
    result = validate(statement())
    payload = result.model_dump(mode="json")
    assert set(payload["checks"]) >= {
        "span_match",
        "numbers_match",
        "unit_match",
        "metric_match",
        "numbers_attached",
        "entity_match",
        "temporal_consistency",
    }


@pytest.mark.parametrize(
    "quote,lower,upper",
    [
        # TRATON writes the sign with a figure dash, sometimes spaced; VW with an en dash
        (
            "We continue to expect a range of "
            + chr(0x2012)
            + "5% to +5% for unit sales and sales revenue.",
            -5,
            5,
        ),
        (
            "For 2025, the TRATON GROUP now expects a range of "
            + chr(0x2012)
            + " 10% to + 0% for unit sales and sales revenue.",
            -10,
            0,
        ),
        (
            "The Volkswagen Group expects sales revenue in 2026 to develop within a range of "
            + chr(0x2013)
            + "3 to 0 percent compared with the previous year.",
            -3,
            0,
        ),
    ],
)
def test_negative_bounds_written_with_typographic_dashes_are_found(quote, lower, upper):
    """First real run (2026-10-06): six correct extractions were rejected because the
    validator read "" + chr(0x2012) + "5%" as 5 and reported -5 missing from the quote."""
    text = "Outlook\n" + quote + "\nThe margin is expected between 6 and 7%."
    st = statement(
        text,
        quote,
        metric="revenue",
        metric_label="sales revenue",
        basis="yoy_change_pct",
        unit="PCT",
        previous_lower=None,
        previous_upper=None,
        current_lower=lower,
        current_upper=upper,
        status="new",
    )
    result = validate_statement(st, doc(text), ISSUER_NAMES, document_date=date(2026, 4, 28))
    assert result.checks["numbers_match"] is True and result.checks["numbers_attached"] is True
    assert result.status == "VALID", result.reasons
