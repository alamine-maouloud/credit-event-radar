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
    assert routing["going_concern"].default == "TO_BENCHMARK"
    assert routing["going_concern"].challenger is None
    for route in routing.values():
        assert route.reason and "2026-10-06" in route.reason or route.default == "TO_BENCHMARK"
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


def test_a_family_without_a_benchmark_refuses_the_default(settings):
    with pytest.raises(RoutingNotBenchmarked) as exc:
        select_model(settings.llm, "going_concern")
    assert "going_concern" in str(exc.value) and "--alternative" in str(exc.value)
    with pytest.raises(RoutingNotBenchmarked):
        select_model(settings.llm, "going_concern", challenger=True)
    explicit = select_model(settings.llm, "going_concern", alternative="openai_terra")
    assert explicit.role == "explicit" and explicit.model.model == "gpt-5.6-terra"


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


def test_the_cli_refuses_an_unbenchmarked_family_before_any_call(tmp_path):
    from typer.testing import CliRunner

    from radar.cli import app

    result = CliRunner().invoke(
        app, ["llm-extract", "--events", "--kind", "going_concern", "--db", str(tmp_path / "r.db")]
    )
    assert result.exit_code != 0
    assert "going_concern" in result.output and "--alternative" in result.output
