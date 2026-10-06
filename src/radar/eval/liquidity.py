"""Liquidity gold rows and scorer (Phase 3.4a).

A gold document lists the issuer's own liquidity statements with their status and exact
span, the passages that look like statements but are ineligible (a third party speaking,
a segment), and the flag the code must set. Scores are given per split (dev, holdout) and
overall, at statement level and at document level.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from radar.eval.flagfamily import score_family

NEGATIVE = {"deteriorated", "concern"}


class GoldLiquidityStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    evidence_quote: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    metric_label: str | None = None
    value: float | None = None
    unit: str | None = None
    period: str | None = None


class IneligiblePassage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str  # third_party | segment | not_liquidity
    evidence_quote: str
    start_offset: int | None = None
    end_offset: int | None = None


class LiquidityGoldDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gold_id: str
    issuer_id: str
    fixture: str
    source_url: str
    document_date: date
    raw_sha256: str
    normalized_sha256: str
    normalizer_version: str
    split: str  # dev | holdout
    expected_flag: bool
    statements: list[GoldLiquidityStatement] = Field(default_factory=list)
    ineligible: list[IneligiblePassage] = Field(default_factory=list)
    notes: str | None = None


def load_liquidity_gold(path: Path) -> list[LiquidityGoldDocument]:
    return [
        LiquidityGoldDocument.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def score_liquidity(
    gold: list[LiquidityGoldDocument], output_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    return score_family(gold, output_rows, frozenset(NEGATIVE))
