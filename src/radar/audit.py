"""Audit log entries (docs/SPEC.md section 14). Pure helpers; persistence is in db.py."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AuditStatus = Literal["ok", "skipped", "error"]


def stable_hash(payload: Any) -> str:
    """SHA-256 of the canonical JSON form of ``payload`` (sorted keys, no spaces)."""
    data = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class AuditEntry(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    step: str
    event_id: str | None = None
    doc_id: str | None = None
    inputs_hash: str | None = None
    outputs_hash: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    rules_version: str | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    status: AuditStatus = "ok"
    message: str | None = None
