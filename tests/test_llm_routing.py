"""Model routing per extraction kind (settings.llm.routing): one default and one challenger
per family, chosen on the benchmarks and named with their reason; the audit records why a
model was used, not only its name."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from radar.config import CONFIG_DIR, LLMSettings, load_settings
from radar.llm.routing import RoutingNotBenchmarked, select_model


@pytest.fixture(scope="module")
def settings():
    return load_settings(CONFIG_DIR / "settings.yaml")


def test_routing_names_a_default_and_a_challenger_per_benchmarked_family(settings):
    routing = settings.llm.routing
    assert set(routing) == {"guidance", "liquidity", "covenant", "going_concern"}
    assert (routing["guidance"].default, routing["guidance"].challenger) == (
        "openai_terra",
        "openai_sol",
    )
    assert (routing["liquidity"].default, routing["liquidity"].challenger) == (
        "openai_terra",
        "openai_sol",
    )
    assert (routing["covenant"].default, routing["covenant"].challenger) == (
        "openai_sol",
        "openai_terra",
    )
    # going concern (Phase 3.4c, 2026-10-07): Terra alone, Sol deliberately not benchmarked
    assert (routing["going_concern"].default, routing["going_concern"].challenger) == (
        "openai_terra",
        None,
    )
    assert (
        "7/7" in routing["going_concern"].reason
        and "not benchmarked" in routing["going_concern"].reason
    )
    for route in routing.values():
        assert route.reason and "2026-10-0" in route.reason
    # the covenant conclusion stays cautious: a holdout result, not a general claim
    assert "holdout" in routing["covenant"].reason and "always" not in routing["covenant"].reason


def test_default_and_challenger_resolve_to_exact_model_ids(settings):
    chosen = select_model(settings.llm, "covenant")
    assert chosen.kind == "covenant" and chosen.role == "default" and chosen.name == "openai_sol"
    assert chosen.model.model == "gpt-5.6-sol" and chosen.model.provider == "openai"
    assert chosen.reason == settings.llm.routing["covenant"].reason
    challenger = select_model(settings.llm, "covenant", challenger=True)
    assert challenger.role == "challenger" and challenger.model.model == "gpt-5.6-terra"
    assert select_model(settings.llm, "liquidity").model.model == "gpt-5.6-terra"


def test_an_explicit_alternative_overrides_the_route_and_says_so(settings):
    chosen = select_model(settings.llm, "guidance", alternative="openai_sol")
    assert chosen.role == "explicit" and chosen.model.model == "gpt-5.6-sol"
    assert "openai_sol" in chosen.reason and "command line" in chosen.reason
    with pytest.raises(KeyError):
        select_model(settings.llm, "guidance", alternative="nope")


def _unbenchmarked(settings) -> LLMSettings:
    """The settings with one family still TO_BENCHMARK, as going concern was before its run."""
    data = settings.llm.model_dump()
    data["routing"]["new_family"] = {
        "default": "TO_BENCHMARK",
        "challenger": None,
        "reason": "no benchmark yet",
    }
    return LLMSettings.model_validate(data)


def test_a_family_without_a_benchmark_refuses_the_default(settings):
    llm = _unbenchmarked(settings)
    with pytest.raises(RoutingNotBenchmarked) as exc:
        select_model(llm, "new_family")
    assert "new_family" in str(exc.value) and "--alternative" in str(exc.value)
    with pytest.raises(RoutingNotBenchmarked):
        select_model(llm, "new_family", challenger=True)
    explicit = select_model(llm, "new_family", alternative="openai_terra")
    assert explicit.role == "explicit" and explicit.model.model == "gpt-5.6-terra"
    # a family with a default and no challenger refuses the challenger only
    assert select_model(settings.llm, "going_concern").model.model == "gpt-5.6-terra"
    with pytest.raises(RoutingNotBenchmarked):
        select_model(settings.llm, "going_concern", challenger=True)


def test_the_selection_is_a_plain_record_for_the_audit(settings):
    record = select_model(settings.llm, "covenant").as_record()
    assert set(record) == {"kind", "role", "name", "model_id", "provider", "reason"}
    assert record["model_id"] == "gpt-5.6-sol" and record["role"] == "default"


def test_a_route_must_name_a_configured_alternative(settings):
    data = settings.llm.model_dump()
    data["routing"]["covenant"]["default"] = "openai_unknown"
    with pytest.raises(ValidationError, match="openai_unknown"):
        LLMSettings.model_validate(data)
    data = settings.llm.model_dump()
    data["routing"]["covenant"]["reason"] = ""
    with pytest.raises(ValidationError):
        LLMSettings.model_validate(data)


def test_the_cli_refuses_an_unbenchmarked_family_before_any_call(tmp_path, settings, monkeypatch):
    from typer.testing import CliRunner

    from radar import cli

    unbenchmarked = settings.model_copy(update={"llm": _unbenchmarked(settings)})
    monkeypatch.setattr(cli, "_settings", lambda: unbenchmarked)
    result = CliRunner().invoke(
        cli.app, ["llm-extract", "--events", "--kind", "new_family", "--db", str(tmp_path / "r.db")]
    )
    assert result.exit_code != 0
    assert "new_family" in result.output and "--alternative" in result.output
