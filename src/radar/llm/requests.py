"""The extraction request for one document, shared by the benchmark and the pipeline."""

from __future__ import annotations

from radar.llm.prompts import Prompt, render_prompt
from radar.llm.provider import ExtractionRequest
from radar.llm.schemas import guidance_json_schema
from radar.models import RawDocument

DEFAULT_MAX_OUTPUT_TOKENS = 32768  # reasoning tokens count against the ceiling (ADR-015)


def build_guidance_request(
    doc: RawDocument,
    issuer_name: str,
    document_date: str,
    prompt: Prompt,
    *,
    model_id: str,
    temperature: float = 0.0,
    reasoning_effort: str | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> ExtractionRequest:
    system, user = render_prompt(
        prompt,
        {"issuer_name": issuer_name, "document_date": document_date, "source_text": doc.text},
    )
    return ExtractionRequest(
        system=system,
        user=user,
        schema_name="GuidanceExtraction",
        json_schema=guidance_json_schema(),
        model_id=model_id,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )
