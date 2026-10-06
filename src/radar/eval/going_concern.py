"""Going concern gold rows and scorer (Phase 3.4c): the strictest family, the flag follows
an explicit, non-negated doubt stated for the issuer; an alleviated doubt is recorded
without a flag."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from radar.eval.flagfamily import score_family
from radar.eval.liquidity import IneligiblePassage

NEGATIVE = frozenset({"doubt"})


class GoldGoingConcernStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    evidence_quote: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    period: str | None = None


class GoingConcernGoldDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gold_id: str
    issuer_id: str
    fixture: str
    source_url: str
    document_date: date
    raw_sha256: str
    normalized_sha256: str
    normalizer_version: str
    split: str
    expected_flag: bool
    statements: list[GoldGoingConcernStatement] = Field(default_factory=list)
    ineligible: list[IneligiblePassage] = Field(default_factory=list)
    notes: str | None = None


def load_going_concern_gold(path: Path) -> list[GoingConcernGoldDocument]:
    return [
        GoingConcernGoldDocument.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def score_going_concern(
    gold: list[GoingConcernGoldDocument], output_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    return score_family(gold, output_rows, NEGATIVE)
