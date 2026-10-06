"""Cache keyed on every experimental parameter; budget hard stop before any call."""

from __future__ import annotations

from pathlib import Path

import pytest

from radar.db import Database
from radar.llm.budget import BudgetExceeded, RunBudget
from radar.llm.cache import LLMCache, cache_key
from radar.llm.pricing import Pricing, load_pricing
from radar.llm.provider import ExtractionRequest, ExtractionResponse, FakeProvider


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "r.db")
    database.init_schema()
    yield database
    database.close()


BASE = dict(
    document_hash="d" * 64,
    extractor_version="llm-guidance-1.0",
    provider="openai",
    model_id="gpt-5.6-terra",
    prompt_version="1.0.0",
    schema_version="guidance-1.0",
    reasoning_effort="low",
)


def test_cache_key_changes_with_every_parameter():
    reference = cache_key(**BASE)
    for field, value in [
        ("document_hash", "e" * 64),
        ("extractor_version", "llm-guidance-1.1"),
        ("provider", "anthropic"),
        ("model_id", "gpt-5.6-sol"),
        ("prompt_version", "1.0.1"),
        ("schema_version", "guidance-1.1"),
        ("reasoning_effort", "high"),
    ]:
        assert cache_key(**{**BASE, field: value}) != reference, field
    assert cache_key(**BASE) == reference and len(reference) == 64


def test_cache_roundtrip_and_miss(db):
    cache = LLMCache(db)
    key = cache_key(**BASE)
    assert cache.get(key) is None
    response = ExtractionResponse(
        raw_json='{"has_guidance": false, "statements": []}',
        model_id="gpt-5.6-terra",
        resolved_model="gpt-5.6-terra-2026-09-01",
        provider="openai",
        input_tokens=1200,
        output_tokens=40,
        latency_ms=850,
        reasoning_effort="low",
    )
    cache.put(key, response)
    hit = cache.get(key)
    assert (
        hit is not None
        and hit.raw_json == response.raw_json
        and hit.resolved_model == "gpt-5.6-terra-2026-09-01"
    )
    assert cache.get(cache_key(**{**BASE, "reasoning_effort": "high"})) is None


# ---------------------------------------------------------------- pricing --- #


def test_pricing_config_is_versioned_and_covers_the_benchmark_models():
    pricing = load_pricing()
    assert pricing.version
    terra = pricing.models["gpt-5.6-terra"]
    assert (terra.input_per_million_usd, terra.output_per_million_usd) == (2.0, 12.0)
    sol = pricing.models["gpt-5.6-sol"]
    assert (sol.input_per_million_usd, sol.output_per_million_usd) == (4.0, 20.0)
    assert sol.note and "2026-11-21" in sol.note
    assert pricing.cost("gpt-5.6-terra", input_tokens=1_000_000, output_tokens=0) == 2.0
    assert pricing.cost(
        "gpt-5.6-terra", input_tokens=500_000, output_tokens=100_000
    ) == pytest.approx(1.0 + 1.2)


def test_unknown_model_price_is_an_error_not_zero():
    pricing = load_pricing()
    with pytest.raises(KeyError):
        pricing.cost("mystery-model", input_tokens=10, output_tokens=10)


# ----------------------------------------------------------------- budget --- #


def pricing_stub() -> Pricing:
    return Pricing.model_validate(
        {
            "version": "test",
            "models": {"m": {"input_per_million_usd": 10.0, "output_per_million_usd": 30.0}},
        }
    )


def test_budget_hard_stop_before_the_call():
    budget = RunBudget(limit_usd=1.0, pricing=pricing_stub())
    reservation = budget.reserve(
        "m", estimated_input_tokens=50_000, estimated_output_tokens=10_000
    )  # 0.5 + 0.3
    assert reservation.estimated_cost_before_call == pytest.approx(0.8)
    budget.settle(reservation, actual_input_tokens=40_000, actual_output_tokens=5_000)  # 0.4 + 0.15
    assert budget.cumulative_cost == pytest.approx(0.55) and budget.remaining == pytest.approx(0.45)
    with pytest.raises(BudgetExceeded):
        budget.reserve("m", estimated_input_tokens=50_000, estimated_output_tokens=0)  # 0.5 > 0.45
    assert budget.cumulative_cost == pytest.approx(0.55)  # a refused call costs nothing


def test_budget_ledger_records_every_figure():
    budget = RunBudget(limit_usd=10.0, pricing=pricing_stub())
    r = budget.reserve("m", estimated_input_tokens=10_000, estimated_output_tokens=1_000)
    budget.settle(r, actual_input_tokens=12_000, actual_output_tokens=800)
    entry = budget.ledger[0]
    assert entry.estimated_cost_before_call == pytest.approx(0.13)
    assert entry.actual_cost_after_call == pytest.approx(0.144)
    assert entry.cumulative_run_cost == pytest.approx(0.144)
    assert entry.budget_remaining == pytest.approx(10.0 - 0.144)
    assert entry.model_id == "m"


