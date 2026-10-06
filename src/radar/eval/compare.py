"""Side by side reading of benchmark runs.

The unsupported claim rate of metrics.py mixes very different things. Here every proposed
statement falls in exactly one category:

- supported: validated and matched to a gold occurrence;
- scope_violation: validated (faithful quote, numbers in the span) but matching no gold
  occurrence, typically a segment figure the guide excludes;
- span_failure: the quote is not in the document;
- ungrounded: the quote exists but is about another entity or an impossible date;
- field_inconsistency: the quote exists but the numbers, unit or metric are not supported by it;
- scope_rejected: a segment level statement refused by the deterministic scope guard.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from radar.eval.gold import GoldDocument
from radar.eval.metrics import SCORED, _match, predicted_behaviour

CATEGORIES = (
    "supported",
    "scope_violation",
    "scope_rejected",
    "field_inconsistency",
    "span_failure",
    "ungrounded",
)


def categorise_statement(entry: dict[str, Any], matched: bool) -> str:
    checks = entry["validation"].get("checks", {})
    if entry["validation"]["status"] == "VALID":
        return "supported" if matched else "scope_violation"
    if not checks.get("scope_match", True):
        return "scope_rejected"
    if not checks.get("span_match", True):
        return "span_failure"
    if not checks.get("entity_match", True) or not checks.get("temporal_consistency", True):
        return "ungrounded"
    return "field_inconsistency"


def categorise_run(gold_rows: list[GoldDocument], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {g.gold_id: g for g in gold_rows}
    counts: Counter = Counter()
    scope_labels: list[str] = []
    missed: list[tuple[str, str, str]] = []
    gold_total = gold_found = behaviour_correct = documents = 0
    no_guidance_docs = no_guidance_fp = 0
    for r in rows:
        g = by_id.get(r["gold_id"])
        if g is None or r.get("run_status") not in SCORED:
            continue
        documents += 1
        gold_total += len(g.occurrences)
        behaviour_correct += predicted_behaviour(r) == g.behaviour
        statements = r.get("statements", [])
        valid = [s for s in statements if s["validation"]["status"] == "VALID"]
        pairs = _match(g.occurrences, valid)
        matched_entries = {id(e) for _, e in pairs}
        matched_gold = {id(o) for o, _ in pairs}
        gold_found += len(pairs)
        for s in statements:
            category = categorise_statement(s, id(s) in matched_entries)
            counts[category] += 1
            if category == "scope_violation":
                scope_labels.append(s["statement"]["metric_label"])
        for o in g.occurrences:
            if id(o) in matched_gold:
                continue
            proposals = [
                categorise_statement(s, False)
                for s in statements
                if s["statement"]["metric"] == o.metric and s["validation"]["status"] != "VALID"
            ]
            missed.append((r["gold_id"], o.metric, proposals[0] if proposals else "not_proposed"))
        if g.behaviour == "no_guidance":
            no_guidance_docs += 1
            no_guidance_fp += bool(statements) or bool(r.get("has_guidance"))
    return {
        "documents": documents,
        "behaviour_correct": behaviour_correct,
        "statements": sum(counts.values()),
        "categories": {c: counts.get(c, 0) for c in CATEGORIES},
        "scope_violation_labels": scope_labels,
        "gold_total": gold_total,
        "gold_found": gold_found,
        "missed": missed,
        "no_guidance_documents": no_guidance_docs,
        "no_guidance_false_positives": no_guidance_fp,
    }


def _load_rows(run_dir: Path) -> list[dict[str, Any]]:
    source = run_dir / "outputs.jsonl"
    if not source.exists():
        source = run_dir / "results.jsonl"
    return [
        json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def compare_runs(gold_rows: list[GoldDocument], run_dirs: list[Path]) -> dict[str, Any]:
    runs = []
    for d in run_dirs:
        rows = _load_rows(d)
        meta = json.loads((d / "run.json").read_text(encoding="utf-8"))
        cat = categorise_run(gold_rows, rows)
        scored = [r for r in rows if r.get("run_status") in SCORED]
        real = [r for r in rows if r.get("run_status") == "ok"]
        latencies = [r.get("latency_ms") or 0 for r in real]
        runs.append(
            {
                "run_id": meta.get("run_id", d.name),
                "model_id": meta.get("model_id"),
                "resolved_models": meta.get("resolved_models"),
                "cost_usd": sum(r.get("cost_usd") or 0.0 for r in scored),
                "mean_latency_ms": sum(latencies) / len(latencies) if latencies else None,
                **cat,
            }
        )
    return {"runs": runs}


def _ratio(num: int, den: int) -> str:
    return "n/a" if den == 0 else f"{num / den:.3f}"


def render_markdown(table: dict[str, Any]) -> str:
    runs = table["runs"]
    head = "| Metric | " + " | ".join(f"{r['run_id']} ({r['model_id']})" for r in runs) + " |"
    sep = "|---|" + "---|" * len(runs)
    valid = [r["categories"]["supported"] + r["categories"]["scope_violation"] for r in runs]
    lines = [
        head,
        sep,
        "| Gold occurrences found | "
        + " | ".join(f"{r['gold_found']}/{r['gold_total']}" for r in runs)
        + " |",
        "| Recall (occurrences) | "
        + " | ".join(_ratio(r["gold_found"], r["gold_total"]) for r in runs)
        + " |",
        "| Precision on validated statements | "
        + " | ".join(
            _ratio(r["categories"]["supported"], v) for r, v in zip(runs, valid, strict=True)
        )
        + " |",
        "| Precision on all statements | "
        + " | ".join(_ratio(r["categories"]["supported"], r["statements"]) for r in runs)
        + " |",
        "| Documents with the correct behaviour | "
        + " | ".join(f"{r['behaviour_correct']}/{r['documents']}" for r in runs)
        + " |",
        "| No-guidance documents with a false guidance | "
        + " | ".join(
            f"{r['no_guidance_false_positives']}/{r['no_guidance_documents']}" for r in runs
        )
        + " |",
        "| Statements proposed | " + " | ".join(str(r["statements"]) for r in runs) + " |",
        "| Supported (validated and in the gold) | "
        + " | ".join(str(r["categories"]["supported"]) for r in runs)
        + " |",
        "| Scope violations (valid quote, outside the gold scope) | "
        + " | ".join(str(r["categories"]["scope_violation"]) for r in runs)
        + " |",
        "| Scope violations rejected by the guard | "
        + " | ".join(str(r["categories"]["scope_rejected"]) for r in runs)
        + " |",
        "| Field inconsistencies (numbers, unit or metric not in the quote) | "
        + " | ".join(str(r["categories"]["field_inconsistency"]) for r in runs)
        + " |",
        "| Span failures (quote not in the document) | "
        + " | ".join(str(r["categories"]["span_failure"]) for r in runs)
        + " |",
        "| Ungrounded (other entity or impossible date) | "
        + " | ".join(str(r["categories"]["ungrounded"]) for r in runs)
        + " |",
        "| Cost USD | " + " | ".join(f"{r['cost_usd']:.2f}" for r in runs) + " |",
        "| Mean latency s | "
        + " | ".join(
            "n/a" if r["mean_latency_ms"] is None else f"{r['mean_latency_ms'] / 1000:.1f}"
            for r in runs
        )
        + " |",
    ]
    return "\n".join(lines) + "\n"
