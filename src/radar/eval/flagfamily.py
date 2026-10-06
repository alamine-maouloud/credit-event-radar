"""Shared scorer of the flag families (liquidity, covenant): statement precision, recall and
status accuracy, negative statement precision and recall, document flag precision, recall
and false positive rate, per split and overall. Extra fields (resolution) are scored on
matched statements when asked."""

from __future__ import annotations

from collections import Counter
from typing import Any

SCORED = {"ok", "cached"}


def _overlap(a0: int | None, a1: int | None, b0: int, b1: int) -> bool:
    return a0 is not None and a1 is not None and a0 < b1 and b0 < a1


def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def score_split(
    gold: list[Any],
    rows: dict[str, dict],
    negative: frozenset[str],
    extra_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    n_gold = n_pred = n_valid = n_matched = status_ok = 0
    extra_ok: Counter = Counter()
    neg_gold = neg_valid = neg_matched = 0
    flag_gold = flag_pred = flag_tp = neg_docs = neg_docs_fp = 0
    ineligible_proposed = 0
    rejections: Counter = Counter()
    scored_docs = 0
    for g in gold:
        r = rows.get(g.gold_id)
        n_gold += len(g.statements)
        neg_gold += sum(1 for s in g.statements if s.status in negative)
        if r is None or r.get("run_status") not in SCORED:
            continue
        scored_docs += 1
        statements = r.get("statements", [])
        n_pred += len(statements)
        valid = [s for s in statements if s["validation"]["status"] == "VALID"]
        n_valid += len(valid)
        for s in statements:
            if s["validation"]["status"] != "VALID":
                for reason in s["validation"].get("reasons", []):
                    rejections[reason.split(":", 1)[0].strip()] += 1
        used: set[int] = set()
        for gs in g.statements:
            for i, ps in enumerate(valid):
                v = ps["validation"]
                same_text = ps["statement"].get("evidence_quote") == gs.evidence_quote
                if i in used or not (
                    same_text
                    or _overlap(
                        v.get("matched_start"), v.get("matched_end"), gs.start_offset, gs.end_offset
                    )
                ):
                    continue
                used.add(i)
                n_matched += 1
                status_ok += ps["statement"]["status"] == gs.status
                for f in extra_fields:
                    extra_ok[f] += ps["statement"].get(f) == getattr(gs, f)
                if gs.status in negative and ps["statement"]["status"] in negative:
                    neg_matched += 1
                break
        neg_valid += sum(1 for s in valid if s["statement"]["status"] in negative)
        for i, ps in enumerate(valid):
            if i in used:
                continue
            v = ps["validation"]
            if any(
                _overlap(v.get("matched_start"), v.get("matched_end"), p.start_offset, p.end_offset)
                for p in g.ineligible
                if p.start_offset is not None and p.end_offset is not None
            ):
                ineligible_proposed += 1
        predicted_flag = any(s["statement"]["status"] in negative for s in valid)
        flag_gold += g.expected_flag
        flag_pred += predicted_flag
        flag_tp += g.expected_flag and predicted_flag
        if not g.expected_flag:
            neg_docs += 1
            neg_docs_fp += predicted_flag
    statements: dict[str, Any] = {
        "gold": n_gold,
        "predicted": n_pred,
        "predicted_valid": n_valid,
        "matched": n_matched,
        "precision": _rate(n_matched, n_valid),
        "recall": _rate(n_matched, n_gold),
        "status_accuracy": _rate(status_ok, n_matched),
    }
    for f in extra_fields:
        statements[f"{f}_accuracy"] = _rate(extra_ok[f], n_matched)
    return {
        "documents": scored_docs,
        "statements": statements,
        "negative_statements": {
            "gold": neg_gold,
            "predicted_valid": neg_valid,
            "matched": neg_matched,
            "precision": _rate(neg_matched, neg_valid),
            "recall": _rate(neg_matched, neg_gold),
        },
        "document_flag": {
            "gold_true": flag_gold,
            "predicted_true": flag_pred,
            "true_positives": flag_tp,
            "precision": _rate(flag_tp, flag_pred),
            "recall": _rate(flag_tp, flag_gold),
            "false_positive_rate_on_negative_docs": _rate(neg_docs_fp, neg_docs),
            "negative_docs": neg_docs,
        },
        "ineligible_proposed": ineligible_proposed,
        "rejections": dict(rejections),
    }


def score_family(
    gold: list[Any],
    output_rows: list[dict[str, Any]],
    negative: frozenset[str],
    extra_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    rows = {r["gold_id"]: r for r in output_rows}
    out: dict[str, Any] = {}
    for split in ("dev", "holdout"):
        out[split] = score_split(
            [g for g in gold if g.split == split], rows, negative, extra_fields
        )
    out["all"] = score_split(gold, rows, negative, extra_fields)
    out["cost"] = {
        "total_usd": sum(r.get("cost_usd") or 0.0 for r in output_rows),
        "input_tokens": sum(r.get("input_tokens") or 0 for r in output_rows),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in output_rows),
    }
    return out
