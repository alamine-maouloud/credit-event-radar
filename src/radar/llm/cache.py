"""Response cache keyed on every experimental parameter (CLAUDE.md stack, ADR-014)."""

from __future__ import annotations

from datetime import UTC, datetime

from radar.audit import stable_hash
from radar.db import Database
from radar.llm.provider import ExtractionResponse


def cache_key(
    *,
    document_hash: str,
    extractor_version: str,
    provider: str,
    model_id: str,
    prompt_version: str,
    schema_version: str,
    reasoning_effort: str | None,
) -> str:
    return stable_hash(
        {
            "document_hash": document_hash,
            "extractor_version": extractor_version,
            "provider": provider,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "reasoning_effort": reasoning_effort or "none",
        }
    )


class LLMCache:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str) -> ExtractionResponse | None:
        raw = self.db.llm_cache_get(key)
        return ExtractionResponse.model_validate_json(raw) if raw else None

    def put(self, key: str, response: ExtractionResponse) -> None:
        self.db.llm_cache_put(key, response.model_dump_json(), datetime.now(UTC).isoformat())
