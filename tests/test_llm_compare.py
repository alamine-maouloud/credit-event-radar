"""Side by side reading of benchmark runs: every proposed statement falls in exactly one
category, so an unsupported claim rate is never mistaken for a hallucination rate."""

from __future__ import annotations

import json
from pathlib import Path

from radar.eval.compare import categorise_run, compare_runs, render_markdown
from radar.eval.gold import load_gold

GOLD = [
    {
        "gold_id": "G-1",
        "issuer_id": "X",
        "fixture": "f",
        "source_url": "u",
        "document_date": "2026-01-01",
        "raw_sha256": "a" * 64,
        "normalized_sha256": "b" * 64,
        "normalizer_version": "t",
        "behaviour": "guidance_maintained",
        "notes": None,
        "occurrences": [
            {
                "metric": "capex",
                "metric_label": "Organic CAPEX",
                "basis": "absolute",
                "unit": "EUR_BN",
                "current_lower": 3.4,
                "current_upper": 3.4,
                "period": "2026",
                "status": "new",
                "change_basis": "none",
                "evidence_quote": "Organic CAPEX around EUR 3.4 bn.",
                "start_offset": 0,
                "end_offset": 32,
            },
            {
                "metric": "revenue",
                "metric_label": "sales revenue",
                "basis": "yoy_change_pct",
                "unit": "PCT",
                "current_lower": -5,
                "current_upper": 5,
                "period": "2026",
                "status": "new",
                "change_basis": "none",
                "evidence_quote": "x",
                "start_offset": 40,
                "end_offset": 41,
            },
        ],
    },
    {
        "gold_id": "G-2",
        "issuer_id": "X",
        "fixture": "f",
        "source_url": "u",
        "document_date": "2026-01-02",
        "raw_sha256": "a" * 64,
        "normalized_sha256": "c" * 64,
        "normalizer_version": "t",
        "behaviour": "no_guidance",
        "notes": None,
        "occurrences": [],
    },
]


def _st(metric, label, checks, matched_quote="q", status="new", **bounds):
    all_ok = all(checks.values())
    return {
        "statement": {
            "metric": metric,
            "metric_label": label,
            "status": status,
            "period": "2026",
            "evidence_quote": matched_quote,
            "basis": "absolute",
            "unit": "EUR_BN",
            **bounds,
        },
        "validation": {
            "status": "VALID" if all_ok else "INVALID",
            "checks": checks,
            "reasons": [],
            "match_kind": "exact" if checks.get("span_match", True) else "none",
            "match_score": 100.0,
            "matched_start": 0,
            "matched_end": 1,
        },
        "change": {"change_basis": "none"},
    }


OK = {
    "span_match": True,
    "numbers_match": True,
    "unit_match": True,
    "metric_match": True,
    "numbers_attached": True,
    "entity_match": True,
    "temporal_consistency": True,
}


def _rows():
    return [
        {
            "gold_id": "G-1",
            "run_status": "ok",
            "has_guidance": True,
            "cost_usd": 0.05,
            "latency_ms": 20000,
            "statements": [
                _st(
                    "capex",
                    "Organic CAPEX",
                    OK,
                    "Organic CAPEX around EUR 3.4 bn.",
                    current_lower=3.4,
                    current_upper=3.4,
                ),
                _st(
                    "capex",
                    "Organic CAPEX for Energy",
                    OK,
                    "Organic CAPEX for Energy EUR 1.9 bn.",
                    current_lower=1.9,
                    current_upper=1.9,
                ),
                _st(
                    "revenue",
                    "sales revenue",
                    {**OK, "numbers_match": False, "numbers_attached": False},
                    "x",
                    current_lower=-5,
                    current_upper=5,
                ),
                _st("ebitda", "EBITDA", {**OK, "span_match": False}, "not in the document"),
                _st(
                    "fcf",
                    "net cash flow",
                    {**OK, "entity_match": False},
                    "Subsidiary net cash flow EUR 1 bn",
                ),
            ],
        },
        {
            "gold_id": "G-2",
            "run_status": "ok",
            "has_guidance": False,
            "cost_usd": 0.01,
            "latency_ms": 2000,
            "statements": [],
        },
    ]


def _gold(tmp_path: Path):
    p = tmp_path / "gold.jsonl"
    p.write_text("\n".join(json.dumps(g) for g in GOLD) + "\n")
    return load_gold(p)


def test_every_statement_falls_in_one_category(tmp_path):
    cat = categorise_run(_gold(tmp_path), _rows())
    assert cat["statements"] == 5
    assert cat["categories"] == {
        "supported": 1,
        "scope_violation": 1,
        "field_inconsistency": 1,
        "span_failure": 1,
        "ungrounded": 1,
    }
    assert cat["scope_violation_labels"] == ["Organic CAPEX for Energy"]
    assert cat["gold_found"] == 1 and cat["gold_total"] == 2
    assert cat["missed"] == [("G-1", "revenue", "field_inconsistency")]
    assert cat["no_guidance_false_positives"] == 0 and cat["no_guidance_documents"] == 1
    assert cat["behaviour_correct"] == 2 and cat["documents"] == 2


def test_compare_renders_the_runs_side_by_side(tmp_path):
    run_a = tmp_path / "a"
    run_a.mkdir()
    (run_a / "outputs.jsonl").write_text("\n".join(json.dumps(r) for r in _rows()) + "\n")
    (run_a / "run.json").write_text(
        json.dumps(
            {"run_id": "a", "model_id": "m-a", "total_cost_usd": 0.06, "resolved_models": ["m-a"]}
        )
    )
    gold = _gold(tmp_path)
    table = compare_runs(gold, [run_a])
    assert table["runs"][0]["run_id"] == "a" and table["runs"][0]["model_id"] == "m-a"
    assert table["runs"][0]["categories"]["scope_violation"] == 1
    md = render_markdown(table)
    assert "| Scope violations (valid quote, outside the gold scope) | 1 |" in md
    assert "Gold occurrences found | 1/2" in md
