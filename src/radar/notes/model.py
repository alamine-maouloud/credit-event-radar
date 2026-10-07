"""The committee note (Phase P3, SPEC 12): a deterministic document built from the Alert
object, complete without any model. Two prose sections may come from a model; every
generated sentence is tied to the verified facts it cites or excluded as UNSUPPORTED."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from radar.alerts.model import AlertFact, AlertRating, AlertRule, AlertSource
from radar.models import Lang, Priority

CITATION_RE = re.compile(r"\[(\d+)\]")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
ClaimStatus = Literal["VERIFIED", "UNSUPPORTED"]


class NoteClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    citations: list[int] = Field(default_factory=list)
    status: ClaimStatus
    checks: dict[str, bool] = Field(default_factory=dict)
    reason: str | None = None


class NoteSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    source: Literal["deterministic", "llm"]
    claims: list[NoteClaim] = Field(default_factory=list)  # VERIFIED claims only


class NoteIndicator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str
    source: str


class CommitteeNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str
    event_id: str
    alert_id: str
    lang: Lang
    generated_at: datetime
    issuer_id: str
    issuer_name: str
    universe_label: str
    priority: Priority | None
    decision_status: str
    title: str
    summary: str
    effective_date: str | None
    ratings_after: list[AlertRating] = Field(default_factory=list)
    key_facts: list[AlertFact] = Field(default_factory=list)
    indicators: list[NoteIndicator] = Field(default_factory=list)
    triggered_rules: list[AlertRule] = Field(default_factory=list)
    why_it_matters: NoteSection
    points_to_verify: NoteSection
    sources: list[AlertSource] = Field(default_factory=list)
    verification: dict[str, int]
    unsupported: list[NoteClaim] = Field(default_factory=list)
    model_provenance: dict[str, Any] | None = None
    rules_version: str
    notice: str | None = None
    disclaimer: str


def _figure(token: str) -> str:
    return token.replace(",", ".")


def verify_claim(text: str, facts: list[AlertFact], context: str = "") -> NoteClaim:
    """SPEC 10, the deterministic controls: a citation is present, every cited passage
    exists, every figure of the sentence appears in the cited passages (or in the event
    header given as context). No model judges a claim here; a failed control is UNSUPPORTED."""
    citations = [int(c) for c in CITATION_RE.findall(text)]
    by_index = {f.index: f for f in facts}
    citation_present = bool(citations)
    passage_found = citation_present and all(c in by_index for c in citations)
    cited = " ".join(by_index[c].text for c in citations if c in by_index) + " " + context
    body = CITATION_RE.sub("", text)
    # a French sentence writes 6,6 for the 6.6 of an English filing: figures are compared
    # with the decimal comma read as a point, nothing else is normalised
    cited_figures = {_figure(n) for n in NUMBER_RE.findall(cited)}
    missing = [n for n in NUMBER_RE.findall(body) if _figure(n) not in cited_figures]
    checks = {
        "citation_present": citation_present,
        "passage_found": passage_found,
        "numbers_consistent": not missing,
    }
    reason = None
    if not citation_present:
        reason = "no citation [n] to a verified fact"
    elif not passage_found:
        unknown = sorted({c for c in citations if c not in by_index})
        reason = f"cites unknown fact(s) {unknown}"
    elif missing:
        reason = f"figures not in the cited facts: {missing}"
    return NoteClaim(
        text=text,
        citations=citations,
        status="VERIFIED" if all(checks.values()) else "UNSUPPORTED",
        checks=checks,
        reason=reason,
    )
