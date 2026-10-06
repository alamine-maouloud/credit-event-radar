"""Behaviour of every base rule in rules.yaml 1.2, positives and counter-examples."""

from __future__ import annotations

from datetime import timedelta

import pytest

from radar.config import CONFIG_DIR, load_rules
from radar.materiality.engine import MaterialityEngine
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


@pytest.fixture(scope="module")
def engine(rules, scales):
    return MaterialityEngine(rules, scales)


def harley_state(rules, scales):
    """Moody's Baa3 and Fitch BBB observed 8 days before D, both IG."""
    return state(
        rules,
        scales,
        candidate("SP", "BBB-", watch="negative", outlook=None),
        candidate("MOODYS", "Baa3"),
        candidate("FITCH", "BBB", outlook="negative"),
    )


# ------------------------------------------------------------ registry --- #


def test_every_yaml_condition_has_a_function_and_vice_versa(rules, engine):
    yaml_conditions = {r.condition for r in rules.rules} | {m.condition for m in rules.modifiers}
    assert yaml_conditions == set(engine.conditions)


def test_decision_lists_every_rule_and_modifier(rules, scales):
    d = decide(rules, scales, rating_event("SP", "BBB-", "BB+"))
    assert [r.id for r in d.rules] == [r.id for r in rules.rules]
    assert [m.id for m in d.modifiers] == [m.id for m in rules.modifiers]
    assert d.rules_version == "1.5"


# ------------------------------------------------- RAT-01 / 03 / 05 / 08 --- #


@pytest.mark.parametrize(
    "agency,old,new,expected",
    [
        ("SP", "BBB-", "BB+", {"RAT-01"}),  # crossing, one notch
        ("SP", "BBB-", "BB", {"RAT-01", "RAT-03"}),  # crossing and severity: orthogonal
        ("SP", "BBB", "BB+", {"RAT-01", "RAT-03"}),
        ("MOODYS", "Baa3", "Ba1", {"RAT-01"}),
        ("FITCH", "BBB-", "BB+", {"RAT-01"}),
        ("SP", "BBB", "BBB-", {"RAT-05"}),  # stays IG: not a fallen angel
        ("SP", "A", "BBB+", {"RAT-03"}),  # two notches, stays IG
        ("SP", "BB+", "BB", {"RAT-05"}),  # already HY
        ("SP", "BB", "B", {"RAT-03"}),  # three notches within HY
        ("SP", "CCC+", "D", {"RAT-03"}),
    ],
)
def test_downgrade_rules(rules, scales, agency, old, new, expected):
    d = decide(rules, scales, rating_event(agency, old, new))
    assert triggered(d) == expected
    assert d.final_priority == ("P1" if expected & {"RAT-01", "RAT-03"} else "P2")


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("BB+", "BBB-", {"RAT-08"}),  # rising star
        ("BB", "BBB-", {"RAT-08"}),
        ("BBB-", "BBB", {"RAT-09"}),  # upgrade within IG
        ("B", "BB-", {"RAT-09"}),  # upgrade within HY, two notches is not RAT-03
    ],
)
def test_upgrade_rules(rules, scales, old, new, expected):
    d = decide(rules, scales, rating_event("SP", old, new, event_type="upgrade"))
    assert triggered(d) == expected
    assert outcome(d, "RAT-03").triggered is False
    assert d.final_priority == ("P2" if "RAT-08" in expected else "P3")


def test_downgrade_labels_drive_categories_not_fields(rules, scales):
    """The engine recomputes notches from the labels and the scale, never trusting extras."""
    d = decide(
        rules, scales, rating_event("SP", "BBB-", "BB+", crosses_ig_to_hy=False, notch_delta=0)
    )
    assert "RAT-01" in triggered(d)


def test_unknown_label_triggers_nothing_and_explains(rules, scales):
    d = decide(rules, scales, rating_event("SP", "Baa3", "BB+"))
    assert triggered(d) == set() and d.final_priority is None
    assert d.decision_status == "NO_APPLICABLE_RULE"
    assert "unknown" in outcome(d, "RAT-01").reason.lower()


# ---------------------------------------------------------------- RAT-02 --- #


