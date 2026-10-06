"""`radar llm-extract --events`: the guidance extraction on the documents behind the stored
earnings_release events. Statements and their validation are stored, events are not touched."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from radar.audit import AuditEntry
from radar.config import Universe
from radar.db import Database
from radar.llm.budget import RunBudget
from radar.llm.cache import LLMCache
from radar.llm.compute import compute_guidance_change
from radar.llm.prompts import Prompt
from radar.llm.provider import LLMProvider
from radar.llm.requests import DEFAULT_MAX_OUTPUT_TOKENS, build_extraction_request
from radar.llm.runner import run_extraction
from radar.llm.schemas import (
    GUIDANCE_SCHEMA_VERSION,
    LIQUIDITY_SCHEMA_VERSION,
    GuidanceExtraction,
    LiquidityExtraction,
    guidance_json_schema,
    liquidity_json_schema,
)
from radar.llm.validate import validate_liquidity_statement, validate_statement
from radar.pipeline import issuer_names
from radar.snapshot import sha256_hex

EXTRACTOR_VERSION = "llm-guidance-1.0"

# One extraction kind = one schema, one prompt, one validator, one cache namespace.
KINDS: dict[str, dict] = {
    "guidance": {
        "model": GuidanceExtraction,
        "schema_name": "GuidanceExtraction",
        "json_schema": guidance_json_schema,
        "schema_version": GUIDANCE_SCHEMA_VERSION,
        "extractor_version": "llm-guidance-1.0",
        "validate": validate_statement,
        "change": compute_guidance_change,
    },
    "liquidity": {
        "model": LiquidityExtraction,
        "schema_name": "LiquidityExtraction",
        "json_schema": liquidity_json_schema,
        "schema_version": LIQUIDITY_SCHEMA_VERSION,
        "extractor_version": "llm-liquidity-1.0",
        "validate": validate_liquidity_statement,
        "change": None,
    },
}


@dataclass
class ExtractSummary:
    documents: int = 0
    calls: int = 0
    cached: int = 0
    cost_usd: float = 0.0
    statements: int = 0
    valid: int = 0
    skipped: list[tuple[str, str, str | None]] = field(default_factory=list)


def extract_events(
    db: Database,
    universe: Universe,
    *,
    provider: LLMProvider,
    cache: LLMCache,
    budget: RunBudget,
    prompt: Prompt,
    model_id: str,
    reasoning_effort: str | None = None,
    temperature: float = 0.0,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    issuer_id: str | None = None,
    doc_id: str | None = None,
    kind: str = "guidance",
    extractor_version: str | None = None,
) -> ExtractSummary:
    spec = KINDS[kind]
    extractor_version = extractor_version or spec["extractor_version"]
    issuers = {i.id: i for i in universe.issuers}
    summary = ExtractSummary()
    seen: set[str] = set()
    for event in db.list_events(issuer_id):
        if event.family != "earnings" or event.event_type != "earnings_release":
            continue
        for source in event.source_doc_ids:
            if (doc_id and source != doc_id) or source in seen:
                continue
            seen.add(source)
            doc = db.get_document(source)
            issuer = issuers.get(event.issuer_id)
            if doc is None or issuer is None:
                summary.skipped.append((source, "missing", "document or issuer not found"))
                continue
            summary.documents += 1
            document_date = event.effective_date or (
                doc.published_at.date() if doc.published_at else None
            )
            request = build_extraction_request(
                doc,
                issuer.name,
                document_date.isoformat() if document_date else "unknown",
                prompt,
                schema_name=spec["schema_name"],
                json_schema=spec["json_schema"](),
                model_id=model_id,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                max_output_tokens=max_output_tokens,
            )
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
                event_id=event.event_id,
                schema_version=spec["schema_version"],
            )
            summary.calls += result.status == "ok"
            summary.cached += result.status == "cached"
            summary.cost_usd += result.cost_usd or 0.0
            if result.parsed is None:
                summary.skipped.append((doc.doc_id, result.status, result.error))
                continue
            parsed = result.parsed
            names = issuer_names(issuer)
            rows = []
            for index, st in enumerate(parsed.statements):
                validation = spec["validate"](
                    st, doc, names, document_date=document_date, segments=issuer.segments
                )
                change = asdict(spec["change"](st)) if spec["change"] else {}
                rows.append(
                    {
                        "statement_id": sha256_hex(f"{result.cache_key}:{index}"),
                        "llm_call_id": result.llm_call_id,
                        "doc_id": doc.doc_id,
                        "issuer_id": issuer.id,
                        "event_id": event.event_id,
                        "model_id": request.model_id,
                        "resolved_model": result.response.resolved_model
                        if result.response
                        else None,
                        "prompt_version": prompt.version,
                        "schema_version": spec["schema_version"],
                        "statement_json": st.model_dump(),
                        "validation_json": validation.model_dump(),
                        "change_json": change,
                        "validation_status": validation.status,
                        "statement_kind": kind,
                    }
                )
            db.insert_statements(rows)
            n_valid = sum(1 for r in rows if r["validation_status"] == "VALID")
            summary.statements += len(rows)
            summary.valid += n_valid
            db.audit(
                AuditEntry(
                    step="llm_extract_events",
                    doc_id=doc.doc_id,
                    event_id=event.event_id,
                    model_id=request.model_id,
                    prompt_version=prompt.version,
                    cost_usd=result.cost_usd,
                    status="ok",
                    message=(
                        f"{kind} {result.status}: {len(rows)} statement(s), {n_valid} valid, "
                        f"events untouched"
                    ),
                )
            )
    return summary
