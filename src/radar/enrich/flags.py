"""Flag families (liquidity, covenant): from validated statements to one flag decision, in
code only. The model qualifies each passage, the validator has rejected what the words do
not support, and here the flag follows one rule: at least one VALID statement with a
negative status at Group level. Every statement is kept for the audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FAMILIES: dict[str, dict[str, Any]] = {
    "liquidity": {"flag": "liquidity", "negative": frozenset({"deteriorated", "concern"})},
    "covenant": {"flag": "covenant", "negative": frozenset({"breached"})},
}


@dataclass
class FlagEnrichment:
    family: str
    flag_name: str
    statements: list[dict[str, Any]]
    flag: bool
    negative_ids: list[str]
    statuses: list[str] = field(default_factory=list)


def build_flag_enrichment(rows: list[dict[str, Any]], family: str) -> FlagEnrichment:
    spec = FAMILIES[family]
    valid = sorted(
        (r for r in rows if r.get("validation_status", "VALID") == "VALID"),
        key=lambda r: r["statement_id"],
    )
    statements, negative = [], []
    for r in valid:
        st = dict(r["statement_json"])
        for field_name in (r.get("validation_json") or {}).get("salvaged", {}):
            st[field_name] = None  # dropped by the validator, never shown as the model's claim
        summary = {
            "statement_id": r["statement_id"],
            "status": st.get("status"),
            "period": st.get("period"),
        }
        if family == "liquidity":
            summary |= {
                "metric_label": st.get("metric_label"),
                "value": st.get("value"),
                "unit": st.get("unit"),
            }
        else:
            summary |= {
                "resolution": st.get("resolution", "none"),
                "covenant_label": st.get("covenant_label"),
                "agreement": st.get("agreement"),
            }
        statements.append(summary)
        if st.get("status") in spec["negative"]:
            negative.append(r["statement_id"])
    return FlagEnrichment(
        family=family,
        flag_name=spec["flag"],
        statements=statements,
        flag=bool(negative),
        negative_ids=negative,
        statuses=[s["status"] for s in statements],
    )
