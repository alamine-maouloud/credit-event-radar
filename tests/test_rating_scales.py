"""Rating scale tests (SPEC 9.1): every notch, every agency, boundary, normalisation."""

from __future__ import annotations

import pytest

from radar.ratings import (
    UnknownRatingError,
    category,
    from_notch,
    is_at_boundary,
    is_investment_grade,
    normalize_rating,
    notch_delta,
    to_notch,
)

SP_SCALE = [
    "AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
    "BB+", "BB", "BB-", "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D",
]  # fmt: skip
MOODYS_SCALE = [
    "Aaa", "Aa1", "Aa2", "Aa3", "A1", "A2", "A3", "Baa1", "Baa2", "Baa3",
    "Ba1", "Ba2", "Ba3", "B1", "B2", "B3", "Caa1", "Caa2", "Caa3", "Ca", "C",
]  # fmt: skip
DBRS_SCALE = [
    "AAA", "AA (high)", "AA", "AA (low)", "A (high)", "A", "A (low)",
    "BBB (high)", "BBB", "BBB (low)", "BB (high)", "BB", "BB (low)",
    "B (high)", "B", "B (low)", "CCC (high)", "CCC", "CCC (low)", "CC", "C", "D",
]  # fmt: skip


@pytest.mark.parametrize("notch,label", list(enumerate(SP_SCALE, start=1)))
def test_sp_scale_notches(scales, notch, label):
    assert to_notch("SP", label, scales) == notch
    assert from_notch("SP", notch, scales) == label


@pytest.mark.parametrize("notch,label", list(enumerate(SP_SCALE, start=1)))
def test_fitch_scale_matches_sp(scales, notch, label):
    assert to_notch("FITCH", label, scales) == notch


@pytest.mark.parametrize("notch,label", list(enumerate(MOODYS_SCALE, start=1)))
def test_moodys_scale_notches(scales, notch, label):
    assert to_notch("MOODYS", label, scales) == notch
    assert from_notch("MOODYS", notch, scales) == label


@pytest.mark.parametrize("notch,label", list(enumerate(DBRS_SCALE, start=1)))
def test_dbrs_scale_notches(scales, notch, label):
    assert to_notch("DBRS", label, scales) == notch


def test_default_aliases_share_notch_22(scales):
    assert to_notch("SP", "SD", scales) == 22
    assert to_notch("FITCH", "RD", scales) == 22
    assert to_notch("FITCH", "D", scales) == 22
    assert from_notch("SP", 22, scales) == "D"


def test_moodys_has_no_default_notch(scales):
    with pytest.raises(UnknownRatingError):
        from_notch("MOODYS", 22, scales)


@pytest.mark.parametrize(
    "sp,moodys,fitch,dbrs",
    [
        ("BBB-", "Baa3", "BBB-", "BBB (low)"),
        ("BB+", "Ba1", "BB+", "BB (high)"),
        ("A", "A2", "A", "A"),
    ],
)
def test_cross_agency_equivalence(scales, sp, moodys, fitch, dbrs):
    notches = {
        to_notch("SP", sp, scales),
        to_notch("MOODYS", moodys, scales),
        to_notch("FITCH", fitch, scales),
        to_notch("DBRS", dbrs, scales),
    }
    assert len(notches) == 1


def test_boundary(scales):
    assert scales.boundary.last_ig_notch == 10
    assert scales.boundary.first_hy_notch == 11
    assert is_investment_grade(10, scales)
    assert not is_investment_grade(11, scales)
    assert is_at_boundary(10, scales)
    assert not is_at_boundary(9, scales)
    assert not is_at_boundary(11, scales)


@pytest.mark.parametrize(
    "notch,expected",
    [(1, "IG"), (10, "IG"), (11, "HY"), (21, "HY"), (22, "DEFAULT")],
)
def test_category(scales, notch, expected):
    assert category(notch, scales) == expected


@pytest.mark.parametrize("notch", [0, 23])
def test_category_out_of_range(scales, notch):
    with pytest.raises(ValueError):
        category(notch, scales)


@pytest.mark.parametrize(
    "agency,raw,expected",
    [
        ("SP", " bbb- ", "BBB-"),
        ("MOODYS", "baa3", "Baa3"),
        ("MOODYS", "BAA3", "Baa3"),
        ("DBRS", "BBB(low)", "BBB (low)"),
        ("DBRS", "bbb (LOW)", "BBB (low)"),
        ("FITCH", "A+", "A+"),
    ],
)
def test_normalize_rating(scales, agency, raw, expected):
    assert normalize_rating(agency, raw, scales) == expected


@pytest.mark.parametrize(
    "agency,raw", [("SP", "BBB--"), ("MOODYS", "BBB-"), ("SP", ""), ("SP", "Aaa")]
)
def test_unknown_rating_is_rejected(scales, agency, raw):
    with pytest.raises(UnknownRatingError):
        to_notch(agency, raw, scales)


def test_unknown_agency_is_rejected(scales):
    with pytest.raises(UnknownRatingError):
        to_notch("EGAN_JONES", "A", scales)


@pytest.mark.parametrize("token", ["NR", "WD", "nr", " wd "])
def test_unrated_tokens(scales, token):
    assert scales.is_unrated(token)
    with pytest.raises(UnknownRatingError):
        to_notch("SP", token, scales)


def test_notch_delta_positive_means_downgrade():
    assert notch_delta(old_notch=10, new_notch=11) == 1
    assert notch_delta(old_notch=10, new_notch=12) == 2
    assert notch_delta(old_notch=11, new_notch=10) == -1
    assert notch_delta(old_notch=9, new_notch=9) == 0


def test_agency_resolution_from_display_names(scales):
    assert scales.resolve_agency("S&P Global") == "SP"
    assert scales.resolve_agency("Moody's") == "MOODYS"
    assert scales.resolve_agency("Fitch Ratings") == "FITCH"
    assert scales.resolve_agency("Morningstar DBRS") == "DBRS"
    assert scales.resolve_agency("SP") == "SP"
    assert scales.resolve_agency("Egan-Jones") is None
