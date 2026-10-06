"""Builders shared by the materiality tests. Every issuer and value is fictional."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from radar.config import Rules
from radar.materiality.engine import Decision, MaterialityEngine, PriorContext
from radar.materiality.state import RatingCandidate, RatingState, build_rating_state
from radar.models import CreditEvent
from radar.ratings import RatingScales

D = date(2026, 7, 8)


def ev(
    family: str = "rating",
    event_type: str = "downgrade",
    *,
    issuer: str = "ISSUER_TEST_A",
    effective: date | None = D,
    method: str = "structured",
    **fields: Any,
) -> CreditEvent:
    return CreditEvent(
        event_id=f"evt_test_{family}_{event_type}",
        issuer_id=issuer,
        family=family,  # type: ignore[arg-type]
        event_type=event_type,
        effective_date=effective,
        fields=fields,
        extraction_method=method,  # type: ignore[arg-type]
        source_doc_ids=["doc_test"],
    )


def rating_event(
    agency: str, old: str, new: str, event_type: str = "downgrade", **extra: Any
) -> CreditEvent:
    return ev("rating", event_type, agency=agency, old_rating=old, new_rating=new, **extra)


def candidate(
    agency: str,
    rating: str,
    *,
    as_of: date | None = D - timedelta(days=8),
    origin: str = "observation",
    rating_type: str = "long_term_issuer",
    scope: str = "issuer",
    outlook: str | None = "stable",
    watch: str = "none",
    source: str = "doc_test",
    verification: str = "structured_table",
    as_of_basis: str = "stated",
) -> RatingCandidate:
    return RatingCandidate(
        agency=agency,
        rating=rating,
        as_of=as_of,
        as_of_basis=as_of_basis,
        origin=origin,
        rating_type=rating_type,
        scope=scope,
        outlook=outlook,
        watch=watch,
        source=source,
        verification=verification,
    )  # type: ignore[arg-type]


def state(
    rules: Rules, scales: RatingScales, *candidates: RatingCandidate, as_of: date = D
) -> RatingState:
    return build_rating_state(list(candidates), as_of, rules.rating_state, scales)


def decide(
    rules: Rules,
    scales: RatingScales,
    event: CreditEvent,
    rating_state: RatingState | None = None,
    prior: PriorContext | None = None,
) -> Decision:
    return MaterialityEngine(rules, scales).evaluate(event, rating_state, prior)


def outcome(decision: Decision, rule_id: str):
    for item in [*decision.rules, *decision.modifiers]:
        if item.id == rule_id:
            return item
    raise KeyError(rule_id)


def triggered(decision: Decision) -> set[str]:
    return {r.id for r in decision.rules if r.triggered} | {
        m.id for m in decision.modifiers if m.applied
    }
