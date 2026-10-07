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
    assert {i.id for i in unverified} == {
        "GM_FINANCIAL",
        "HARLEY_DAVIDSON_FS",
        "ROQUETTE",
        "HARLEY_DAVIDSON_INC",
        "GOPRO_INC",
        "MICROVAST_HOLDINGS",
        "CHICAGO_RIVET_MACHINE",
        "NEW_FORTRESS_ENERGY",
        "SLEEP_NUMBER",
        "CATERPILLAR",
        "DEERE",
        "FORD_MOTOR",
        "COMPASS_DIVERSIFIED",
        "HYDROFARM",
        "PACCAR",
        "CUMMINS",
        "GENERAL_MOTORS",
        "BOXLIGHT",
        "AMERICAN_SHARED_HOSPITAL_SERVICES",
        "FTC_SOLAR",
        "HONEYWELL",
        "EMERSON",
    }
    assert all(i.notes for i in unverified)


def test_historical_control_issuer_is_not_in_the_demo_watchlist(universe):
    hog = universe.by_id("HARLEY_DAVIDSON_INC")
    assert "historical_control" in hog.tags and "demo_watchlist" not in hog.tags
    assert hog.sec_cik == "0000793952"
    assert hog.id != universe.by_id("HARLEY_DAVIDSON_FS").id


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
    assert rules.version == "1.5"
    assert any("1.4" in entry for entry in rules.changelog)
    assert all(r.definition and r.direction for r in rules.rules)
    assert all(m.definition for m in rules.modifiers)


def test_rules_rating_state_parameters(rules):
    rs = rules.rating_state
    assert rs.admissible_agencies == ["SP", "MOODYS", "FITCH"]
    assert rs.eligible_rating_types == ["long_term_issuer", "long_term_issuer_default"]
    assert rs.max_rating_age_days == 400
    assert rs.require_complete_date is True and rs.allow_future_observation is False
    assert rs.stale_policy == "exclude"


def test_rule_directions(rules):
    negative = {r.id for r in rules.rules if r.direction == "negative"}
    assert negative == {
        "RAT-01",
        "RAT-02",
        "RAT-03",
        "RAT-04",
        "RAT-05",
        "RAT-06",
        "RAT-07",
        "ERN-01",
        "ERN-02",
        "ERN-03",
        "ISS-03",
        "EDG-01",
    }
    assert {r.id for r in rules.rules if r.direction == "positive"} == {"RAT-08", "RAT-09"}
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


def test_no_rule_depends_on_the_composite(rules):
    """ADR-001: agency-level ratings are authoritative; the composite never drives a rule."""
    for item in [*rules.rules, *rules.modifiers]:
        assert "composite" not in item.condition.lower(), item.id
        assert "composite" not in item.description.lower(), item.id
    assert rules.by_id("RAT-01").condition == "agency_rating_crosses_ig_to_hy"
    assert rules.by_id("RAT-08").condition == "agency_rating_crosses_hy_to_ig"
    assert "weakest_agency" in rules.by_id("MOD-01").condition


# ----------------------------------------------------------------- seed --- #


