"""Covenant gold rows and scorer (Phase 3.4b): statements carry a status and a resolution,
the flag follows a breach actually stated whatever its resolution."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from radar.eval.flagfamily import score_family
from radar.eval.liquidity import IneligiblePassage

NEGATIVE = frozenset({"breached"})


class GoldCovenantStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    resolution: str = "none"
    evidence_quote: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    covenant_label: str | None = None
    agreement: str | None = None
    period: str | None = None


class CovenantGoldDocument(BaseModel):
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
    statements: list[GoldCovenantStatement] = Field(default_factory=list)
    ineligible: list[IneligiblePassage] = Field(default_factory=list)
    notes: str | None = None


def load_covenant_gold(path: Path) -> list[CovenantGoldDocument]:
    return [
        CovenantGoldDocument.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def score_covenant(
    gold: list[CovenantGoldDocument], output_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    return score_family(gold, output_rows, NEGATIVE, extra_fields=("resolution",))
