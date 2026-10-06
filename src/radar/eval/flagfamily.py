"""Shared scorer of the flag families (liquidity, covenant): statement precision, recall and
status accuracy, negative statement precision and recall, document flag precision, recall
and false positive rate, per split and overall. Extra fields (resolution) are scored on
matched statements when asked."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
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


# ------------------------------------------------- side by side, three levels --- #


def _load_rows(run_dir: Path) -> list[dict[str, Any]]:
    source = run_dir / "outputs.jsonl"
    if not source.exists():
        source = run_dir / "results.jsonl"
    return [
        json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def compare_family_runs(
    gold: list[Any],
    run_dirs: list[Path],
    *,
    negative: frozenset[str],
    extra_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    """One entry per run: the family scores per split and the flag per document, so that a
    report can show extraction (passages found), qualification (status, resolution) and
    decision (flag, ERN-01) separately."""
    runs = []
    for d in run_dirs:
        rows = _load_rows(d)
        meta = json.loads((d / "run.json").read_text(encoding="utf-8"))
        by_id = {r["gold_id"]: r for r in rows}
        scores = score_family(gold, rows, negative, extra_fields)
        documents = []
        for g in gold:
            r = by_id.get(g.gold_id)
            predicted = None
            if r is not None and r.get("run_status") in SCORED:
                predicted = any(
                    s["validation"]["status"] == "VALID" and s["statement"]["status"] in negative
                    for s in r.get("statements", [])
                )
            documents.append(
                {
                    "gold_id": g.gold_id,
                    "split": g.split,
                    "expected": g.expected_flag,
                    "predicted": predicted,
                }
            )
        runs.append(
            {
                "run_id": meta.get("run_id", d.name),
                "model_id": meta.get("model_id"),
                "kind": meta.get("kind"),
                "cost_usd": sum(r.get("cost_usd") or 0.0 for r in rows),
                "dev": scores["dev"],
                "holdout": scores["holdout"],
                "all": scores["all"],
                "documents": documents,
            }
        )
    return {"runs": runs}


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_family_markdown(table: dict[str, Any]) -> str:
    runs = table["runs"]
    head = "| Level | " + " | ".join(f"{r['run_id']} ({r['model_id']})" for r in runs) + " |"
    sep = "|---|" + "---|" * len(runs)
    lines = [head, sep]
    for split in ("dev", "holdout"):
        if not any(r[split]["documents"] for r in runs):
            continue
        lines.append(
            f"| [{split}] documents | "
            + " | ".join(str(r[split]["documents"]) for r in runs)
            + " |"
        )
        st = [r[split]["statements"] for r in runs]
        lines.append(
            f"| [{split}] Extraction: statement recall | "
            + " | ".join(_fmt(x["recall"]) for x in st)
            + " |"
        )
        lines.append(
            f"| [{split}] Extraction: statement precision (valid) | "
            + " | ".join(_fmt(x["precision"]) for x in st)
            + " |"
        )
        lines.append(
            f"| [{split}] Qualification: status accuracy | "
            + " | ".join(_fmt(x["status_accuracy"]) for x in st)
            + " |"
        )
        for f in [k for k in st[0] if k.endswith("_accuracy") and k != "status_accuracy"]:
            name = f.removesuffix("_accuracy")
            lines.append(
                f"| [{split}] Qualification: {name} accuracy | "
                + " | ".join(_fmt(x.get(f)) for x in st)
                + " |"
            )
        ng = [r[split]["negative_statements"] for r in runs]
        lines.append(
            f"| [{split}] Qualification: negative statement precision | "
            + " | ".join(_fmt(x["precision"]) for x in ng)
            + " |"
        )
        lines.append(
            f"| [{split}] Qualification: negative statement recall | "
            + " | ".join(_fmt(x["recall"]) for x in ng)
            + " |"
        )
        df = [r[split]["document_flag"] for r in runs]
        lines.append(
            f"| [{split}] Decision: document flag precision | "
            + " | ".join(_fmt(x["precision"]) for x in df)
            + " |"
        )
        lines.append(
            f"| [{split}] Decision: document flag recall | "
            + " | ".join(_fmt(x["recall"]) for x in df)
            + " |"
        )
        lines.append(
            f"| [{split}] Decision: false flag rate on documents without flag | "
            + " | ".join(
                f"{_fmt(x['false_positive_rate_on_negative_docs'])} (of {x['negative_docs']})"
                for x in df
            )
            + " |"
        )
    lines.append("| Cost USD | " + " | ".join(f"{r['cost_usd']:.2f}" for r in runs) + " |")
    lines.append("")
    lines.append("| Document | split | expected | " + " | ".join(r["run_id"] for r in runs) + " |")
    lines.append("|---|---|---|" + "---|" * len(runs))
    for i, d in enumerate(runs[0]["documents"]):
        cells = []
        for r in runs:
            p = r["documents"][i]["predicted"]
            cells.append("not run" if p is None else ("FLAG" if p else "NO FLAG"))
        lines.append(
            f"| {d['gold_id']} | {d['split']} | {'FLAG' if d['expected'] else 'NO FLAG'} | "
            + " | ".join(cells)
            + " |"
        )
    return "\n".join(lines) + "\n"
