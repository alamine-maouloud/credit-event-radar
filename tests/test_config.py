"""Configuration and seed loading tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from radar.config import (
    CONFIG_DIR,
    SEED_COLUMNS,
    SEEDS_DIR,
    load_ratings_seed,
    load_rules,
    load_settings,
    load_universe,
    parse_outlook,
    parse_partial_date,
)
from radar.ratings import composite_rating

SPEC_RULE_IDS = {
    "RAT-01": "P1", "RAT-02": "P1", "RAT-03": "P1", "RAT-04": "P1", "RAT-05": "P2",
    "RAT-06": "P2", "RAT-07": "P2", "RAT-08": "P2", "RAT-09": "P3", "RAT-10": "P3",
    "ERN-01": "P1", "ERN-02": "P1", "ERN-03": "P2", "ERN-04": "P3",
    "ISS-01": "P2", "ISS-02": "P2", "ISS-03": "P1", "ISS-04": "P3",
    "EDG-01": "P1",
}  # fmt: skip


@pytest.fixture(scope="module")
def settings():
    return load_settings(CONFIG_DIR / "settings.yaml")


@pytest.fixture(scope="module")
def universe():
    return load_universe(CONFIG_DIR / "universe.yaml")


@pytest.fixture(scope="module")
def rules():
    return load_rules(CONFIG_DIR / "rules.yaml")


@pytest.fixture(scope="module")
def seed(scales, universe):
    return load_ratings_seed(SEEDS_DIR / "ratings_seed.csv", scales, universe)


# ------------------------------------------------------------- settings --- #


def test_settings_load(settings):
    assert settings.ratings.composite_method == "middle"
    assert settings.ratings.composite_rounding == "nearest_weaker"
    assert settings.ratings.composite_agencies == ["SP", "MOODYS", "FITCH"]
    assert all(role.temperature == 0 for role in settings.llm.roles.values())
    assert settings.ingestion.sec.max_requests_per_second < 10


def test_settings_local_channel_everywhere(settings):
    for route in settings.alerts.routing.values():
        assert "local" in route.channels
    assert settings.alerts.routing["P1"].mode == "immediate"


# ------------------------------------------------------------- universe --- #


def test_universe_load(universe):
    assert universe.name == "Demo watchlist"
    assert len(universe.issuers) >= 13
    assert "demo_watchlist" in universe.by_id("VOLKSWAGEN").tags


def test_universe_unverified_issuers_have_notes(universe):
    unverified = [i for i in universe.issuers if i.rating_status == "unverified"]
    assert {i.id for i in unverified} == {"GM_FINANCIAL", "HARLEY_DAVIDSON_FS"}
    assert all(i.notes for i in unverified)


def test_universe_has_boundary_issuers(universe):
    assert len([i for i in universe.issuers if "ig_hy_boundary" in i.tags]) >= 2


def test_universe_rejects_duplicate_alias(tmp_path: Path, universe):
    text = (CONFIG_DIR / "universe.yaml").read_text(encoding="utf-8")
    broken = text.replace(
        'aliases: ["TRATON SE", "Traton Group"]', 'aliases: ["VW", "Traton Group"]'
    )
    p = tmp_path / "universe.yaml"
    p.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError):
        load_universe(p)


# ---------------------------------------------------------------- rules --- #


def test_rules_match_spec_matrix(rules):
    assert rules.version == "1.0"
    assert {r.id: r.priority for r in rules.rules} == SPEC_RULE_IDS
    assert [m.id for m in rules.modifiers] == ["MOD-01", "MOD-02", "MOD-03"]


def test_rules_thresholds(rules):
    assert rules.thresholds.guidance_cut_p1_pct == 10
    assert rules.thresholds.issuance_p2_eur == 1_000_000_000
    assert rules.thresholds.leverage_threshold is None
    assert rules.thresholds.mod02_window_days == 30


def test_rules_conditions_unique(rules):
    conditions = [r.condition for r in rules.rules] + [m.condition for m in rules.modifiers]
    assert len(conditions) == len(set(conditions))


def test_mod03_requires_sourced_data(rules):
    assert rules.by_id("MOD-03").requires_sourced_data is True


# ----------------------------------------------------------------- seed --- #


def test_seed_columns():
    header = (SEEDS_DIR / "ratings_seed.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == SEED_COLUMNS


def test_seed_loads_and_is_fully_verified(seed, universe):
    assert len(seed) == 30
    assert all(r.verification_status == "VERIFIED" for r in seed)
    assert {r.issuer_id for r in seed} <= universe.ids
    assert all(str(r.source_url).startswith("https://") for r in seed)


def test_seed_ratings_are_canonical_labels(seed, scales):
    for r in seed:
        assert r.rating in scales.agency(r.agency).scale


def test_seed_partial_dates_are_not_completed(seed):
    vw_moodys = next(r for r in seed if r.issuer_id == "VOLKSWAGEN" and r.agency == "MOODYS")
    assert vw_moodys.as_of is None
    assert vw_moodys.as_of_raw == "2025-03"
    traton_sp = next(r for r in seed if r.issuer_id == "TRATON" and r.agency == "SP")
    assert str(traton_sp.as_of) == "2026-07-30"


def test_seed_unverified_issuers_have_no_rows(seed):
    assert not [r for r in seed if r.issuer_id in {"GM_FINANCIAL", "HARLEY_DAVIDSON_FS"}]


def test_seed_composites(seed, scales, settings):
    def comp(issuer_id):
        rows = [r for r in seed if r.issuer_id == issuer_id]
        return composite_rating(
            rows,
            scales,
            method=settings.ratings.composite_method,
            agencies=settings.ratings.composite_agencies,
        )

    var = comp("VAR_ENERGI")  # Baa3, BBB, BBB -> median BBB
    assert var is not None and var.label == "BBB"
    moeve = comp("MOEVE")  # Baa3, BBB- -> boundary
    assert moeve is not None and moeve.notch == scales.boundary.last_ig_notch
    vw = comp("VOLKSWAGEN")  # A-, Baa1, BBB+ with DBRS excluded -> median BBB+
    assert vw is not None and vw.n_ratings == 3 and vw.label == "BBB+"
    dassault = comp("DASSAULT_SYSTEMES")  # instrument row ignored
    assert dassault is not None and dassault.n_ratings == 1


def test_seed_rejects_unknown_agency(tmp_path: Path, scales, universe):
    text = (SEEDS_DIR / "ratings_seed.csv").read_text(encoding="utf-8")
    p = tmp_path / "seed.csv"
    p.write_text(text.replace("Morningstar DBRS", "Egan-Jones", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown agency"):
        load_ratings_seed(p, scales, universe)


def test_seed_rejects_unknown_rating(tmp_path: Path, scales, universe):
    text = (SEEDS_DIR / "ratings_seed.csv").read_text(encoding="utf-8")
    p = tmp_path / "seed.csv"
    p.write_text(text.replace(",Baa1,", ",BBB+,", 1), encoding="utf-8")
    with pytest.raises(ValueError):
        load_ratings_seed(p, scales, universe)


# -------------------------------------------------------------- parsers --- #


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Stable", ("stable", "none")),
        ("negative", ("negative", "none")),
        ("", (None, "none")),
        (None, (None, "none")),
        ("CreditWatch Negative", (None, "negative")),
        ("Review for downgrade", (None, "negative")),
        ("Watch Positive", (None, "positive")),
    ],
)
def test_parse_outlook(text, expected):
    assert parse_outlook(text) == expected


def test_parse_outlook_rejects_unknown():
    with pytest.raises(ValueError):
        parse_outlook("Cloudy")


@pytest.mark.parametrize(
    "text,as_of,raw",
    [("2026-07-30", "2026-07-30", "2026-07-30"), ("2025-03", None, "2025-03"), ("", None, None)],
)
def test_parse_partial_date(text, as_of, raw):
    d, r = parse_partial_date(text)
    assert (str(d) if d else None) == as_of
    assert r == raw


def test_parse_partial_date_rejects_garbage():
    with pytest.raises(ValueError):
        parse_partial_date("March 2025")
