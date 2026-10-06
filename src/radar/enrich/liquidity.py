"""Liquidity flag enrichment (Phase 3.4a): a thin wrapper over the generic flag family."""

from __future__ import annotations

from typing import Any

from radar.enrich.flags import FlagEnrichment, build_flag_enrichment

LiquidityEnrichment = FlagEnrichment


def build_liquidity_enrichment(rows: list[dict[str, Any]]) -> FlagEnrichment:
    return build_flag_enrichment(rows, "liquidity")
