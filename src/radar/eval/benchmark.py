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
from radar.enrich.extract import KINDS
from radar.eval.covenant import score_covenant
from radar.eval.gold import GoldDocument, load_gold_document
from radar.eval.liquidity import score_liquidity
from radar.eval.metrics import score
from radar.llm.budget import RunBudget
from radar.llm.cache import LLMCache
from radar.llm.prompts import Prompt
from radar.llm.provider import ExtractionRequest, LLMProvider
from radar.llm.requests import build_extraction_request
from radar.llm.runner import run_extraction
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
    extractor_version: str | None = None  # per kind when None


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
    doc: RawDocument,
    issuer_name: str,
    document_date: str,
    prompt: Prompt,
    config: BenchmarkConfig,
    kind: str = "guidance",
) -> ExtractionRequest:
    spec = KINDS[kind]
    return build_extraction_request(
        doc,
        issuer_name,
        document_date,
        prompt,
        schema_name=spec["schema_name"],
        json_schema=spec["json_schema"](),
        model_id=config.model_id,
        temperature=config.temperature,
        reasoning_effort=config.reasoning_effort,
        max_output_tokens=config.max_output_tokens,
    )


def _statement_entries(parsed, doc, names, document_date, segments=None, kind="guidance"):
    spec = KINDS[kind]
    entries = []
    for st in parsed.statements:
        validation = spec["validate"](
            st, doc, names, document_date=document_date, segments=segments
        )
        change = asdict(spec["change"](st)) if spec["change"] else {}
        entries.append(
            {"statement": st.model_dump(), "validation": validation.model_dump(), "change": change}
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
    kind: str = "guidance",
) -> RunSummary:
    spec = KINDS[kind]
    extractor_version = config.extractor_version or spec["extractor_version"]
    started = datetime.now(UTC)
    run_id = out_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    issuers = {i.id: i for i in universe.issuers}
    rows: list[dict[str, Any]] = []
    estimated_total = 0.0
    for gold in gold_rows[:limit] if limit else gold_rows:
        issuer = issuers[gold.issuer_id]
        doc = load_document(gold, universe, root)
        request = build_request(
            doc, issuer.name, gold.document_date.isoformat(), prompt, config, kind
        )
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
                spec["model"],
                provider=provider,
                cache=cache,
                budget=budget,
                document_hash=doc.doc_id,
                extractor_version=extractor_version,
                prompt_version=prompt.version,
                doc_id=doc.doc_id,
                schema_version=spec["schema_version"],
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
                parsed = result.parsed
                if kind == "guidance":
                    row["has_guidance"] = parsed.has_guidance
                elif kind == "liquidity":
                    row["has_liquidity_statements"] = parsed.has_liquidity_statements
                else:
                    row["has_covenant_statements"] = parsed.has_covenant_statements
                row["statements"] = _statement_entries(
                    parsed, doc, issuer_names(issuer), gold.document_date, issuer.segments, kind
                )
        rows.append(row)
    (out_dir / "outputs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows) + "\n",
        encoding="utf-8",
    )
    write_results(out_dir, rows)
    scorers = {"guidance": score, "liquidity": score_liquidity, "covenant": score_covenant}
    metrics = scorers[kind](gold_rows, rows)
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
        "kind": kind,
        "schema_version": spec["schema_version"],
        "extractor_version": extractor_version,
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
