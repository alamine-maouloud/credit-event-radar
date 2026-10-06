"""One structured extraction, end to end: cache, budget hard stop, call, strict parse, audit."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from radar.audit import AuditEntry, stable_hash
from radar.db import Database
from radar.llm.budget import BudgetExceeded, RunBudget
from radar.llm.cache import LLMCache, cache_key
from radar.llm.provider import ExtractionRequest, ExtractionResponse, LLMProvider
from radar.llm.schemas import GUIDANCE_SCHEMA_VERSION

RunStatus = Literal[
    "ok", "cached", "budget_refused", "schema_failure", "provider_error", "truncated"
]


class ExtractionRun(BaseModel):
    status: RunStatus
    cached: bool
    parsed: Any | None = None
    response: ExtractionResponse | None = None
    error: str | None = None
    cost_usd: float | None = None
    cache_key: str


def _parse(raw_json: str, schema_model: type[BaseModel]) -> tuple[BaseModel | None, str | None]:
    try:
        return schema_model.model_validate(json.loads(raw_json)), None
    except (json.JSONDecodeError, ValidationError) as exc:
        return None, f"{exc.__class__.__name__}: {str(exc)[:400]}"


def run_extraction(
    db: Database,
    request: ExtractionRequest,
    schema_model: type[BaseModel],
    *,
    provider: LLMProvider,
    cache: LLMCache,
    budget: RunBudget,
    document_hash: str,
    extractor_version: str,
    prompt_version: str,
    doc_id: str,
    event_id: str | None = None,
    schema_version: str = GUIDANCE_SCHEMA_VERSION,
) -> ExtractionRun:
    key = cache_key(
        document_hash=document_hash,
        extractor_version=extractor_version,
        provider=provider.name,
        model_id=request.model_id,
        prompt_version=prompt_version,
        schema_version=schema_version,
        reasoning_effort=request.reasoning_effort,
    )
    inputs_hash = stable_hash(
        {"system": request.system, "user": request.user, "schema": request.json_schema}
    )
    base_call = {
        "doc_id": doc_id,
        "event_id": event_id,
        "provider": provider.name,
        "model_id": request.model_id,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "reasoning_effort": request.reasoning_effort,
        "inputs_hash": inputs_hash,
        "cache_key": key,
    }

    hit = cache.get(key)
    if hit is not None:
        parsed, error = _parse(hit.raw_json, schema_model)
        status: RunStatus = "cached" if parsed is not None else "schema_failure"
        db.insert_llm_call(
            {
                **base_call,
                "resolved_model": hit.resolved_model,
                "outputs_hash": stable_hash(hit.raw_json),
                "input_tokens": hit.input_tokens,
                "output_tokens": hit.output_tokens,
                "cost_usd": 0.0,
                "latency_ms": 0,
                "cached": 1,
                "status": status,
            }
        )
        db.audit(
            AuditEntry(
                step="llm_extract",
                doc_id=doc_id,
                event_id=event_id,
                inputs_hash=inputs_hash,
                outputs_hash=stable_hash(hit.raw_json),
                model_id=request.model_id,
                prompt_version=prompt_version,
                latency_ms=0,
                cost_usd=0.0,
                status="ok" if parsed is not None else "error",
                message=f"cache hit ({provider.name} {request.model_id}, effort {request.reasoning_effort})"  # noqa: E501
                + (f"; schema failure: {error}" if error else ""),
            )
        )
        return ExtractionRun(
            status=status,
            cached=True,
            parsed=parsed,
            response=hit,
            error=error,
            cost_usd=0.0,
            cache_key=key,
        )

    try:
        reservation = budget.reserve(
            request.model_id,
            estimated_input_tokens=request.estimated_input_tokens,
            estimated_output_tokens=request.max_output_tokens,
        )
    except (BudgetExceeded, KeyError) as exc:
        message = f"refused before the call: {exc}"
        db.audit(
            AuditEntry(
                step="llm_extract",
                doc_id=doc_id,
                event_id=event_id,
                inputs_hash=inputs_hash,
                model_id=request.model_id,
                prompt_version=prompt_version,
                status="skipped",
                message=message,
            )
        )
        return ExtractionRun(status="budget_refused", cached=False, error=str(exc), cache_key=key)

    try:
        response = provider.complete(request)
    except Exception as exc:  # the provider's own error is the message, nothing is retried silently
        budget.release(reservation)
        db.audit(
            AuditEntry(
                step="llm_extract",
                doc_id=doc_id,
                event_id=event_id,
                inputs_hash=inputs_hash,
                model_id=request.model_id,
                prompt_version=prompt_version,
                status="error",
                message=f"provider error: {exc.__class__.__name__}: {str(exc)[:300]}",
            )
        )
        return ExtractionRun(status="provider_error", cached=False, error=str(exc), cache_key=key)

    entry = budget.settle(
        reservation,
        actual_input_tokens=response.input_tokens,
        actual_output_tokens=response.output_tokens,
    )
    if response.incomplete_reason:
        parsed, error = None, f"response incomplete: {response.incomplete_reason}"
        status: RunStatus = "truncated"
    else:
        parsed, error = _parse(response.raw_json, schema_model)
        status = "ok" if parsed is not None else "schema_failure"
    if parsed is not None:
        cache.put(key, response)  # only complete, parseable answers are worth replaying
    outputs_hash = stable_hash(response.raw_json)
    db.insert_llm_call(
        {
            **base_call,
            "resolved_model": response.resolved_model,
            "outputs_hash": outputs_hash,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": entry.actual_cost_after_call,
            "latency_ms": response.latency_ms,
            "cached": 0,
            "status": status,
        }
    )
    db.audit(
        AuditEntry(
            step="llm_extract",
            doc_id=doc_id,
            event_id=event_id,
            inputs_hash=inputs_hash,
            outputs_hash=outputs_hash,
            model_id=request.model_id,
            prompt_version=prompt_version,
            latency_ms=response.latency_ms,
            cost_usd=entry.actual_cost_after_call,
            status="ok" if parsed is not None else "error",
            message=(
                f"{provider.name} {request.model_id} (resolved {response.resolved_model}, effort {request.reasoning_effort}): "  # noqa: E501
                f"estimated {entry.estimated_cost_before_call:.4f} USD before, actual {entry.actual_cost_after_call:.4f} after, "  # noqa: E501
                f"cumulative {entry.cumulative_run_cost:.4f}, remaining {entry.budget_remaining:.4f}"  # noqa: E501
                + (f"; {status}: {error}" if error else "")
            ),
        )
    )
    return ExtractionRun(
        status=status,
        cached=False,
        parsed=parsed,
        response=response,
        error=error,
        cost_usd=entry.actual_cost_after_call,
        cache_key=key,
    )


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
