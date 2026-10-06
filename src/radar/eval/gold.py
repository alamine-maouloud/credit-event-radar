"""Frozen gold rows (eval/gold/*.jsonl) and the documents they point to."""

from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from radar.config import Universe
from radar.connectors.fixture import FixtureAdapter
from radar.models import RawDocument
from radar.normalize import SecHtmlNormalizer

Behaviour = (
    str  # guidance_changed | guidance_maintained | guidance_mentioned_not_actionable | no_guidance
)


class GoldMismatch(RuntimeError):
    """The document bytes on disk do not match the frozen gold hashes."""


class GoldOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    metric_label: str
    scope: str = "group"
    basis: str
    unit: str
    previous_lower: float | None = None
    previous_upper: float | None = None
    current_lower: float | None = None
    current_upper: float | None = None
    period: str | None = None
    status: str
    change_basis: str = "none"
    evidence_quote: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)


class GoldDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gold_id: str
    issuer_id: str
    fixture: str
    source_url: str
    document_date: date
    raw_sha256: str
    normalized_sha256: str
    normalizer_version: str
    behaviour: Behaviour
    occurrences: list[GoldOccurrence] = Field(default_factory=list)
    notes: str | None = None


def load_gold(path: Path) -> list[GoldDocument]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(GoldDocument.model_validate(json.loads(line)))
    return rows


def load_gold_document(row: GoldDocument, universe: Universe, root: Path) -> RawDocument:
    """Rebuild the document from the private fixture bytes with the current normalisers and
    refuse it when its hashes differ from the frozen ones (the gold would not apply)."""
    directory = root / row.fixture
    with tempfile.TemporaryDirectory() as tmp:
        docs = FixtureAdapter(directory, Path(tmp), SecHtmlNormalizer()).fetch(
            date(2000, 1, 1), universe.issuers
        )
    if not docs:
        raise GoldMismatch(f"{row.gold_id}: no document could be built from {row.fixture}")
    doc = docs[0]
    if doc.content_hash != row.raw_sha256 or doc.doc_id != row.normalized_sha256:
        raise GoldMismatch(
            f"{row.gold_id}: document hashes differ from the frozen gold "
            f"(raw {doc.content_hash[:12]} vs {row.raw_sha256[:12]}, "
            f"normalised {doc.doc_id[:12]} vs {row.normalized_sha256[:12]})"
        )
    return doc