def test_rat02_split_rating_harley(rules, scales):
    d = decide(
        rules,
        scales,
        rating_event("SP", "BBB-", "BB+", new_outlook="stable"),
        harley_state(rules, scales),
    )
    assert {"RAT-01", "RAT-02"} <= triggered(d)
    o = outcome(d, "RAT-02")
    assert o.triggered and "MOODYS" in o.reason and "FITCH" in o.reason
    assert o.data["other_ig_agencies"] == ["FITCH", "MOODYS"]
    assert d.final_priority == "P1"


def test_rat02_not_triggered_when_other_agencies_already_hy(rules, scales):
    st = state(rules, scales, candidate("MOODYS", "Ba1"), candidate("FITCH", "BB+"))
    d = decide(rules, scales, rating_event("SP", "BBB-", "BB+"), st)
    assert "RAT-01" in triggered(d) and "RAT-02" not in triggered(d)
    assert "HY" in outcome(d, "RAT-02").reason


def test_rat02_not_triggered_without_other_ratings(rules, scales):
    d = decide(rules, scales, rating_event("SP", "BBB-", "BB+"), None)
    assert "RAT-02" not in triggered(d)
    assert "no other admissible" in outcome(d, "RAT-02").reason.lower()
    d2 = decide(rules, scales, rating_event("SP", "BBB-", "BB+"), state(rules, scales))
    assert "RAT-02" not in triggered(d2)


def test_rat02_ignores_future_stale_and_non_admissible_ratings(rules, scales):
    st = state(
        rules,
        scales,
        candidate("MOODYS", "Baa3", as_of=D + timedelta(days=1)),  # published after D
        candidate("FITCH", "BBB", as_of=D - timedelta(days=401)),  # stale
        candidate("DBRS", "BBB (low)"),  # not admissible
    )
    d = decide(rules, scales, rating_event("SP", "BBB-", "BB+"), st)
    assert "RAT-02" not in triggered(d)
    assert len(st.ignored) == 3


def test_rat02_uses_only_the_acting_agency_crossing(rules, scales):
    """A one-notch downgrade that stays IG is not RAT-02 even with other IG agencies."""
    d = decide(rules, scales, rating_event("SP", "BBB", "BBB-"), harley_state(rules, scales))
    assert "RAT-02" not in triggered(d)


def test_rat02_acting_agency_own_old_rating_does_not_count_as_other(rules, scales):
    st = state(rules, scales, candidate("SP", "BBB-"))
    d = decide(rules, scales, rating_event("SP", "BBB-", "BB+"), st)
    assert "RAT-02" not in triggered(d)


# ---------------------------------------------------------- RAT-04 / 07 --- #


def test_rat04_negative_watch_at_boundary(rules, scales):
    d = decide(rules, scales, ev("rating", "watch", agency="SP", rating="BBB-", watch="negative"))
    assert triggered(d) == {"RAT-04"} and d.final_priority == "P1"


def test_rat04_from_downgrade_landing_on_boundary_with_watch(rules, scales):
    d = decide(rules, scales, rating_event("SP", "BBB", "BBB-", watch="negative"))
    assert {"RAT-04", "RAT-05"} <= triggered(d) and "RAT-07" not in triggered(d)
    assert d.base_priority == "P1"


def test_rat07_negative_watch_elsewhere(rules, scales):
    d = decide(rules, scales, ev("rating", "watch", agency="SP", rating="BBB", watch="negative"))
    assert triggered(d) == {"RAT-07"} and d.final_priority == "P2"


def test_rat04_uses_state_when_event_has_no_rating(rules, scales):
    st = state(rules, scales, candidate("MOODYS", "Baa3"))
    d = decide(rules, scales, ev("rating", "watch", agency="MOODYS", watch="negative"), st)
    assert "RAT-04" in triggered(d)
    d2 = decide(rules, scales, ev("rating", "watch", agency="MOODYS", watch="negative"), None)
    assert "RAT-07" in triggered(d2) and "RAT-04" not in triggered(d2)


@pytest.mark.parametrize("watch", ["positive", "developing", "none"])
def test_non_negative_watch_is_not_rat04_or_07(rules, scales, watch):
    d = decide(rules, scales, ev("rating", "watch", agency="SP", rating="BBB-", watch=watch))
    assert not {"RAT-04", "RAT-07"} & triggered(d)


# ---------------------------------------------------------- RAT-06 / 09 --- #


