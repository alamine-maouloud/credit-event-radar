"""Composite rating tests (SPEC 9.2): middle and average methods, rounding, deduplication."""

from __future__ import annotations

from datetime import date

import pytest

from radar.ratings import composite_rating
from tests.conftest import make_rating

BIG_THREE = ("SP", "MOODYS", "FITCH")


# ---------------------------------------------------------------- middle --- #


def test_middle_three_ratings_takes_median(scales):
    ratings = [
        make_rating("SP", "BBB+"),  # 8
        make_rating("MOODYS", "Baa3"),  # 10
        make_rating("FITCH", "BBB"),  # 9
    ]
    result = composite_rating(ratings, scales, method="middle")
    assert result is not None
    assert result.notch == 9
    assert result.label == "BBB"
    assert result.category == "IG"
    assert result.method == "middle"
    assert result.rounding is None
    assert result.n_ratings == 3


def test_middle_two_ratings_takes_lowest(scales):
    ratings = [make_rating("SP", "BBB"), make_rating("MOODYS", "Ba1")]
    result = composite_rating(ratings, scales, method="middle")
    assert result is not None
    assert result.notch == 11
    assert result.category == "HY"


def test_middle_single_rating(scales):
    result = composite_rating([make_rating("FITCH", "A-")], scales, method="middle")
    assert result is not None
    assert result.notch == 7
    assert result.label == "A-"


def test_no_rating_returns_none(scales):
    assert composite_rating([], scales, method="middle") is None
    assert composite_rating([], scales, method="average") is None


def test_rat02_scenario_middle_stays_ig(scales):
    """One agency downgrades BBB- to BB+, the other two stay at BBB-: composite stays IG."""
    before = [
        make_rating("SP", "BBB-"),
        make_rating("MOODYS", "Baa3"),
        make_rating("FITCH", "BBB-"),
    ]
    after = [
        make_rating("SP", "BB+"),
        make_rating("MOODYS", "Baa3"),
        make_rating("FITCH", "BBB-"),
    ]
    assert composite_rating(before, scales, method="middle").notch == 10
    after_result = composite_rating(after, scales, method="middle")
    assert after_result.notch == 10
    assert after_result.category == "IG"


def test_fallen_angel_scenario_middle(scales):
    """Two of three agencies in HY: composite crosses to HY (RAT-01 input)."""
    ratings = [
        make_rating("SP", "BB+"),
        make_rating("MOODYS", "Ba1"),
        make_rating("FITCH", "BBB-"),
    ]
    result = composite_rating(ratings, scales, method="middle")
    assert result.notch == 11
    assert result.category == "HY"


def test_middle_four_ratings_takes_weaker_middle(scales):
    """Even count: the weaker of the two middle values, consistent with rounding toward weaker."""
    ratings = [
        make_rating("SP", "BBB"),  # 9
        make_rating("MOODYS", "Baa3"),  # 10
        make_rating("FITCH", "BBB-"),  # 10
        make_rating("DBRS", "BB (high)"),  # 11
    ]
    result = composite_rating(ratings, scales, method="middle", agencies=None)
    assert result.notch == 10


# --------------------------------------------------------------- average --- #


def test_average_exact_mean(scales):
    ratings = [make_rating("SP", "BBB+"), make_rating("MOODYS", "Baa3")]  # 8, 10
    result = composite_rating(ratings, scales, method="average")
    assert result.notch == 9
    assert result.method == "average"
    assert result.rounding == "nearest_weaker"


def test_average_default_rounding_does_not_flip_on_one_downgrade(scales):
    """Mean 10.33 (BB+, BBB-, BBB-) stays at 10 with nearest_weaker rounding."""
    ratings = [
        make_rating("SP", "BB+"),
        make_rating("MOODYS", "Baa3"),
        make_rating("FITCH", "BBB-"),
    ]
    result = composite_rating(ratings, scales, method="average")
    assert result.notch == 10
    assert result.category == "IG"


