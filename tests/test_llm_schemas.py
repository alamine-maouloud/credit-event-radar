"""Strict schemas and Python-side arithmetic: the LLM reads bounds, the code computes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from radar.llm.compute import compute_guidance_change
from radar.llm.schemas import (
    GUIDANCE_SCHEMA_VERSION,
    GuidanceExtraction,
    guidance_json_schema,
)
from tests.llm_helpers import statement


def test_schema_version_and_strictness():
    assert GUIDANCE_SCHEMA_VERSION == "guidance-1.0"
    with pytest.raises(ValidationError):
        statement(confidence=0.9)  # no self-declared confidence, ever
    with pytest.raises(ValidationError):
        statement(metric="profits")
    with pytest.raises(ValidationError):
        statement(unit="EUR")
    with pytest.raises(ValidationError):
        GuidanceExtraction(has_guidance=True, statements=[], extra_field=1)


def test_json_schema_is_closed_everywhere():
    schema = guidance_json_schema()
    assert schema["additionalProperties"] is False
    nested = schema["$defs"]["GuidanceStatement"]
    assert nested["additionalProperties"] is False
    assert "evidence_quote" in nested["required"] and "start_offset" in nested["required"]


def test_offsets_must_be_ordered():
    with pytest.raises(ValidationError):
        statement(start_offset=50, end_offset=10)


# ------------------------------------------------------------ arithmetic --- #


def test_compute_range_midpoints_and_direction_from_bounds_not_claim():
    st = statement(direction_claimed="up")  # the claim is wrong on purpose
    change = compute_guidance_change(st)
    assert (change.midpoint_previous, change.midpoint_current) == (6.0, 4.75)
    assert change.delta_absolute == pytest.approx(-1.25)
    assert change.delta_percent == pytest.approx(-20.8333, rel=1e-4)
    assert change.direction == "down"
    assert change.direction_claim_matches is False


def test_compute_margin_points_and_absolute_amounts():
    amount = statement(
        metric="fcf",
        basis="absolute",
        unit="EUR_BN",
        previous_lower=5.0,
        previous_upper=7.0,
        current_lower=3.0,
        current_upper=6.0,
    )
    change = compute_guidance_change(amount)
    assert change.midpoint_previous == 6.0 and change.midpoint_current == 4.5
    assert change.delta_percent == pytest.approx(-25.0)
    growth = statement(
        metric="revenue",
        basis="yoy_change_pct",
        unit="PCT",
        previous_lower=2.0,
        previous_upper=4.0,
        current_lower=0.0,
        current_upper=3.0,
    )
    change = compute_guidance_change(growth)
    assert change.delta_absolute == pytest.approx(-1.5)
    assert (
        change.delta_percent is None
    )  # a change in growth rate is reported in points, not in percent of itself
    assert change.direction == "down"


def test_compute_without_previous_bounds():
    st = statement(previous_lower=None, previous_upper=None, status="new", direction_claimed=None)
    change = compute_guidance_change(st)
    assert (
        change.midpoint_previous is None
        and change.delta_absolute is None
        and change.delta_percent is None
    )
    assert change.direction is None and change.direction_claim_matches is None


def test_compute_unchanged_and_single_point():
    st = statement(
        previous_lower=5.0,
        previous_upper=5.0,
        current_lower=5.0,
        current_upper=5.0,
        status="reaffirmed",
        direction_claimed="unchanged",
    )
    change = compute_guidance_change(st)
    assert (
        change.direction == "unchanged"
        and change.delta_absolute == 0.0
        and change.direction_claim_matches is True
    )


def test_compute_rejects_inverted_bounds():
    with pytest.raises(ValueError):
        compute_guidance_change(statement(current_lower=6.0, current_upper=4.0))