@pytest.mark.parametrize("old", ["stable", "positive"])
def test_rat06_outlook_to_negative(rules, scales, old):
    d = decide(
        rules,
        scales,
        ev("rating", "outlook_change", agency="SP", old_outlook=old, new_outlook="negative"),
    )
    assert triggered(d) == {"RAT-06"} and d.final_priority == "P2"


def test_rat06_not_from_negative_or_unknown(rules, scales):
    d = decide(
        rules,
        scales,
        ev("rating", "outlook_change", agency="SP", old_outlook="negative", new_outlook="negative"),
    )
    assert "RAT-06" not in triggered(d)
    d2 = decide(
        rules, scales, ev("rating", "outlook_change", agency="SP", new_outlook="negative"), None
    )
    assert "RAT-06" not in triggered(d2) and "unknown" in outcome(d2, "RAT-06").reason.lower()


def test_rat06_previous_outlook_from_state(rules, scales):
    st = state(rules, scales, candidate("SP", "BBB", outlook="stable"))
    d = decide(
        rules, scales, ev("rating", "outlook_change", agency="SP", new_outlook="negative"), st
    )
    assert "RAT-06" in triggered(d)


def test_rat06_combined_with_downgrade(rules, scales):
    d = decide(
        rules, scales, rating_event("SP", "A", "A-", old_outlook="stable", new_outlook="negative")
    )
    assert {"RAT-05", "RAT-06"} <= triggered(d)


@pytest.mark.parametrize(
    "fields",
    [
        dict(new_outlook="positive", old_outlook="stable"),
        dict(watch="positive"),
        dict(watch="positive", rating="BBB-"),
    ],
)
def test_rat09_positive_outlook_or_watch(rules, scales, fields):
    d = decide(rules, scales, ev("rating", "outlook_change", agency="SP", **fields))
    assert triggered(d) == {"RAT-09"} and d.final_priority == "P3"


# ---------------------------------------------------------------- RAT-10 --- #


def test_rat10_affirmation(rules, scales):
    d = decide(rules, scales, ev("rating", "affirmation", agency="SP", rating="BBB-"))
    assert triggered(d) == {"RAT-10"} and d.final_priority == "P3"


def test_affirmation_with_outlook_change_is_not_rat10(rules, scales):
    d = decide(
        rules,
        scales,
        ev(
            "rating",
            "affirmation",
            agency="SP",
            rating="BBB",
            old_outlook="stable",
            new_outlook="negative",
        ),
    )
    assert triggered(d) == {"RAT-06"}


# --------------------------------------------------------------- earnings --- #


@pytest.mark.parametrize("flag", ["liquidity", "going_concern", "covenant"])
def test_ern01_flags(rules, scales, flag):
    d = decide(rules, scales, ev("earnings", "earnings_release", flags=[flag]))
    assert triggered(d) == {"ERN-01"} and d.final_priority == "P1"


def test_impairment_is_ern03_not_ern01(rules, scales):
    d = decide(rules, scales, ev("earnings", "earnings_release", flags=["impairment"]))
    assert triggered(d) == {"ERN-03"} and d.final_priority == "P2"


@pytest.mark.parametrize(
    "metric,pct,expected",
    [
        ("revenue", -10.0, {"ERN-02"}),  # exactly at threshold
        ("ebitda", -25.0, {"ERN-02"}),
        ("fcf", -9.99, {"ERN-03"}),
        ("revenue", -0.5, {"ERN-03"}),
        ("revenue", 0.0, set()),  # no explicit guidance statement: nothing can be concluded
        ("revenue", 3.0, set()),
        ("capex", -30.0, set()),  # metric outside the rule's scope, no statement
    ],
)
def test_guidance_thresholds(rules, scales, metric, pct, expected):
    d = decide(
        rules,
        scales,
        ev("earnings", "guidance_update", guidance_metric=metric, guidance_change_pct=pct),
    )
    assert triggered(d) == expected
    if not expected:
        assert d.final_priority is None and d.decision_status == "NO_APPLICABLE_RULE"


@pytest.mark.parametrize("status", ["reaffirmed", "in_line"])
def test_ern04_requires_explicit_guidance_evidence(rules, scales, status):
    d = decide(rules, scales, ev("earnings", "earnings_release", guidance_status=status))
    assert triggered(d) == {"ERN-04"} and d.final_priority == "P3"
    assert status in outcome(d, "ERN-04").reason