def test_seed_columns():
    header = (SEEDS_DIR / "ratings_seed.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == SEED_COLUMNS


def test_seed_loads_and_every_row_has_a_verification_level(seed, universe):
    assert len(seed) == 28
    assert all(r.verified_at_least("SOURCE_VERIFIED") for r in seed)
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


def test_seed_unverified_issuers_have_no_rows(seed, universe):
    unverified = {i.id for i in universe.issuers if i.rating_status == "unverified"}
    assert not [r for r in seed if r.issuer_id in unverified]


def test_seed_instrument_rows_have_no_outlook(seed):
    assert all(r.outlook is None for r in seed if r.scope == "instrument")


def test_seed_no_row_is_golden_without_sign_off(seed):
    """GOLDEN is a human decision (ADR-003); the validation pass stops at DATE_VERIFIED."""
    assert not [r for r in seed if r.verification_status == "GOLDEN"]


def test_seed_rating_types_are_explicit(seed):
    intesa = [r for r in seed if r.issuer_id == "INTESA_SANPAOLO"]
    assert intesa and all(r.rating_type == "senior_preferred" for r in intesa)
    assert not any(r.composite_eligible for r in intesa)
    omv_moodys = next(r for r in seed if r.issuer_id == "OMV" and r.agency == "MOODYS")
    assert omv_moodys.rating_type == "senior_unsecured" and not omv_moodys.composite_eligible
    dassault_bond = next(r for r in seed if r.scope == "instrument")
    assert dassault_bond.rating_type == "instrument"
    assert not [r for r in seed if r.issuer_id == "ROQUETTE"]


def test_seed_composites_require_golden_by_default(seed, scales, settings):
    """Until rows are promoted to GOLDEN, the composite returns nothing for every issuer."""
    for issuer_id in {r.issuer_id for r in seed}:
        rows = [r for r in seed if r.issuer_id == issuer_id]
        golden = [r for r in rows if r.verification_status == "GOLDEN"]
        result = composite_rating(rows, scales, agencies=settings.ratings.composite_agencies)
        assert (result is None) == (not [r for r in golden if r.composite_eligible]), issuer_id


def test_seed_composites_structural_check(seed, scales, settings):
    """Structural check with the ladder lowered: eligibility and arithmetic only."""

    def comp(issuer_id):
        rows = [r for r in seed if r.issuer_id == issuer_id]
        return composite_rating(
            rows,
            scales,
            method=settings.ratings.composite_method,
            agencies=settings.ratings.composite_agencies,
            min_verification="SOURCE_VERIFIED",
        )

    var = comp("VAR_ENERGI")  # Baa3, BBB, BBB -> median BBB
    assert var is not None and var.label == "BBB"
    moeve = comp("MOEVE")  # Baa3, BBB- -> boundary
    assert moeve is not None and moeve.notch == scales.boundary.last_ig_notch
    vw = comp("VOLKSWAGEN")  # A-, Baa1, BBB+ with DBRS excluded -> median BBB+
    assert vw is not None and vw.n_ratings == 3 and vw.label == "BBB+"
    dassault = comp("DASSAULT_SYSTEMES")  # instrument row ignored
    assert dassault is not None and dassault.n_ratings == 1
    assert comp("INTESA_SANPAOLO") is None  # senior preferred only: no issuer rating yet
    omv = comp("OMV")  # Moody's row is senior unsecured: only the Fitch IDR counts
    assert omv is not None and omv.n_ratings == 1 and omv.inputs[0].agency == "FITCH"


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


# ------------------------------------------------------------ ir_sources --- #


def test_ir_sources_configured_for_the_three_pilot_issuers(universe):
    kinds = {i.id: [s.kind for s in i.ir_sources] for i in universe.issuers if i.ir_sources}
    assert kinds == {
        "VOLKSWAGEN": ["rss", "page", "page_links"],
        "TRATON": ["rss"],
        "OMV": ["sitemap", "page_links", "page"],
    }
    vw = universe.by_id("VOLKSWAGEN")
    assert vw.ir_sources[1].table_profile == "current_by_agency"
    assert vw.ir_sources[2].content == "pdf" and vw.ir_sources[2].max_items == 20
    omv = universe.by_id("OMV")
    assert omv.ir_sources[0].path_prefix == "/en/investors/news-and-events/news/"
    assert omv.ir_sources[2].table_profile == "dated_by_agency"
    assert universe.by_id("TRATON").ir_sources[0].include_categories == ["Press releases"]


@pytest.mark.parametrize(
    "fields,message",
    [
        (dict(kind="sitemap"), "path_prefix"),
        (dict(kind="page_links"), "link_pattern"),
        (dict(kind="rss", table_profile="current_by_agency"), "table_profile"),
        (dict(kind="page_links", link_pattern="(unclosed"), "unterminated|missing"),
        (dict(kind="rss", id="Bad Id"), "pattern"),
    ],
)
def test_ir_source_validation(fields, message):
    from pydantic import ValidationError

    from radar.config import IRSource

    base = dict(
        id="src_a", kind="rss", url="https://example.invalid/feed", document_type="press_release"
    )
    base.update(fields)
    with pytest.raises((ValidationError, ValueError), match=message):
        IRSource(**base)


def test_duplicate_ir_source_ids_across_issuers_rejected(tmp_path: Path):
    text = (CONFIG_DIR / "universe.yaml").read_text(encoding="utf-8")
    broken = text.replace("id: omv_debt_ratings_page", "id: vw_press_rss")
    p = tmp_path / "universe.yaml"
    p.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate ir_sources"):
        load_universe(p)


def test_ir_settings(settings):
    ir = settings.ingestion.ir
    assert ir.user_agent_env == "IR_USER_AGENT" and ir.respect_robots is True
    assert ir.min_interval_seconds >= 0.5


# ------------------------------------------------------------- llm --- #


def test_llm_settings_name_the_benchmark_models(settings):
    roles = settings.llm.roles
    assert roles["extraction"].provider == "openai" and roles["extraction"].model == "gpt-5.6-terra"
    assert roles["extraction"].reasoning_effort == "low"
    assert settings.llm.benchmark_alternatives["openai_sol"]["extraction"].model == "gpt-5.6-sol"
    assert settings.llm.benchmark_alternatives["anthropic"]["extraction"].model == "TO_CONFIRM"
    assert settings.llm.run_budget_usd_env == "LLM_RUN_BUDGET_USD"
    assert settings.llm.pricing_file == "config/llm_pricing.yaml"


def test_env_example_declares_the_run_budget():
    text = (CONFIG_DIR.parent / ".env.example").read_text(encoding="utf-8")
    assert "LLM_RUN_BUDGET_USD=10.00" in text


def test_issuer_segments_name_the_levels_that_never_carry_guidance(universe):
    by_id = {i.id: i for i in universe.issuers}
    assert by_id["OMV"].segments == ["Chemicals", "Fuels & Feedstock", "Fuels", "Energy"]
    assert by_id["VOLKSWAGEN"].principal_division == "Automotive Division"
    assert by_id["TRATON"].principal_division == "TRATON Operations"
    assert "TRATON Operations" not in by_id["TRATON"].segments


def test_stress_case_issuers_are_historical_controls_outside_the_watchlist(universe):
    for issuer_id in ("GOPRO_INC", "MICROVAST_HOLDINGS", "CHICAGO_RIVET_MACHINE"):
        issuer = universe.by_id(issuer_id)
        assert "historical_control" in issuer.tags and "demo_watchlist" not in issuer.tags
        assert issuer.rating_status == "unverified" and issuer.ir_sources == []


def test_demo_settings_are_presentation_only(settings):
    """Featured cases are a presentation list: every id is a universe issuer, no rule or
    decision reads it, and General Motors (a known false flag) is not featured."""
    universe = load_universe(CONFIG_DIR / "universe.yaml")
    ids = {i.id for i in universe.issuers}
    assert settings.demo.featured_issuers and set(settings.demo.featured_issuers) <= ids
    assert "GENERAL_MOTORS" not in settings.demo.featured_issuers
    assert settings.demo.generation_budget_env == "DEMO_GENERATION_BUDGET_USD"
