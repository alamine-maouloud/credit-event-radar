"""Modifiers MOD-01 to MOD-03 and the combination policy."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from radar.config import CONFIG_DIR, load_rules
from radar.materiality.engine import Fundamentals, PriorContext, PriorEvent
from tests.materiality_helpers import (
    D,
    candidate,
    decide,
    ev,
    outcome,
    rating_event,
    state,
    triggered,
)


@pytest.fixture(scope="module")
def rules():
    return load_rules(CONFIG_DIR / "rules.yaml")


# ---------------------------------------------------------------- MOD-01 --- #


def test_mod01_raises_p2_to_p1_when_weakest_rating_lands_on_boundary(rules, scales):
    st = state(rules, scales, candidate("SP", "BBB"), candidate("MOODYS", "Baa2"))
    d = decide(rules, scales, rating_event("SP", "BBB", "BBB-"), st)
    assert triggered(d) == {"RAT-05", "MOD-01"}
    assert d.base_priority == "P2" and d.final_priority == "P1"
    assert outcome(d, "MOD-01").data["weakest"] == {"agency": "SP", "rating": "BBB-", "notch": 10}


def test_mod01_raises_p3_to_p2(rules, scales):
    st = state(rules, scales, candidate("SP", "BBB-"))
    d = decide(
        rules,
        scales,
        ev("earnings", "guidance_update", guidance_metric="fcf", guidance_change_pct=-2.0),
        st,
    )
    assert triggered(d) == {"ERN-03", "MOD-01"} and d.final_priority == "P1"
    d2 = decide(
        rules,
        scales,
        ev("earnings", "earnings_release", flags=["impairment"]),
        state(rules, scales, candidate("FITCH", "BBB-")),
    )
    assert d2.base_priority == "P2" and d2.final_priority == "P1"


def test_mod01_not_applied_when_issuer_already_hy(rules, scales):
    """After the Harley action the weakest rating is BB+: not at the boundary."""
    st = state(rules, scales, candidate("MOODYS", "Baa3"), candidate("FITCH", "BBB"))
    d = decide(rules, scales, rating_event("SP", "BBB-", "BB+"), st)
    assert "MOD-01" not in triggered(d) and "HY" in outcome(d, "MOD-01").reason


def test_mod01_not_applied_to_positive_or_neutral_events(rules, scales):
    st = state(rules, scales, candidate("SP", "BBB-"))
    assert "MOD-01" not in triggered(
        decide(rules, scales, rating_event("SP", "BB+", "BBB-", event_type="upgrade"), st)
    )
    assert "MOD-01" not in triggered(
        decide(rules, scales, ev("issuance", "new_issue", amount_eur_equiv=2e9), st)
    )
    assert "MOD-01" not in triggered(
        decide(rules, scales, ev("rating", "affirmation", agency="SP", rating="BBB-"), st)
    )


def test_mod01_not_applied_without_state(rules, scales):
    d = decide(rules, scales, rating_event("SP", "A", "A-"), None)
    assert (
        "MOD-01" not in triggered(d)
        and "no admissible rating" in outcome(d, "MOD-01").reason.lower()
    )


def test_mod01_uses_weakest_not_acting_agency(rules, scales):
    st = state(rules, scales, candidate("SP", "A"), candidate("FITCH", "BBB-"))
    d = decide(rules, scales, rating_event("SP", "A", "A-"), st)
    assert "MOD-01" in triggered(d) and outcome(d, "MOD-01").data["weakest"]["agency"] == "FITCH"


def test_mod01_ignores_non_admissible_and_future_ratings(rules, scales):
    st = state(
        rules,
        scales,
        candidate("SP", "A"),
        candidate("DBRS", "BBB (low)"),
        candidate("FITCH", "BBB-", as_of=D + timedelta(days=2)),
    )
    d = decide(rules, scales, rating_event("SP", "A", "A-"), st)
    assert "MOD-01" not in triggered(d)


# ---------------------------------------------------------------- MOD-02 --- #


def prior(*days: int, base: str = "P2", negative: bool = True) -> PriorContext:
    return PriorContext(
        prior_events=[
            PriorEvent(
                event_id=f"prior_{i}",
                effective_date=D - timedelta(days=n),
                base_priority=base,
                negative=negative,
            )
            for i, n in enumerate(days)
        ]
    )


def test_mod02_second_negative_p2_in_window_becomes_p1(rules, scales):
    d = decide(rules, scales, rating_event("SP", "A", "A-"), None, prior(10))
    assert triggered(d) == {"RAT-05", "MOD-02"} and d.final_priority == "P1"
    assert outcome(d, "MOD-02").data["count"] == 2


def test_mod02_window_is_inclusive_on_effective_dates(rules, scales):
    assert "MOD-02" in triggered(
        decide(rules, scales, rating_event("SP", "A", "A-"), None, prior(30))
    )
    assert "MOD-02" not in triggered(
        decide(rules, scales, rating_event("SP", "A", "A-"), None, prior(31))
    )


def test_mod02_ignores_future_prior_events(rules, scales):
    """A later event must never change an earlier decision (look-ahead guard)."""
    assert "MOD-02" not in triggered(
        decide(rules, scales, rating_event("SP", "A", "A-"), None, prior(-1))
    )


def test_mod02_counts_only_negative_p2(rules, scales):
    assert "MOD-02" not in triggered(
        decide(rules, scales, rating_event("SP", "A", "A-"), None, prior(5, base="P3"))
    )
    assert "MOD-02" not in triggered(
        decide(rules, scales, rating_event("SP", "A", "A-"), None, prior(5, base="P1"))
    )
    assert "MOD-02" not in triggered(
        decide(rules, scales, rating_event("SP", "A", "A-"), None, prior(5, negative=False))
    )


def test_mod02_requires_current_event_negative_p2(rules, scales):
    assert "MOD-02" not in triggered(
        decide(rules, scales, rating_event("SP", "BBB-", "BB+"), None, prior(5))
    )  # already P1
    assert "MOD-02" not in triggered(
        decide(rules, scales, ev("rating", "affirmation", agency="SP", rating="A"), None, prior(5))
    )


def test_mod02_min_events_threshold(rules, scales):
    assert rules.thresholds.mod02_min_events == 2
    d = decide(rules, scales, rating_event("SP", "A", "A-"), None, PriorContext())
    assert "MOD-02" not in triggered(d)


# ---------------------------------------------------------------- MOD-03 --- #


def with_leverage(rules, threshold):
    return rules.model_copy(
        update={"thresholds": rules.thresholds.model_copy(update={"leverage_threshold": threshold})}
    )


def fundamentals(
    leverage: float, *, as_of: date = D - timedelta(days=20), verified: bool = True
) -> PriorContext:
    return PriorContext(
        fundamentals=Fundamentals(
            leverage=leverage, as_of=as_of, source="doc_xbrl", verified=verified
        )
    )


def test_mod03_never_applies_without_threshold(rules, scales):
    assert rules.thresholds.leverage_threshold is None
    d = decide(rules, scales, rating_event("SP", "A", "A-"), None, fundamentals(9.0))
    assert "MOD-03" not in triggered(d) and "threshold" in outcome(d, "MOD-03").reason.lower()


def test_mod03_applies_with_sourced_leverage_above_threshold(rules, scales):
    r = with_leverage(rules, 4.0)
    d = decide(r, scales, rating_event("SP", "A", "A-"), None, fundamentals(4.5))
    assert triggered(d) == {"RAT-05", "MOD-03"} and d.final_priority == "P1"


@pytest.mark.parametrize(
    "ctx",
    [
        fundamentals(4.0),
        fundamentals(4.5, verified=False),
        fundamentals(4.5, as_of=D + timedelta(days=1)),
        PriorContext(),
    ],
)
def test_mod03_counter_examples(rules, scales, ctx):
    r = with_leverage(rules, 4.0)
    d = decide(r, scales, rating_event("SP", "A", "A-"), None, ctx)
    assert "MOD-03" not in triggered(d)


def test_mod03_not_on_positive_event(rules, scales):
    r = with_leverage(rules, 4.0)
    d = decide(
        r, scales, rating_event("SP", "BB+", "BBB-", event_type="upgrade"), None, fundamentals(9.0)
    )
    assert "MOD-03" not in triggered(d)


# ------------------------------------------------------------ combination --- #


def test_modifiers_never_lower_and_cap_at_p1(rules, scales):
    st = state(rules, scales, candidate("MOODYS", "Baa3"), candidate("FITCH", "BBB"))
    d = decide(
        rules, scales, rating_event("SP", "BBB-", "BB", new_outlook="negative"), st, prior(3)
    )
    assert d.base_priority == "P1" and d.final_priority == "P1"


def test_two_modifiers_do_not_stack_beyond_p1(rules, scales):
    r = with_leverage(rules, 4.0)
    st = state(r, scales, candidate("SP", "BBB-"))
    d = decide(
        r,
        scales,
        ev("earnings", "guidance_update", guidance_metric="fcf", guidance_change_pct=-2.0),
        st,
        fundamentals(9.0),
    )
    assert {"ERN-03", "MOD-01", "MOD-03"} <= triggered(d) and d.final_priority == "P1"


def test_modifiers_not_applied_without_base_rule(rules, scales):
    st = state(rules, scales, candidate("SP", "BBB-"))
    d = decide(rules, scales, ev("other", "management_change", edgar_item="5.02"), st, prior(3))
    assert d.final_priority is None and all(not m.applied for m in d.modifiers)
