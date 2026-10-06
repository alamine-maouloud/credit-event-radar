"""From validated liquidity statements to one flag decision, in code only (Phase 3.4a).

The model qualifies each passage (deteriorated, concern, stable, improved, mentioned) and
the validator has already rejected any negative status the passage does not support. Here
the flag follows one rule: at least one VALID statement with a negative status at Group
level. stable, improved and mentioned never set anything; every statement is kept for the
audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from radar.llm.validate import NEGATIVE_LIQUIDITY_STATUSES


@dataclass
class LiquidityEnrichment:
    statements: list[dict[str, Any]]
    flag: bool
    negative_ids: list[str]
    statuses: list[str] = field(default_factory=list)


def build_liquidity_enrichment(rows: list[dict[str, Any]]) -> LiquidityEnrichment:
    valid = sorted(
        (r for r in rows if r.get("validation_status", "VALID") == "VALID"),
        key=lambda r: r["statement_id"],
    )
    statements = []
    negative = []
    for r in valid:
        st = r["statement_json"]
        statements.append(
            {
                "statement_id": r["statement_id"],
                "status": st.get("status"),
                "metric_label": st.get("metric_label"),
                "value": st.get("value"),
                "unit": st.get("unit"),
                "period": st.get("period"),
            }
        )
        if st.get("status") in NEGATIVE_LIQUIDITY_STATUSES:
            negative.append(r["statement_id"])
    return LiquidityEnrichment(
        statements=statements,
        flag=bool(negative),
        negative_ids=negative,
        statuses=[s["status"] for s in statements],
    )
