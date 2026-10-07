"""Optional prose of the committee note (Phase P3): two sections written by the notes
model from the verified facts only, under one cap for the whole demo, cached like every
other call, never required."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from radar.alerts.model import Alert
from radar.audit import stable_hash
from radar.config import ROOT
from radar.db import Database
from radar.llm.budget import RunBudget
from radar.llm.cache import LLMCache
from radar.llm.pricing import Pricing
from radar.llm.prompts import load_prompt
from radar.llm.provider import LLMProvider
from radar.llm.requests import build_request_from_text
from radar.llm.runner import run_extraction
from radar.llm.schemas import strict_json_schema

NOTE_PROSE_SCHEMA_VERSION = "note-prose-1.0"
NOTE_PROSE_EXTRACTOR_VERSION = "note-prose-1.0"


class NoteProse(BaseModel):
    """The two optional sections, sentences citing the facts [n] they rest on."""

    model_config = ConfigDict(extra="forbid")

    why_it_matters: list[str] = Field(default_factory=list)
    points_to_verify: list[str] = Field(default_factory=list)


class GenerationLedger:
    """One cap for every note of the demo, persisted across commands."""

    def __init__(self, path: Path, cap_usd: float) -> None:
        self.path = path
        self.cap_usd = float(cap_usd)
        self.entries: list[dict[str, Any]] = []
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.entries = list(data.get("entries", []))

    @property
    def spent_usd(self) -> float:
        return round(sum(float(e.get("cost_usd") or 0.0) for e in self.entries), 6)

    @property
    def remaining_usd(self) -> float:
        return round(self.cap_usd - self.spent_usd, 6)

    def record(self, event_id: str, lang: str, cost_usd: float, model_id: str) -> None:
        self.entries.append(
            {
                "event_id": event_id,
                "lang": lang,
                "cost_usd": float(cost_usd),
                "model_id": model_id,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"cap_usd": self.cap_usd, "spent_usd": self.spent_usd, "entries": self.entries},
                indent=2,
            ),
            encoding="utf-8",
        )


@dataclass
class ProseResult:
    prose: NoteProse | None
    cost_usd: float | None
    cached: bool
    provenance: dict[str, Any] | None
    reason: str | None = None


def facts_digest(alert: Alert) -> str:
    """What the model sees: the verified facts numbered as the note cites them, the rules,
    the ratings and the decision. Nothing else."""
    lines = ["Verified facts (verbatim passages of the sources):"]
    for f in alert.facts:
        field = f" ({f.field})" if f.field else ""
        lines.append(f"[{f.index}] {f.text}{field} [source {f.source}]")
    lines.append("")
    lines.append(f"Decision: {alert.summary}")
    lines.append("Triggered rules:")
    for r in alert.triggered_rules:
        lines.append(f"- {r.id} [{r.priority}] {r.description or ''}: {r.reason}")
    if alert.ratings_after:
        lines.append("Ratings after the action:")
        for r in alert.ratings_after:
            lines.append(
                f"- {r.agency_name}: {r.rating} {r.category}"
                + (f", outlook {r.outlook}" if r.outlook else "")
                + f" (as of {r.as_of.isoformat()})"
            )
    return "\n".join(lines)


def generate_prose(
    db: Database,
    alert: Alert,
    lang: str,
    *,
    provider: LLMProvider,
    pricing: Pricing,
    ledger: GenerationLedger,
    model_id: str,
    reasoning_effort: str | None = "low",
    temperature: float = 0.0,
    prompt_dir: Path = ROOT / "prompts" / "notes",
) -> ProseResult:
    if ledger.remaining_usd <= 0:
        return ProseResult(
            None, None, False, None,
            reason=(
                f"prose sections not generated: the demo generation budget of "
                f"{ledger.cap_usd:.2f} USD is spent ({ledger.spent_usd:.4f} USD)"
            ),
        )  # fmt: skip
    prompt = load_prompt(prompt_dir / f"committee_note.{lang}.v1.yaml")
    request = build_request_from_text(
        facts_digest(alert),
        alert.issuer_name,
        alert.effective_date.isoformat() if alert.effective_date else "unknown",
        prompt,
        schema_name="NoteProse",
        json_schema=strict_json_schema(NoteProse),
        model_id=model_id,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_output_tokens=4096,
    )
    run = run_extraction(
        db,
        request,
        NoteProse,
        provider=provider,
        cache=LLMCache(db),
        budget=RunBudget(limit_usd=ledger.remaining_usd, pricing=pricing),
        document_hash=stable_hash({"alert": alert.alert_id, "lang": lang}),
        extractor_version=NOTE_PROSE_EXTRACTOR_VERSION,
        prompt_version=prompt.version,
        doc_id=alert.sources[0].doc_id if alert.sources else alert.event_id,
        event_id=alert.event_id,
        schema_version=NOTE_PROSE_SCHEMA_VERSION,
    )
    cost = run.cost_usd or 0.0
    if run.parsed is None:
        return ProseResult(
            None, cost, run.cached, None,
            reason=f"prose sections not generated: {run.status} {run.error or ''}".strip(),
        )  # fmt: skip
    if cost:
        ledger.record(alert.event_id, lang, cost, model_id)
    provenance = {
        "kind": "note_prose",
        "model_id": model_id,
        "resolved_model": run.response.resolved_model if run.response else model_id,
        "prompt_id": prompt.id,
        "prompt_version": prompt.version,
        "schema_version": NOTE_PROSE_SCHEMA_VERSION,
        "cached": run.cached,
        "cost_usd": cost,
        "role": "notes",
        "reason": "settings.llm.roles.notes (Terra); one cap for every note of the demo",
    }
    return ProseResult(run.parsed, cost, run.cached, provenance)
