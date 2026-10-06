"""One benchmark run: every gold document through the extraction, the validator and the
derived figures; outputs.jsonl, run.json and metrics.json in the run directory."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from radar.config import ROOT, Universe
from radar.db import Database
from radar.eval.gold import GoldDocument, load_gold_document
from radar.eval.metrics import score
from radar.llm.budget import RunBudget
from radar.llm.cache import LLMCache
from radar.llm.compute import compute_guidance_change
from radar.llm.prompts import Prompt
from radar.llm.provider import ExtractionRequest, LLMProvider
from radar.llm.requests import build_guidance_request
from radar.llm.runner import run_extraction
from radar.llm.schemas import GUIDANCE_SCHEMA_VERSION, GuidanceExtraction
from radar.llm.validate import validate_statement
from radar.models import RawDocument
from radar.pipeline import issuer_names

EXTRACTOR_VERSION = "llm-guidance-1.0"
Loader = Callable[[GoldDocument, Universe, Path], RawDocument]


class BenchmarkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    reasoning_effort: str | None = None
    temperature: float = 0.0
    # Reasoning tokens count against the ceiling: 4096 left no text at all on the second
    # real document (2026-10-06). The budget reserves the ceiling before every call.
    max_output_tokens: int = Field(default=32768, ge=1)
    extractor_version: str = EXTRACTOR_VERSION


class RunSummary(BaseModel):
    run_id: str
    out_dir: Path
    n_documents: int
    statuses: dict[str, int]
    total_cost_usd: float
    estimated_cost_usd: float
    budget_limit_usd: float
    budget_remaining_usd: float
    metrics: dict[str, Any]


def build_request(
    doc: RawDocument, issuer_name: str, document_date: str, prompt: Prompt, config: BenchmarkConfig
) -> ExtractionRequest:
    return build_guidance_request(
        doc,
        issuer_name,
        document_date,
        prompt,
        model_id=config.model_id,
        temperature=config.temperature,
        reasoning_effort=config.reasoning_effort,
        max_output_tokens=config.max_output_tokens,
    )


def _statement_entries(
    parsed: GuidanceExtraction, doc, names, document_date, segments: list[str] | None = None
) -> list[dict]:
    entries = []
    for st in parsed.statements:
        validation = validate_statement(
            st, doc, names, document_date=document_date, segments=segments
        )
        change = compute_guidance_change(st)
        entries.append(
            {
                "statement": st.model_dump(),
                "validation": validation.model_dump(),
                "change": asdict(change),
            }
        )
    return entries


def run_benchmark(
    gold_rows: list[GoldDocument],
    *,
    db: Database,
    universe: Universe,
    provider: LLMProvider,
    cache: LLMCache,
    budget: RunBudget,
    prompt: Prompt,
    config: BenchmarkConfig,
    out_dir: Path,
    load_document: Loader = load_gold_document,
    root: Path = ROOT,
    limit: int | None = None,
    dry_run: bool = False,
    gold_path: Path | None = None,
) -> RunSummary:
    started = datetime.now(UTC)
    run_id = out_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    issuers = {i.id: i for i in universe.issuers}
    rows: list[dict[str, Any]] = []
    estimated_total = 0.0
    for gold in gold_rows[:limit] if limit else gold_rows:
        issuer = issuers[gold.issuer_id]
        doc = load_document(gold, universe, root)
        request = build_request(doc, issuer.name, gold.document_date.isoformat(), prompt, config)
        row: dict[str, Any] = {
            "gold_id": gold.gold_id,
            "issuer_id": gold.issuer_id,
            "doc_id": doc.doc_id,
            "document_date": gold.document_date.isoformat(),
            "document_chars": len(doc.text),
            "estimated_input_tokens": request.estimated_input_tokens,
            "estimated_cost_usd": budget.pricing.cost(
                config.model_id,
                input_tokens=request.estimated_input_tokens,
                output_tokens=config.max_output_tokens,
            ),
            "run_status": "dry_run",
            "cached": False,
            "cost_usd": 0.0,
            "latency_ms": None,
            "input_tokens": None,
            "output_tokens": None,
            "resolved_model": None,
            "cache_key": None,
            "error": None,
            "has_guidance": None,
            "statements": [],
        }
        estimated_total += row["estimated_cost_usd"]
        if not dry_run:
            result = run_extraction(
                db,
                request,
                GuidanceExtraction,
                provider=provider,
                cache=cache,
                budget=budget,
                document_hash=doc.doc_id,
                extractor_version=config.extractor_version,
                prompt_version=prompt.version,
                doc_id=doc.doc_id,
            )
            row.update(
                run_status=result.status,
                cached=result.cached,
                cost_usd=result.cost_usd or 0.0,
                cache_key=result.cache_key,
                error=result.error,
            )
            if result.response is not None:
                row.update(
                    latency_ms=result.response.latency_ms,
                    input_tokens=result.response.input_tokens,
                    output_tokens=result.response.output_tokens,
                    resolved_model=result.response.resolved_model,
                )
            if result.parsed is not None:
                parsed: GuidanceExtraction = result.parsed
                row["has_guidance"] = parsed.has_guidance
                row["statements"] = _statement_entries(
                    parsed, doc, issuer_names(issuer), gold.document_date, issuer.segments
                )
        rows.append(row)
    (out_dir / "outputs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows) + "\n",
        encoding="utf-8",
    )
    write_results(out_dir, rows)
    metrics = score(gold_rows, rows)
    statuses: dict[str, int] = {}
    for r in rows:
        statuses[r["run_status"]] = statuses.get(r["run_status"], 0) + 1
    total_cost = sum(r["cost_usd"] for r in rows)
    run = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "provider": provider.name,
        "model_id": config.model_id,
        "resolved_models": sorted({r["resolved_model"] for r in rows if r["resolved_model"]}),
        "reasoning_effort": config.reasoning_effort,
        "temperature": config.temperature,
        "max_output_tokens": config.max_output_tokens,
        "prompt_id": prompt.id,
        "prompt_version": prompt.version,
        "prompt_content_hash": prompt.content_hash,
        "schema_version": GUIDANCE_SCHEMA_VERSION,
        "extractor_version": config.extractor_version,
        "gold_path": str(gold_path) if gold_path else None,
        "gold_labels_sha256": _sha256(gold_path) if gold_path else None,
        "gold_lock_sha256": _lock_hash(gold_path),
        "n_documents": len(rows),
        "statuses": statuses,
        "total_cost_usd": total_cost,
        "estimated_cost_usd": estimated_total,
        "budget_limit_usd": budget.limit_usd,
        "budget_remaining_usd": budget.remaining,
        "input_tokens": sum(r["input_tokens"] or 0 for r in rows),
        "output_tokens": sum(r["output_tokens"] or 0 for r in rows),
        "dry_run": dry_run,
        "limit": limit,
    }
    (out_dir / "run.json").write_text(json.dumps(run, indent=2, default=str), encoding="utf-8")
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return RunSummary(
        run_id=run_id,
        out_dir=out_dir,
        n_documents=len(rows),
        statuses=statuses,
        total_cost_usd=total_cost,
        estimated_cost_usd=estimated_total,
        budget_limit_usd=budget.limit_usd,
        budget_remaining_usd=budget.remaining,
        metrics=metrics,
    )


def sanitise_row(row: dict[str, Any]) -> dict[str, Any]:
    """The committable form of an output row: verbatim excerpts of the private documents are
    replaced by their hash and length; offsets, numbers, validation and figures are kept."""
    out = dict(row)
    statements = []
    for entry in row.get("statements", []):
        st = dict(entry["statement"])
        quote = st.pop("evidence_quote", "")
        st["evidence_sha256"] = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        st["evidence_chars"] = len(quote)
        statements.append({**entry, "statement": st})
    out["statements"] = statements
    if row.get("run_status") == "schema_failure" and row.get("error"):
        # pydantic messages echo the model's raw text; keep the error class only
        out["error"] = str(row["error"]).split(":", 1)[0] + " (details in the local outputs.jsonl)"
    return out


def write_results(out_dir: Path, rows: list[dict[str, Any]]) -> Path:
    path = out_dir / "results.jsonl"
    path.write_text(
        "\n".join(json.dumps(sanitise_row(r), ensure_ascii=False, default=str) for r in rows)
        + "\n",
        encoding="utf-8",
    )
    return path


def _sha256(path: Path | None) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path and path.exists() else None


def _lock_hash(gold_path: Path | None) -> str | None:
    if gold_path is None:
        return None
    lock = gold_path.with_suffix(".lock.json")
    if not lock.exists():
        return None
    return json.loads(lock.read_text(encoding="utf-8")).get("lock_sha256")
