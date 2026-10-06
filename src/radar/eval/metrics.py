"""Scores of one benchmark run against the gold rows (docs/SPEC.md 15.3).

Precision and recall of occurrences are matched on (metric, period) inside each document,
only statements that passed the two level validator count as predictions for recall; the
unsupported claim rate counts every statement that is invalid or matches no gold occurrence.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from statistics import mean, median
from typing import Any

from radar.eval.gold import GoldDocument, GoldOccurrence

SCORED = {"ok", "cached"}
CHANGING = {"raised", "cut", "withdrawn"}
STATING = {"new", "reaffirmed"}
FIELDS = (
    "status",
    "basis",
    "unit",
    "previous_lower",
    "previous_upper",
    "current_lower",
    "current_upper",
    "change_basis",
    "metric_label",
)


def predicted_behaviour(row: dict[str, Any]) -> str | None:
    """Document behaviour implied by the validated statements, mirroring the guide: a
    validated change wins, then validated figures, then any mention (even invalid)."""
    if row.get("run_status") not in SCORED:
        return None
    valid = [
        s["statement"]["status"]
        for s in row.get("statements", [])
        if s["validation"]["status"] == "VALID"
    ]
    if any(v in CHANGING for v in valid):
        return "guidance_changed"
    if any(v in STATING for v in valid):
        return "guidance_maintained"
    if row.get("has_guidance") or row.get("statements"):
        return "guidance_mentioned_not_actionable"
    return "no_guidance"


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(float(a) - float(b)) < 1e-9
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold().strip() == b.casefold().strip()
    return a == b


def _predicted_value(entry: dict[str, Any], field: str) -> Any:
    if field == "change_basis":
        return (entry.get("change") or {}).get("change_basis")
    return entry["statement"].get(field)


def _match(
    gold: list[GoldOccurrence], valid: list[dict[str, Any]]
) -> list[tuple[GoldOccurrence, dict]]:
    pairs = []
    used: set[int] = set()
    for occ in gold:
        for i, entry in enumerate(valid):
            st = entry["statement"]
            if i in used or st["metric"] != occ.metric:
                continue
            if _same(st.get("period"), occ.period):
                used.add(i)
                pairs.append((occ, entry))
                break
    return pairs


def _quote_matches(entry: dict[str, Any], occ: GoldOccurrence) -> bool:
    """Exact quote agreement, also on sanitised rows that carry only the hash."""
    st = entry["statement"]
    if "evidence_quote" in st:
        return st["evidence_quote"] == occ.evidence_quote
    digest = hashlib.sha256(occ.evidence_quote.encode("utf-8")).hexdigest()
    return st.get("evidence_sha256") == digest


def _overlaps(entry: dict[str, Any], occ: GoldOccurrence) -> bool:
    v = entry["validation"]
    a, b = v.get("matched_start"), v.get("matched_end")
    if a is None or b is None:
        return False
    return a < occ.end_offset and occ.start_offset < b


def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def score(gold_rows: list[GoldDocument], output_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {g.gold_id: g for g in gold_rows}
    rows = [r for r in output_rows if r["gold_id"] in by_id]
    statuses = Counter(r["run_status"] for r in rows)

    confusion: dict[str, Counter] = defaultdict(Counter)
    correct = scored = 0
    n_gold = n_pred = n_valid = n_matched = n_invalid = n_unmatched_valid = 0
    agreement: Counter = Counter()
    span_exact = span_overlap = 0
    on_no_guidance = docs_false_guidance = 0
    for r in rows:
        g = by_id[r["gold_id"]]
        n_gold += len(g.occurrences)
        if r["run_status"] not in SCORED:
            continue
        scored += 1
        pred = predicted_behaviour(r)
        confusion[g.behaviour][pred] += 1
        correct += pred == g.behaviour
        statements = r.get("statements", [])
        n_pred += len(statements)
        valid = [s for s in statements if s["validation"]["status"] == "VALID"]
        n_valid += len(valid)
        n_invalid += len(statements) - len(valid)
        pairs = _match(g.occurrences, valid)
        n_matched += len(pairs)
        n_unmatched_valid += len(valid) - len(pairs)
        for occ, entry in pairs:
            for f in FIELDS:
                agreement[f] += _same(_predicted_value(entry, f), getattr(occ, f))
            span_exact += _quote_matches(entry, occ)
            span_overlap += _overlaps(entry, occ)
        if g.behaviour == "no_guidance":
            on_no_guidance += len(statements)
            docs_false_guidance += bool(statements) or bool(r.get("has_guidance"))

    precision_valid = _rate(n_matched, n_valid)
    recall = _rate(n_matched, n_gold)
    f1 = (
        None
        if not precision_valid or not recall
        else 2 * precision_valid * recall / (precision_valid + recall)
    )
    ok_rows = [r for r in rows if r["run_status"] == "ok"]
    latencies = [r.get("latency_ms") or 0 for r in ok_rows]
    costs = [r.get("cost_usd") or 0.0 for r in rows if r["run_status"] in SCORED]
    return {
        "documents": {
            "n": len(rows),
            "ok": statuses.get("ok", 0),
            "cached": statuses.get("cached", 0),
            "json_failures": statuses.get("schema_failure", 0),
            "truncated": statuses.get("truncated", 0),
            "budget_refused": statuses.get("budget_refused", 0),
            "provider_errors": statuses.get("provider_error", 0),
            "dry_run": statuses.get("dry_run", 0),
        },
        "behaviour": {
            "n_scored": scored,
            "accuracy": _rate(correct, scored),
            "confusion": {k: dict(v) for k, v in confusion.items()},
        },
        "occurrences": {
            "gold": n_gold,
            "predicted": n_pred,
            "predicted_valid": n_valid,
            "matched": n_matched,
            "precision_valid": precision_valid,
            "precision_all": _rate(n_matched, n_pred),
            "recall": recall,
            "f1": f1,
            "field_agreement": {f: _rate(agreement[f], n_matched) for f in FIELDS},
            "span_exact_rate": _rate(span_exact, n_matched),
            "span_overlap_rate": _rate(span_overlap, n_matched),
        },
        "claims": {
            "statements": n_pred,
            "invalid": n_invalid,
            "invalid_span_rate": _rate(n_invalid, n_pred),
            "unsupported": n_invalid + n_unmatched_valid,
            "unsupported_claim_rate": _rate(n_invalid + n_unmatched_valid, n_pred),
            "on_no_guidance_documents": on_no_guidance,
            "no_guidance_documents_with_guidance": docs_false_guidance,
        },
        "cost": {
            "total_usd": sum(costs),
            "per_document_usd": _rate(1, 1) and (sum(costs) / scored if scored else None),
            "input_tokens": sum(r.get("input_tokens") or 0 for r in rows),
            "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
        },
        "latency_ms": {
            "mean": mean(latencies) if latencies else None,
            "median": median(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
    }
