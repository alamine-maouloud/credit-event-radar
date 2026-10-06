"""Derived guidance figures, computed in code from the raw bounds the model quoted."""

from __future__ import annotations

from dataclasses import dataclass

from radar.llm.schemas import GuidanceStatement

EPSILON = 1e-9


@dataclass(frozen=True)
class GuidanceChange:
    midpoint_previous: float | None
    midpoint_current: float | None
    delta_absolute: float | None
    delta_percent: float | None
    direction: str | None
    direction_claim_matches: bool | None
    change_basis: str = "none"  # quantitative | qualitative | none


CHANGING_STATUSES = frozenset({"raised", "cut", "withdrawn"})


def change_basis(status: str, previous: float | None, current: float | None) -> str:
    """quantitative when the text states a change and both ranges are present (a delta can be
    computed), qualitative when it states a change without them, none otherwise."""
    if status not in CHANGING_STATUSES:
        return "none"
    return "quantitative" if previous is not None and current is not None else "qualitative"


def _midpoint(lower: float | None, upper: float | None) -> float | None:
    if lower is None and upper is None:
        return None
    if lower is None:
        return upper
    if upper is None:
        return lower
    if lower > upper:
        raise ValueError(f"inverted bounds: {lower} > {upper}")
    return (lower + upper) / 2.0


def compute_guidance_change(statement: GuidanceStatement) -> GuidanceChange:
    previous = _midpoint(statement.previous_lower, statement.previous_upper)
    current = _midpoint(statement.current_lower, statement.current_upper)
    basis = change_basis(statement.status, previous, current)
    if previous is None or current is None:
        return GuidanceChange(previous, current, None, None, None, None, basis)
    delta = current - previous
    if statement.basis == "yoy_change_pct":
        delta_percent = None  # a change of growth rate is a difference in points
    elif abs(previous) > EPSILON:
        delta_percent = delta / abs(previous) * 100.0
    else:
        delta_percent = None
    if delta < -EPSILON:
        direction = "down"
    elif delta > EPSILON:
        direction = "up"
    else:
        direction = "unchanged"
    claim = statement.direction_claimed
    return GuidanceChange(
        midpoint_previous=previous,
        midpoint_current=current,
        delta_absolute=delta,
        delta_percent=delta_percent,
        direction=direction,
        direction_claim_matches=None if claim is None else (claim == direction),
        change_basis=basis,
    )