def test_average_tie_rounds_toward_weaker(scales):
    """Mean 10.5 (BBB-, BB+) goes to 11 (HY) under nearest_weaker."""
    ratings = [make_rating("SP", "BBB-"), make_rating("MOODYS", "Ba1")]
    result = composite_rating(ratings, scales, method="average")
    assert result.notch == 11
    assert result.category == "HY"


def test_average_ceil_rounding_flips_on_any_fraction(scales):
    """Strict reading of the spec: any fraction goes to the weaker notch."""
    ratings = [
        make_rating("SP", "BB+"),
        make_rating("MOODYS", "Baa3"),
        make_rating("FITCH", "BBB-"),
    ]
    result = composite_rating(ratings, scales, method="average", rounding="ceil")
    assert result.notch == 11
    assert result.rounding == "ceil"


def test_average_floor_rounding_toward_stronger(scales):
    ratings = [make_rating("SP", "BBB-"), make_rating("MOODYS", "Ba1")]  # 10.5
    result = composite_rating(ratings, scales, method="average", rounding="floor")
    assert result.notch == 10


def test_average_includes_default_notch(scales):
    ratings = [make_rating("SP", "D"), make_rating("MOODYS", "Baa3")]  # 22, 10 -> 16
    result = composite_rating(ratings, scales, method="average")
    assert result.notch == 16
    assert result.category == "HY"


# ------------------------------------------------------------- filtering --- #


def test_dedupe_per_agency_keeps_latest_dated(scales):
    ratings = [
        make_rating("SP", "BBB", as_of=date(2025, 1, 1)),
        make_rating("SP", "BB+", as_of=date(2026, 1, 1)),
        make_rating("SP", "A", as_of=None),
    ]
    result = composite_rating(ratings, scales, method="middle")
    assert result.notch == 11
    assert result.n_ratings == 1
    assert result.inputs[0].rating == "BB+"


def test_dedupe_without_dates_keeps_first(scales):
    ratings = [make_rating("SP", "BBB", as_of=None), make_rating("SP", "BB+", as_of=None)]
    result = composite_rating(ratings, scales, method="middle")
    assert result.inputs[0].rating == "BBB"


def test_instrument_ratings_are_ignored(scales):
    ratings = [
        make_rating("SP", "A", scope="issuer"),
        make_rating("SP", "BB", scope="instrument"),
    ]
    result = composite_rating(ratings, scales, method="middle")
    assert result.notch == 6


def test_unverified_ratings_are_ignored(scales):
    ratings = [
        make_rating("SP", "A"),
        make_rating("MOODYS", "Caa1", verification_status="UNVERIFIED"),
    ]
    result = composite_rating(ratings, scales, method="middle")
    assert result.n_ratings == 1
    assert result.notch == 6


def test_unrated_tokens_are_ignored(scales):
    ratings = [make_rating("SP", "A"), make_rating("MOODYS", "WR")]
    result = composite_rating(ratings, scales, method="middle")
    assert result.n_ratings == 1


def test_agencies_filter_excludes_dbrs_by_default(scales):
    ratings = [
        make_rating("SP", "BBB-"),
        make_rating("MOODYS", "Baa3"),
        make_rating("DBRS", "AAA"),
    ]
    result = composite_rating(ratings, scales, method="middle", agencies=BIG_THREE)
    assert result.n_ratings == 2
    assert result.notch == 10


def test_only_unverified_returns_none(scales):
    ratings = [make_rating("SP", "A", verification_status="UNVERIFIED")]
    assert composite_rating(ratings, scales, method="middle") is None


def test_issuer_mismatch_is_rejected(scales):
    ratings = [
        make_rating("SP", "A", issuer_id="ISSUER_TEST_A"),
        make_rating("MOODYS", "A2", issuer_id="ISSUER_TEST_B"),
    ]
    with pytest.raises(ValueError):
        composite_rating(ratings, scales, method="middle")


def test_unknown_method_is_rejected(scales):
    with pytest.raises(ValueError):
        composite_rating([make_rating("SP", "A")], scales, method="worst")  # type: ignore[arg-type]
