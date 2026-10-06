"""Rating state at date D: admissibility, eligibility, look-ahead guard, freshness, ties."""

from __future__ import annotations

from datetime import timedelta

import pytest

from radar.config import CONFIG_DIR, load_rules
from tests.materiality_helpers import D, candidate, state


@pytest.fixture(scope="module")
def rules():
    return load_rules(CONFIG_DIR / "rules.yaml")


def reasons(st, agency):
    return [i.reason for i in st.ignored if i.agency == agency]


def test_entries_carry_notch_category_age_and_provenance(rules, scales):
    st = state(
        rules, scales, candidate("MOODYS", "Baa3"), candidate("FITCH", "BBB", outlook="negative")
    )
    assert set(st.entries) == {"MOODYS", "FITCH"}
    m = st.entries["MOODYS"]
    assert (m.notch, m.category, m.age_days, m.origin, m.source) == (
        10,
        "IG",
        8,
        "observation",
        "doc_test",
    )
    assert m.verification == "structured_table"
    assert st.entries["FITCH"].outlook == "negative"
    assert st.as_of == D
    assert st.weakest().agency == "MOODYS"


def test_non_admissible_agency_is_ignored_with_reason(rules, scales):
    st = state(rules, scales, candidate("DBRS", "A"), candidate("SP", "A"))
    assert set(st.entries) == {"SP"}
    assert reasons(st, "DBRS") == ["non-admissible agency"]


def test_future_rating_is_never_used(rules, scales):
    """Look-ahead guard: a rating observed after D does not exist for the decision."""
    st = state(
        rules,
        scales,
        candidate("SP", "BB+", as_of=D + timedelta(days=1)),
        candidate("SP", "BBB-", as_of=D - timedelta(days=30)),
    )
    assert st.entries["SP"].rating == "BBB-"
    assert any("after" in r for r in reasons(st, "SP"))


def test_same_day_rating_is_allowed(rules, scales):
    st = state(rules, scales, candidate("SP", "BBB-", as_of=D))
    assert st.entries["SP"].age_days == 0


def test_stale_rating_is_excluded(rules, scales):
    limit = rules.rating_state.max_rating_age_days
    fresh = state(rules, scales, candidate("SP", "A", as_of=D - timedelta(days=limit)))
    stale = state(rules, scales, candidate("SP", "A", as_of=D - timedelta(days=limit + 1)))
    assert "SP" in fresh.entries
    assert "SP" not in stale.entries and any("stale" in r for r in reasons(stale, "SP"))


def test_incomplete_date_is_excluded(rules, scales):
    st = state(rules, scales, candidate("SP", "A", as_of=None))
    assert st.entries == {} and reasons(st, "SP") == ["incomplete date"]


@pytest.mark.parametrize(
    "rating_type,scope",
    [("senior_preferred", "issuer"), ("instrument", "instrument"), ("senior_unsecured", "issuer")],
)
def test_non_issuer_rating_types_are_excluded(rules, scales, rating_type, scope):
    st = state(rules, scales, candidate("SP", "A", rating_type=rating_type, scope=scope))
    assert st.entries == {} and any("not eligible" in r for r in reasons(st, "SP"))


def test_issuer_default_rating_is_eligible(rules, scales):
    st = state(rules, scales, candidate("FITCH", "A-", rating_type="long_term_issuer_default"))
    assert st.entries["FITCH"].notch == 7


def test_latest_dated_rating_wins_and_superseded_is_listed(rules, scales):
    st = state(
        rules,
        scales,
        candidate(
            "SP",
            "BBB",
            as_of=D - timedelta(days=100),
            origin="seed",
            source="https://example.invalid/seed",
        ),
        candidate("SP", "BBB-", as_of=D - timedelta(days=8)),
    )
    assert st.entries["SP"].rating == "BBB-"
    assert any("superseded" in r for r in reasons(st, "SP"))


def test_tie_prefers_event_then_observation_then_seed(rules, scales):
    same = D - timedelta(days=8)
    st = state(
        rules,
        scales,
        candidate("SP", "BBB", as_of=same, origin="seed"),
        candidate("SP", "BBB-", as_of=same, origin="observation"),
        candidate("SP", "BB+", as_of=same, origin="event"),
    )
    assert st.entries["SP"].rating == "BB+"


def test_unknown_label_and_unrated_tokens_are_ignored(rules, scales):
    st = state(rules, scales, candidate("SP", "Baa3"), candidate("MOODYS", "WR"))
    assert st.entries == {}
    assert reasons(st, "SP") == ["unknown rating label"]
    assert reasons(st, "MOODYS") == ["unrated token"]


def test_after_action_replaces_only_the_acting_agency(rules, scales):
    st = state(rules, scales, candidate("SP", "BBB-"), candidate("MOODYS", "Baa3"))
    after = st.after_action("SP", "BB+", scales, outlook="stable")
    assert after.entries["SP"].rating == "BB+" and after.entries["SP"].category == "HY"
    assert after.entries["SP"].origin == "event" and after.entries["SP"].age_days == 0
    assert after.entries["MOODYS"] == st.entries["MOODYS"]
    assert st.entries["SP"].rating == "BBB-"  # original untouched
    assert after.weakest().agency == "SP"


def test_after_action_on_unknown_agency_adds_it(rules, scales):
    st = state(rules, scales, candidate("MOODYS", "Baa3"))
    after = st.after_action("SP", "BB+", scales)
    assert set(after.entries) == {"MOODYS", "SP"}


def test_retrieval_basis_observation_is_prospective_only(rules, scales):
    """A rating read from a current ratings page on day X exists from X onwards, never before."""
    page_day = D - timedelta(days=4)
    cand = candidate(
        "SP",
        "BBB+",
        as_of=page_day,
        as_of_basis="retrieval",
        verification="structured_table_current",
    )
    usable = state(rules, scales, cand, as_of=D)
    assert usable.entries["SP"].as_of_basis == "retrieval" and usable.entries["SP"].age_days == 4
    backtest = state(rules, scales, cand, as_of=page_day - timedelta(days=1))
    assert "SP" not in backtest.entries
    assert any("after" in r for r in reasons(backtest, "SP"))


def test_stated_basis_is_the_default(rules, scales):
    st = state(rules, scales, candidate("MOODYS", "Baa3"))
    assert st.entries["MOODYS"].as_of_basis == "stated"


def test_empty_state(rules, scales):
    st = state(rules, scales)
    assert st.entries == {} and st.weakest() is None


def test_state_never_mentions_composite():
    import inspect

    import radar.materiality.engine as engine
    import radar.materiality.state as state_module

    for module in (engine, state_module):
        source = inspect.getsource(module)
        assert "composite_rating" not in source
        assert "CompositeRating" not in source
