"""Benchmark of the guidance extraction against the frozen gold set: one run writes
outputs.jsonl and run.json, the scorer turns them into metrics. Everything offline."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from radar.config import Issuer, Universe, load_universe
from radar.db import Database
from radar.eval.benchmark import BenchmarkConfig, run_benchmark
from radar.eval.gold import GoldDocument, GoldMismatch, load_gold, load_gold_document
from radar.eval.metrics import predicted_behaviour, score
from radar.llm.budget import RunBudget
from radar.llm.cache import LLMCache
from radar.llm.pricing import load_pricing
from radar.llm.prompts import load_prompt
from radar.llm.provider import ExtractionRequest, ExtractionResponse, LLMProvider, estimate_tokens
from tests.llm_helpers import FULL_QUOTE, VW_LIKE, doc

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "prompts" / "extraction" / "guidance.v1.yaml"
NO_GUIDANCE_TEXT = (
    "Issuer Test A AG delivers 2.1 million vehicles in the first half\n"
    "Deliveries rose by 3 percent. The order book remains well filled."
)


def _gold_rows() -> list[dict]:
    quote_start = VW_LIKE.index(FULL_QUOTE)
    return [
        {
            "gold_id": "G-T-01",
            "issuer_id": "ISSUER_TEST_A",
            "fixture": "tests/fixtures/none/a",
            "source_url": "https://example.invalid/a",
            "document_date": "2026-04-30",
            "raw_sha256": "a" * 64,
            "normalized_sha256": doc(VW_LIKE).doc_id,
            "normalizer_version": "t",
            "behaviour": "guidance_changed",
            "occurrences": [
                {
                    "metric": "margin",
                    "metric_label": "operating return on sales",
                    "scope": "group",
                    "basis": "margin_pct",
                    "unit": "PCT",
                    "previous_lower": 5.5,
                    "previous_upper": 6.5,
                    "current_lower": 4.0,
                    "current_upper": 5.5,
                    "period": "2026",
                    "status": "cut",
                    "change_basis": "quantitative",
                    "evidence_quote": FULL_QUOTE,
                    "start_offset": quote_start,
                    "end_offset": quote_start + len(FULL_QUOTE),
                },
                {
                    "metric": "revenue",
                    "metric_label": "sales revenue",
                    "scope": "group",
                    "basis": "yoy_change_pct",
                    "unit": "PCT",
                    "previous_lower": None,
                    "previous_upper": None,
                    "current_lower": 0,
                    "current_upper": 3,
                    "period": "2026",
                    "status": "new",
                    "change_basis": "none",
                    "evidence_quote": "The Issuer Test A Group expects sales revenue in 2026 to develop within a range of 0 and +3 percent compared with the previous year.",
                    "start_offset": 0,
                    "end_offset": 10,
                },
            ],
            "notes": None,
        },
        {
            "gold_id": "G-T-02",
            "issuer_id": "ISSUER_TEST_A",
            "fixture": "tests/fixtures/none/b",
            "source_url": "https://example.invalid/b",
            "document_date": "2026-07-10",
            "raw_sha256": "b" * 64,
            "normalized_sha256": doc(NO_GUIDANCE_TEXT).doc_id,
            "normalizer_version": "t",
            "behaviour": "no_guidance",
            "occurrences": [],
            "notes": None,
        },
    ]


@pytest.fixture
def gold_path(tmp_path: Path) -> Path:
    p = tmp_path / "gold.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in _gold_rows()) + "\n", encoding="utf-8")
    return p


TEXTS = {"G-T-01": VW_LIKE, "G-T-02": NO_GUIDANCE_TEXT}
UNIVERSE = Universe(
    name="t",
    disclosure="fictional",
    retrieved_as_of=date(2026, 1, 1),
    issuers=[
        Issuer(
            id="ISSUER_TEST_A",
            name="Issuer Test A",
            legal_entity="Issuer Test A AG",
            aliases=["ITA"],
            sector="t",
            country="DE",
        )
    ],
)


def _loader(row: GoldDocument, universe, root):
    return doc(TEXTS[row.gold_id], published=row.document_date)


class ScriptedProvider(LLMProvider):
    """Answers per document: one valid cut statement plus one unsupported statement for the
    results release, nothing for the deliveries release."""

    name = "openai"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: ExtractionRequest) -> ExtractionResponse:
        self.calls += 1
        if "Deliveries rose" in request.user:
            payload = {"has_guidance": False, "statements": []}
        else:
            start = VW_LIKE.index(FULL_QUOTE)
            payload = {
                "has_guidance": True,
                "statements": [
                    {
                        "metric": "margin",
                        "metric_label": "operating return on sales",
                        "basis": "margin_pct",
                        "unit": "PCT",
                        "previous_lower": 5.5,
                        "previous_upper": 6.5,
                        "current_lower": 4.0,
                        "current_upper": 5.5,
                        "period": "2026",
                        "status": "cut",
                        "direction_claimed": "down",
                        "evidence_quote": FULL_QUOTE,
                        "start_offset": start,
                        "end_offset": start + len(FULL_QUOTE),
                    },
                    {
                        "metric": "ebitda",
                        "metric_label": "EBITDA",
                        "basis": "absolute",
                        "unit": "EUR_BN",
                        "previous_lower": None,
                        "previous_upper": None,
                        "current_lower": 9.0,
                        "current_upper": 9.0,
                        "period": "2026",
                        "status": "new",
                        "direction_claimed": None,
                        "evidence_quote": "EBITDA is expected to reach EUR 9 billion.",
                        "start_offset": 0,
                        "end_offset": 41,
                    },
                ],
            }
        raw = json.dumps(payload)
        return ExtractionResponse(
            raw_json=raw,
            model_id=request.model_id,
            resolved_model=request.model_id + "-2026-09-01",
            provider=self.name,
            input_tokens=request.estimated_input_tokens,
            output_tokens=estimate_tokens(raw),
            latency_ms=42,
            reasoning_effort=request.reasoning_effort,
        )


def _config(**overrides) -> BenchmarkConfig:
    base = dict(
        model_id="gpt-5.6-terra", reasoning_effort="low", temperature=0.0, max_output_tokens=32768
    )
    base.update(overrides)
    return BenchmarkConfig(**base)


def _run(tmp_path: Path, gold_path: Path, *, budget_usd: float = 5.0, dry_run: bool = False):
    db = Database(tmp_path / "r.db")
    db.init_schema()
    provider = ScriptedProvider()
    budget = RunBudget(limit_usd=budget_usd, pricing=load_pricing())
    out = tmp_path / "run"
    summary = run_benchmark(
        load_gold(gold_path),
        db=db,
        universe=UNIVERSE,
        provider=provider,
        cache=LLMCache(db),
        budget=budget,
        prompt=load_prompt(PROMPT),
        config=_config(),
        out_dir=out,
        load_document=_loader,
        dry_run=dry_run,
    )
    return summary, provider, out, db


def test_run_writes_outputs_and_run_json(tmp_path, gold_path):
    summary, provider, out, db = _run(tmp_path, gold_path)
    rows = [json.loads(line) for line in (out / "outputs.jsonl").read_text().splitlines()]
    run = json.loads((out / "run.json").read_text())
    assert provider.calls == 2 and [r["gold_id"] for r in rows] == ["G-T-01", "G-T-02"]
    assert [r["run_status"] for r in rows] == ["ok", "ok"]
    first = rows[0]
    assert first["has_guidance"] is True and len(first["statements"]) == 2
    valid, invalid = first["statements"]
    assert (
        valid["validation"]["status"] == "VALID"
        and valid["change"]["change_basis"] == "quantitative"
    )
    assert valid["change"]["direction"] == "down" and valid["change"]["midpoint_previous"] == 6.0
    assert invalid["validation"]["status"] == "INVALID"
    assert first["cost_usd"] > 0 and first["resolved_model"] == "gpt-5.6-terra-2026-09-01"
    assert (
        run["model_id"] == "gpt-5.6-terra" and run["prompt_version"] == load_prompt(PROMPT).version
    )
    assert run["n_documents"] == 2 and run["statuses"] == {"ok": 2}
    assert run["total_cost_usd"] == pytest.approx(sum(r["cost_usd"] for r in rows))
    assert run["budget_limit_usd"] == 5.0 and run["schema_version"] == "guidance-1.0"
    assert summary.total_cost_usd == run["total_cost_usd"]
    assert db.count("llm_calls") == 2
    # the scorer ran as part of the benchmark
    assert (out / "metrics.json").exists()


def test_metrics_against_the_gold(tmp_path, gold_path):
    _, _, out, _ = _run(tmp_path, gold_path)
    rows = [json.loads(line) for line in (out / "outputs.jsonl").read_text().splitlines()]
    m = score(load_gold(gold_path), rows)
    assert m["documents"]["n"] == 2 and m["documents"]["ok"] == 2
    assert m["behaviour"]["accuracy"] == 1.0
    occ = m["occurrences"]
    assert occ["gold"] == 2 and occ["predicted"] == 2 and occ["predicted_valid"] == 1
    assert occ["matched"] == 1
    assert occ["precision_valid"] == 1.0 and occ["precision_all"] == 0.5 and occ["recall"] == 0.5
    assert occ["field_agreement"]["status"] == 1.0 and occ["field_agreement"]["change_basis"] == 1.0
    assert occ["span_exact_rate"] == 1.0
    claims = m["claims"]
    assert claims["statements"] == 2 and claims["invalid_span_rate"] == 0.5
    assert claims["unsupported_claim_rate"] == 0.5 and claims["on_no_guidance_documents"] == 0
    assert m["cost"]["total_usd"] > 0 and m["latency_ms"]["mean"] == 42


def test_dry_run_calls_nothing_and_estimates(tmp_path, gold_path):
    summary, provider, out, db = _run(tmp_path, gold_path, dry_run=True)
    rows = [json.loads(line) for line in (out / "outputs.jsonl").read_text().splitlines()]
    assert provider.calls == 0 and db.count("llm_calls") == 0
    assert all(r["run_status"] == "dry_run" for r in rows)
    assert all(r["estimated_input_tokens"] > 0 and r["estimated_cost_usd"] > 0 for r in rows)
    assert summary.total_cost_usd == 0.0 and summary.estimated_cost_usd > 0


def test_budget_refusal_is_recorded_not_raised(tmp_path, gold_path):
    summary, provider, out, _ = _run(tmp_path, gold_path, budget_usd=0.0001)
    rows = [json.loads(line) for line in (out / "outputs.jsonl").read_text().splitlines()]
    assert provider.calls == 0
    assert {r["run_status"] for r in rows} == {"budget_refused"}
    m = score(load_gold(gold_path), rows)
    assert m["documents"]["budget_refused"] == 2 and m["behaviour"]["n_scored"] == 0


@pytest.mark.parametrize(
    "statements,has_guidance,expected",
    [
        ([], False, "no_guidance"),
        ([], True, "guidance_mentioned_not_actionable"),
        ([("mentioned", "VALID")], True, "guidance_mentioned_not_actionable"),
        ([("new", "VALID")], True, "guidance_maintained"),
        ([("reaffirmed", "VALID"), ("cut", "INVALID")], True, "guidance_maintained"),
        ([("new", "VALID"), ("raised", "VALID")], True, "guidance_changed"),
        ([("cut", "INVALID")], True, "guidance_mentioned_not_actionable"),
    ],
)
def test_predicted_behaviour_uses_valid_statements_only(statements, has_guidance, expected):
    row = {
        "run_status": "ok",
        "has_guidance": has_guidance,
        "statements": [
            {"statement": {"status": s}, "validation": {"status": v}} for s, v in statements
        ],
    }
    assert predicted_behaviour(row) == expected


def test_default_loader_checks_the_frozen_hashes():
    universe = load_universe()
    manifest = json.loads(
        (ROOT / "tests/fixtures/edgar/harley_davidson_inc_10q_2026q2/manifest.json").read_text()
    )
    row = GoldDocument(
        gold_id="G-H-01",
        issuer_id="HARLEY_DAVIDSON_INC",
        fixture="tests/fixtures/edgar/harley_davidson_inc_10q_2026q2",
        source_url=manifest["source_url"],
        document_date=date(2026, 8, 1),
        raw_sha256=manifest["raw_sha256"],
        normalized_sha256=manifest["normalized_sha256"],
        normalizer_version=manifest["normalizer_version"],
        behaviour="no_guidance",
        occurrences=[],
    )
    d = load_gold_document(row, universe, ROOT)
    assert d.doc_id == manifest["normalized_sha256"]
    with pytest.raises(GoldMismatch):
        load_gold_document(row.model_copy(update={"normalized_sha256": "0" * 64}), universe, ROOT)


def test_results_jsonl_is_sanitised(tmp_path, gold_path):
    """results.jsonl is the committable copy: every verbatim excerpt is replaced by its hash
    and length, offsets, validation and computed figures are kept, metrics are unchanged."""
    import hashlib

    from radar.eval.benchmark import sanitise_row, write_results

    _, _, out, _ = _run(tmp_path, gold_path)
    raw = [json.loads(line) for line in (out / "outputs.jsonl").read_text().splitlines()]
    clean = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]
    assert len(clean) == len(raw) == 2
    first_raw, first_clean = raw[0], clean[0]
    text = json.dumps(clean)
    assert FULL_QUOTE not in text and "evidence_quote" not in text
    st_raw = first_raw["statements"][0]["statement"]
    st_clean = first_clean["statements"][0]["statement"]
    assert st_clean["evidence_sha256"] == hashlib.sha256(FULL_QUOTE.encode()).hexdigest()
    assert st_clean["evidence_chars"] == len(FULL_QUOTE)
    assert st_clean["start_offset"] == st_raw["start_offset"]
    assert st_clean["current_lower"] == 4.0 and st_clean["status"] == "cut"
    assert first_clean["statements"][0]["validation"]["status"] == "VALID"
    assert first_clean["statements"][0]["change"]["change_basis"] == "quantitative"
    for key in (
        "gold_id",
        "run_status",
        "cost_usd",
        "latency_ms",
        "resolved_model",
        "has_guidance",
    ):
        assert first_clean[key] == first_raw[key]
    # scoring the sanitised rows gives the same figures except the exact quote comparison
    assert score(load_gold(gold_path), clean)["occurrences"]["recall"] == 0.5
    # schema failures never leak the model's raw text into the sanitised file
    failed = sanitise_row(
        {
            **first_raw,
            "run_status": "schema_failure",
            "error": "ValidationError: input_value='" + FULL_QUOTE + "'",
        }
    )
    assert FULL_QUOTE not in json.dumps(failed) and failed["error"].startswith("ValidationError")
    # write_results regenerates the file from the raw rows (llm-eval)
    (out / "results.jsonl").unlink()
    write_results(out, raw)
    assert (out / "results.jsonl").exists()


def test_benchmark_runs_the_liquidity_kind_with_its_own_scorer(tmp_path):
    """The benchmark takes a kind: liquidity gold rows, the liquidity schema, validator and
    scorer; outputs keep the same row shape."""
    from radar.eval.liquidity import load_liquidity_gold

    text = "Issuer Test A AG quarterly report\nLiquidity has become constrained in the second quarter.\n"
    quote = "Liquidity has become constrained in the second quarter."
    start = text.index(quote)
    gold_rows = [
        {
            "gold_id": "L-T-01", "issuer_id": "ISSUER_TEST_A", "fixture": "tests/fixtures/none/l",
            "source_url": "https://example.invalid/l", "document_date": "2026-05-11",
            "raw_sha256": "a" * 64, "normalized_sha256": doc(text).doc_id, "normalizer_version": "t",
            "split": "dev", "expected_flag": True, "notes": None, "ineligible": [],
            "statements": [{"status": "concern", "evidence_quote": quote, "start_offset": start, "end_offset": start + len(quote)}],
        }
    ]  # fmt: skip
    gold_path = tmp_path / "liq.jsonl"
    gold_path.write_text("\n".join(json.dumps(r) for r in gold_rows) + "\n")

    class LiquidityProvider(LLMProvider):
        name = "openai"

        def complete(self, request: ExtractionRequest) -> ExtractionResponse:
            payload = {"has_liquidity_statements": True, "statements": [
                {"risk_type": "liquidity", "status": "concern", "metric_label": None, "value": None, "unit": None,
                 "period": None, "evidence_quote": quote, "start_offset": start, "end_offset": start + len(quote)}]}  # fmt: skip
            raw = json.dumps(payload)
            return ExtractionResponse(raw_json=raw, model_id=request.model_id, resolved_model=request.model_id,
                                      provider=self.name, input_tokens=request.estimated_input_tokens,
                                      output_tokens=estimate_tokens(raw), latency_ms=5, reasoning_effort="low")  # fmt: skip

    db = Database(tmp_path / "r.db")
    db.init_schema()
    out = tmp_path / "run"
    summary = run_benchmark(
        load_liquidity_gold(gold_path),
        db=db,
        universe=UNIVERSE,
        provider=LiquidityProvider(),
        cache=LLMCache(db),
        budget=RunBudget(limit_usd=5.0, pricing=load_pricing()),
        prompt=load_prompt(ROOT / "prompts" / "extraction" / "liquidity.v1.yaml"),
        config=_config(),
        out_dir=out,
        load_document=lambda row, universe, root: doc(text, published=row.document_date),
        kind="liquidity",
        gold_path=gold_path,
    )
    rows = [json.loads(line) for line in (out / "outputs.jsonl").read_text().splitlines()]
    assert rows[0]["run_status"] == "ok" and rows[0]["has_liquidity_statements"] is True
    assert rows[0]["statements"][0]["validation"]["status"] == "VALID"
    run = json.loads((out / "run.json").read_text())
    assert run["kind"] == "liquidity" and run["schema_version"] == "liquidity-1.0"
    assert run["extractor_version"] == "llm-liquidity-1.0"
    assert summary.metrics["dev"]["document_flag"]["recall"] == 1.0
    assert summary.metrics["dev"]["negative_statements"]["precision"] == 1.0


def test_benchmark_runs_the_going_concern_kind_with_its_presence_field(tmp_path):
    """The row writer reads the presence field of any family schema
    (has_going_concern_statements), the scorer flags on doubt alone."""
    from radar.eval.going_concern import load_going_concern_gold

    text = "Issuer Test A AG quarterly report\nThese conditions raise substantial doubt about the Company's ability to continue as a going concern.\n"
    quote = "These conditions raise substantial doubt about the Company's ability to continue as a going concern."
    start = text.index(quote)
    gold_rows = [
        {
            "gold_id": "GC-T-01", "issuer_id": "ISSUER_TEST_A", "fixture": "tests/fixtures/none/g",
            "source_url": "https://example.invalid/g", "document_date": "2026-05-11",
            "raw_sha256": "a" * 64, "normalized_sha256": doc(text).doc_id, "normalizer_version": "t",
            "split": "dev", "expected_flag": True, "notes": None, "ineligible": [],
            "statements": [{"status": "doubt", "evidence_quote": quote, "start_offset": start, "end_offset": start + len(quote)}],
        }
    ]  # fmt: skip
    gold_path = tmp_path / "gc.jsonl"
    gold_path.write_text("\n".join(json.dumps(r) for r in gold_rows) + "\n")

    class GoingConcernProvider(LLMProvider):
        name = "openai"

        def complete(self, request: ExtractionRequest) -> ExtractionResponse:
            payload = {"has_going_concern_statements": True, "statements": [
                {"risk_type": "going_concern", "status": "doubt", "period": None,
                 "evidence_quote": quote, "start_offset": start, "end_offset": start + len(quote)}]}  # fmt: skip
            raw = json.dumps(payload)
            return ExtractionResponse(raw_json=raw, model_id=request.model_id, resolved_model=request.model_id,
                                      provider=self.name, input_tokens=request.estimated_input_tokens,
                                      output_tokens=estimate_tokens(raw), latency_ms=5, reasoning_effort="low")  # fmt: skip

    db = Database(tmp_path / "r.db")
    db.init_schema()
    out = tmp_path / "run"
    summary = run_benchmark(
        load_going_concern_gold(gold_path),
        db=db,
        universe=UNIVERSE,
        provider=GoingConcernProvider(),
        cache=LLMCache(db),
        budget=RunBudget(limit_usd=5.0, pricing=load_pricing()),
        prompt=load_prompt(ROOT / "prompts" / "extraction" / "going_concern.v1.yaml"),
        config=_config(),
        out_dir=out,
        load_document=lambda row, universe, root: doc(text, published=row.document_date),
        kind="going_concern",
        gold_path=gold_path,
        selection={
            "kind": "going_concern",
            "role": "explicit",
            "name": "openai_terra",
            "model_id": "gpt-5.6-terra",
            "provider": "openai",
            "reason": "test",
        },
    )
    rows = [json.loads(line) for line in (out / "outputs.jsonl").read_text().splitlines()]
    assert rows[0]["run_status"] == "ok" and rows[0]["has_going_concern_statements"] is True
    assert rows[0]["statements"][0]["validation"]["status"] == "VALID"
    run = json.loads((out / "run.json").read_text())
    assert run["kind"] == "going_concern" and run["schema_version"] == "going_concern-1.0"
    assert run["model_selection"]["role"] == "explicit"
    assert summary.metrics["dev"]["document_flag"]["recall"] == 1.0
