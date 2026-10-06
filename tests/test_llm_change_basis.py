"""change_basis is computed in code: quantitative when a delta is computable, qualitative when
the text states a change without both ranges, none for new, reaffirmed and mentioned."""

from __future__ import annotations

import pytest

from radar.llm.compute import compute_guidance_change
from radar.llm.schemas import GuidanceStatement


def _st(status, **bounds):
    return GuidanceStatement(
        metric="revenue",
        metric_label="sales revenue",
        basis="yoy_change_pct",
        unit="PCT",
        period="2026",
        status=status,
        evidence_quote="x",
        start_offset=0,
        end_offset=1,
        **bounds,
    )


def test_quantitative_change_has_a_delta():
    change = compute_guidance_change(
        _st("cut", previous_lower=-5, previous_upper=5, current_lower=-10, current_upper=0)
    )
    assert change.change_basis == "quantitative"
    assert change.delta_absolute == -5 and change.direction == "down"


@pytest.mark.parametrize(
    "bounds",
    [
        dict(
            current_lower=0, current_upper=7
        ),  # TRATON H1 2026: raised to the upper end, no earlier range
        dict(previous_upper=5),  # VW July 2025: in line with the previous year, previously up to 5
        {},
    ],
)
def test_qualitative_change_has_no_delta(bounds):
    change = compute_guidance_change(_st("raised", **bounds))
    assert change.change_basis == "qualitative"
    assert change.delta_absolute is None and change.direction is None


@pytest.mark.parametrize("status", ["new", "reaffirmed", "mentioned"])
def test_no_change_basis_without_a_change(status):
    change = compute_guidance_change(
        _st(status, previous_lower=1, previous_upper=2, current_lower=1, current_upper=2)
    )
    assert change.change_basis == "none"