def test_budget_reservation_blocks_concurrent_overspend():
    budget = RunBudget(limit_usd=1.0, pricing=pricing_stub())
    r1 = budget.reserve(
        "m", estimated_input_tokens=60_000, estimated_output_tokens=0
    )  # 0.6 reserved
    with pytest.raises(BudgetExceeded):
        budget.reserve(
            "m", estimated_input_tokens=60_000, estimated_output_tokens=0
        )  # 0.6 + 0.6 > 1.0
    budget.settle(
        r1, actual_input_tokens=10_000, actual_output_tokens=0
    )  # 0.1 actual frees the rest
    budget.reserve("m", estimated_input_tokens=60_000, estimated_output_tokens=0)


def test_budget_limit_from_env(monkeypatch):
    monkeypatch.setenv("LLM_RUN_BUDGET_USD", "10.00")
    budget = RunBudget.from_env(pricing_stub())
    assert budget.limit_usd == 10.0
    monkeypatch.delenv("LLM_RUN_BUDGET_USD")
    with pytest.raises(BudgetExceeded):
        RunBudget.from_env(pricing_stub())  # no budget means no call at all


# ---------------------------------------------------------------- runner --- #


def request() -> ExtractionRequest:
    return ExtractionRequest(
        system="s",
        user="u",
        schema_name="GuidanceExtraction",
        json_schema={"type": "object"},
        model_id="m",
        temperature=0.0,
        reasoning_effort="low",
        max_output_tokens=800,
    )


def test_fake_provider_counts_calls_and_reports_usage():
    provider = FakeProvider(
        name="openai",
        raw_json='{"has_guidance": false, "statements": []}',
        resolved_model="m-2026-01-01",
    )
    response = provider.complete(request())
    assert (
        provider.calls == 1
        and response.resolved_model == "m-2026-01-01"
        and response.provider == "openai"
    )
    assert response.input_tokens > 0 and response.latency_ms >= 0


def test_runner_uses_cache_and_budget(db):
    from radar.llm.runner import run_extraction
    from radar.llm.schemas import GuidanceExtraction

    provider = FakeProvider(name="openai", raw_json='{"has_guidance": false, "statements": []}')
    budget = RunBudget(
        limit_usd=10.0,
        pricing=Pricing.model_validate(
            {
                "version": "t",
                "models": {"m": {"input_per_million_usd": 1.0, "output_per_million_usd": 1.0}},
            }
        ),
    )
    cache = LLMCache(db)
    first = run_extraction(
        db,
        request(),
        GuidanceExtraction,
        provider=provider,
        cache=cache,
        budget=budget,
        document_hash="d" * 64,
        extractor_version="llm-guidance-1.0",
        prompt_version="1.0.0",
        doc_id="x" * 64,
    )
    second = run_extraction(
        db,
        request(),
        GuidanceExtraction,
        provider=provider,
        cache=cache,
        budget=budget,
        document_hash="d" * 64,
        extractor_version="llm-guidance-1.0",
        prompt_version="1.0.0",
        doc_id="x" * 64,
    )
    assert provider.calls == 1 and first.cached is False and second.cached is True
    assert first.parsed is not None and first.parsed.has_guidance is False
    assert (
        db.count("llm_calls") == 2
        and db.audit_entries(doc_id="x" * 64)[-1]["step"] == "llm_extract"
    )
    assert db.audit_entries(doc_id="x" * 64)[-1]["model_id"] == "m"


def test_runner_hard_stop_records_refusal_without_calling(db):
    from radar.llm.runner import run_extraction
    from radar.llm.schemas import GuidanceExtraction

    provider = FakeProvider(name="openai", raw_json="{}")
    budget = RunBudget(
        limit_usd=0.0001,
        pricing=Pricing.model_validate(
            {
                "version": "t",
                "models": {"m": {"input_per_million_usd": 100.0, "output_per_million_usd": 100.0}},
            }
        ),
    )
    result = run_extraction(
        db,
        request(),
        GuidanceExtraction,
        provider=provider,
        cache=LLMCache(db),
        budget=budget,
        document_hash="d" * 64,
        extractor_version="llm-guidance-1.0",
        prompt_version="1.0.0",
        doc_id="y" * 64,
    )
    assert provider.calls == 0 and result.status == "budget_refused" and result.parsed is None
    assert db.audit_entries(doc_id="y" * 64)[-1]["status"] == "skipped"


def test_runner_schema_failure_is_recorded(db):
    from radar.llm.runner import run_extraction
    from radar.llm.schemas import GuidanceExtraction

    provider = FakeProvider(name="openai", raw_json='{"has_guidance": "maybe", "confidence": 0.9}')
    budget = RunBudget(
        limit_usd=10.0,
        pricing=Pricing.model_validate(
            {
                "version": "t",
                "models": {"m": {"input_per_million_usd": 1.0, "output_per_million_usd": 1.0}},
            }
        ),
    )
    result = run_extraction(
        db,
        request(),
        GuidanceExtraction,
        provider=provider,
        cache=LLMCache(db),
        budget=budget,
        document_hash="d" * 64,
        extractor_version="llm-guidance-1.0",
        prompt_version="1.0.0",
        doc_id="z" * 64,
    )
    assert result.status == "schema_failure" and result.parsed is None and result.error
    assert db.audit_entries(doc_id="z" * 64)[-1]["status"] == "error"
