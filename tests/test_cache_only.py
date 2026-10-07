"""A cache-only replay reports CACHE_MISS before any budget or provider logic."""

from __future__ import annotations

import json
from pathlib import Path

from radar.db import Database
from radar.llm.budget import RunBudget
from radar.llm.cache import LLMCache
from radar.llm.pricing import Pricing
from radar.llm.provider import ExtractionRequest, FakeProvider, RefusingProvider
from radar.llm.runner import run_extraction
from radar.llm.schemas import GuidanceExtraction, guidance_json_schema


def _request() -> ExtractionRequest:
    return ExtractionRequest(
        system="s", user="u", schema_name="GuidanceExtraction", json_schema=guidance_json_schema(),
        model_id="gpt-5.6-terra", temperature=0.0, reasoning_effort="low", max_output_tokens=64,
    )  # fmt: skip


def _pricing() -> Pricing:
    return Pricing.model_validate(
        {
            "version": "t",
            "models": {
                "gpt-5.6-terra": {"input_per_million_usd": 2.0, "output_per_million_usd": 12.0}
            },
        }
    )


def _run(db: Database, provider, *, cache_only: bool, limit: float = 1.0):
    return run_extraction(
        db, _request(), GuidanceExtraction, provider=provider, cache=LLMCache(db),
        budget=RunBudget(limit_usd=limit, pricing=_pricing()), document_hash="d" * 64,
        extractor_version="llm-guidance-1.0", prompt_version="1.1.0", doc_id="doc", cache_only=cache_only,
    )  # fmt: skip


def test_cache_only_reports_a_cache_miss_without_budget_or_provider(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    db.init_schema()
    run = _run(db, RefusingProvider(name="openai"), cache_only=True, limit=1e-9)
    assert run.status == "cache_miss" and run.cached is False and run.parsed is None
    assert run.error.startswith("CACHE_MISS") and run.cost_usd is None and run.llm_call_id is None
    assert db.count("llm_calls") == 0
    entries = [e for e in db.audit_entries() if e["step"] == "llm_extract"]
    assert entries and entries[-1]["status"] == "skipped" and "CACHE_MISS" in entries[-1]["message"]
    db.close()


def test_cache_only_replays_a_cached_answer_at_zero_cost(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    db.init_schema()
    raw = json.dumps({"has_guidance": False, "statements": []})
    first = _run(db, FakeProvider(name="openai", raw_json=raw), cache_only=False)
    assert first.status == "ok" and first.cost_usd and first.cost_usd > 0
    replay = _run(db, RefusingProvider(name="openai"), cache_only=True, limit=1e-9)
    assert replay.status == "cached" and replay.cost_usd == 0.0 and replay.parsed is not None
    db.close()