@pytest.mark.parametrize("status", ["raised", "cut", "withdrawn", None])
def test_ern04_not_from_other_statements(rules, scales, status):
    fields = {"guidance_status": status} if status else {}
    d = decide(rules, scales, ev("earnings", "earnings_release", **fields))
    assert "ERN-04" not in triggered(d)


def test_ern04_not_when_status_contradicts_numbers(rules, scales):
    d = decide(
        rules,
        scales,
        ev(
            "earnings",
            "x",
            guidance_status="reaffirmed",
            guidance_metric="fcf",
            guidance_change_pct=-1.0,
        ),
    )
    assert "ERN-04" not in triggered(d) and "ERN-03" in triggered(d)


def test_guidance_pct_computed_from_old_and_new(rules, scales):
    d = decide(
        rules,
        scales,
        ev(
            "earnings",
            "guidance_update",
            guidance_metric="EBITDA",
            guidance_old=200.0,
            guidance_new=170.0,
        ),
    )
    assert "ERN-02" in triggered(d) and outcome(d, "ERN-02").data["guidance_change_pct"] == -15.0


def test_guidance_qualified_significant_without_number(rules, scales):
    d = decide(
        rules, scales, ev("earnings", "guidance_update", guidance_qualified_significant=True)
    )
    assert triggered(d) == {"ERN-02"}


def test_earnings_release_skeleton_has_no_priority(rules, scales):
    """An 8-K Item 2.02 says results were published, not that they were in line (ADR-012)."""
    d = decide(rules, scales, ev("earnings", "earnings_release", form="8-K", edgar_item="2.02"))
    assert triggered(d) == set() and d.final_priority is None
    assert "no explicit guidance statement" in outcome(d, "ERN-04").reason


def test_ern04_not_with_flags_or_cut(rules, scales):
    assert "ERN-04" not in triggered(decide(rules, scales, ev("earnings", "x", flags=["covenant"])))
    assert "ERN-04" not in triggered(
        decide(rules, scales, ev("earnings", "x", guidance_metric="fcf", guidance_change_pct=-1.0))
    )


# --------------------------------------------------------------- issuance --- #


@pytest.mark.parametrize(
    "fields,expected,priority",
    [
        (dict(amount_eur_equiv=1_000_000_000.0, seniority="senior"), {"ISS-01"}, "P2"),
        (dict(amount_eur_equiv=999_999_999.0, seniority="senior"), {"ISS-04"}, "P3"),
        (dict(amount_eur_equiv=500_000_000.0, seniority="AT1"), {"ISS-02"}, "P2"),
        (dict(amount_eur_equiv=1_500_000_000.0, seniority="hybrid"), {"ISS-01", "ISS-02"}, "P2"),
        (dict(seniority="T2"), {"ISS-02"}, "P2"),
        (dict(seniority="subordinated"), {"ISS-02"}, "P2"),
        (dict(), set(), None),  # amount and seniority unknown: nothing can be concluded (ADR-012)
        (dict(amount=2_000_000_000.0, currency="USD"), set(), None),  # no EUR equivalent: no rule
        (dict(seniority="senior"), set(), None),
    ],
)
def test_issuance_rules(rules, scales, fields, expected, priority):
    d = decide(rules, scales, ev("issuance", "new_issue", **fields))
    assert triggered(d) == expected and d.final_priority == priority


def test_iss04_explicit_tap_is_routine(rules, scales):
    d = decide(
        rules, scales, ev("issuance", "tap", amount_eur_equiv=200_000_000.0, seniority="senior")
    )
    assert triggered(d) == {"ISS-04"} and d.final_priority == "P3"
    d2 = decide(rules, scales, ev("issuance", "tap"))
    assert triggered(d2) == {"ISS-04"}


def test_redemption_has_no_applicable_rule(rules, scales):
    d = decide(
        rules,
        scales,
        ev("issuance", "redemption", amount_eur_equiv=750_000_000.0, seniority="subordinated"),
    )
    assert d.final_priority is None


@pytest.mark.parametrize(
    "seniority,expected",
    [("AT1", {"ISS-03"}), ("hybrid", {"ISS-03"}), ("T2", set()), ("senior", set())],
)
def test_iss03_non_call(rules, scales, seniority, expected):
    d = decide(rules, scales, ev("issuance", "non_call", seniority=seniority))
    assert triggered(d) == expected
    assert d.final_priority == ("P1" if expected else None)


# ------------------------------------------------------------------ edgar --- #


def test_edg01(rules, scales):
    d = decide(rules, scales, ev("other", "obligation_acceleration", form="8-K", edgar_item="2.04"))
    assert triggered(d) == {"EDG-01"} and d.final_priority == "P1" and d.negative


@pytest.mark.parametrize("item", ["2.02", "5.02", "1.01"])
def test_other_items_do_not_trigger_edg01(rules, scales, item):
    d = decide(rules, scales, ev("other", "x", form="8-K", edgar_item=item))
    assert "EDG-01" not in triggered(d)


# -------------------------------------------------------- no applicable rule --- #


def test_management_change_has_no_priority(rules, scales):
    d = decide(rules, scales, ev("other", "management_change", form="8-K", edgar_item="5.02"))
    assert d.final_priority is None and d.base_priority is None
    assert d.decision_status == "NO_APPLICABLE_RULE"
    assert all(not r.triggered for r in d.rules) and all(not m.applied for m in d.modifiers)
    pd = d.to_priority_decision()
    assert (
        pd.priority is None
        and pd.decision_status == "NO_APPLICABLE_RULE"
        and pd.triggered_rules == []
    )


# --------------------------------------------------------------- provenance --- #


def test_provenance_fields(rules, scales):
    d = decide(rules, scales, rating_event("SP", "BBB-", "BB+"), harley_state(rules, scales))
    assert d.provenance.agency_ratings_used is True
    assert d.provenance.composite_used is False
    assert d.provenance.llm_used == "none"
    pd = d.to_priority_decision()
    assert pd.llm_role == "none" and pd.rules_version == "1.5"
    assert pd.triggered_rules == ["RAT-01", "RAT-02"]
    assert any("S&P" in line or "SP" in line for line in pd.rule_details)


def test_provenance_reports_llm_extraction(rules, scales):
    d = decide(rules, scales, rating_event("SP", "BBB-", "BB+", method="llm_validated"))
    assert d.provenance.llm_used == "field extraction (validated against source text)"
    assert d.to_priority_decision().llm_role != "none"


def test_composite_is_never_called(rules, scales, monkeypatch):
    import radar.ratings as ratings

    def boom(*args, **kwargs):
        raise AssertionError("composite_rating must not be consulted by the engine")

    monkeypatch.setattr(ratings, "composite_rating", boom)
    d = decide(rules, scales, rating_event("SP", "BBB-", "BB+"), harley_state(rules, scales))
    assert d.final_priority == "P1"


def test_effective_date_defaults_to_state_date(rules, scales):
    d = decide(
        rules,
        scales,
        rating_event("SP", "BBB-", "BB+", effective=None),
        harley_state(rules, scales),
    )
    assert d.effective_date == D


def test_non_admissible_agency_events_trigger_no_rating_rule(rules, scales):
    """DBRS actions are stored and shown but never drive a rule in v1 (user decision, 2026-10-06)."""
    d = decide(rules, scales, rating_event("DBRS", "BBB (low)", "BB (high)"))
    assert triggered(d) == set() and d.final_priority is None
    assert "not admissible" in outcome(d, "RAT-01").reason
    d2 = decide(
        rules,
        scales,
        ev("rating", "outlook_change", agency="DBRS", old_outlook="stable", new_outlook="negative"),
    )
    assert "RAT-06" not in triggered(d2)


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"guidance_status": "cut"}, {"ERN-03"}),  # deterministic "lowers its outlook", no figure
        ({"guidance_status": "cut", "guidance_metric": "revenue"}, {"ERN-03"}),  # qualitative cut
        ({"guidance_status": "cut", "guidance_metric": "margin"}, set()),  # outside the scope
        (
            {"guidance_status": "cut", "guidance_metric": "fcf", "guidance_change_pct": -12.0},
            {"ERN-02"},
        ),
        ({"guidance_status": "reaffirmed"}, {"ERN-04"}),
    ],
)
def test_explicit_cut_without_comparable_magnitude_is_ern03(rules, scales, fields, expected):
    d = decide(rules, scales, ev("earnings", "earnings_release", **fields))
    assert triggered(d) == expected
