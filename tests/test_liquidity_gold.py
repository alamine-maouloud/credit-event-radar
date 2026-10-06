"""Liquidity gold set tooling: rows, scorer at statement and document level, dev and
holdout reported separately."""

from __future__ import annotations

import json
from pathlib import Path

from radar.eval.liquidity import LiquidityGoldDocument, load_liquidity_gold, score_liquidity

NEG = "Liquidity has become constrained."
POS = "Our liquidity position remains strong."
GEN = "Liquidity risk management is described in the risk report."


def _gold(tmp_path: Path):
    rows = [
        {
            "gold_id": "L-1",
            "issuer_id": "X",
            "fixture": "f",
            "source_url": "u",
            "document_date": "2026-05-11",
            "raw_sha256": "a" * 64,
            "normalized_sha256": "b" * 64,
            "normalizer_version": "t",
            "split": "dev",
            "expected_flag": True,
            "notes": None,
            "statements": [
                {
                    "status": "concern",
                    "evidence_quote": NEG,
                    "start_offset": 10,
                    "end_offset": 10 + len(NEG),
                },
                {
                    "status": "stable",
                    "evidence_quote": POS,
                    "start_offset": 60,
                    "end_offset": 60 + len(POS),
                },
            ],
            "ineligible": [],
        },
        {
            "gold_id": "L-2",
            "issuer_id": "X",
            "fixture": "f",
            "source_url": "u",
            "document_date": "2026-05-12",
            "raw_sha256": "a" * 64,
            "normalized_sha256": "c" * 64,
            "normalizer_version": "t",
            "split": "dev",
            "expected_flag": False,
            "notes": None,
            "statements": [
                {
                    "status": "mentioned",
                    "evidence_quote": GEN,
                    "start_offset": 5,
                    "end_offset": 5 + len(GEN),
                }
            ],
            "ineligible": [],
        },
        {
            "gold_id": "L-3",
            "issuer_id": "X",
            "fixture": "f",
            "source_url": "u",
            "document_date": "2026-05-13",
            "raw_sha256": "a" * 64,
            "normalized_sha256": "d" * 64,
            "normalizer_version": "t",
            "split": "holdout",
            "expected_flag": False,
            "notes": None,
            "statements": [],
            "ineligible": [
                {
                    "reason": "third_party",
                    "evidence_quote": "We assess the group's liquidity as strong.",
                    "start_offset": 0,
                    "end_offset": 44,
                }
            ],
        },
    ]
    p = tmp_path / "gold.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return load_liquidity_gold(p)


def _st(status, start, end, valid=True, reason=None):
    return {
        "statement": {
            "risk_type": "liquidity",
            "status": status,
            "evidence_quote": "q",
            "start_offset": start,
            "end_offset": end,
        },
        "validation": {
            "status": "VALID" if valid else "INVALID",
            "checks": {},
            "reasons": [reason] if reason else [],
            "match_kind": "exact",
            "match_score": 100.0,
            "matched_start": start,
            "matched_end": end,
        },
        "change": {},
    }


def test_rows_load_with_split_and_expected_flag(tmp_path):
    gold = _gold(tmp_path)
    assert [g.split for g in gold] == ["dev", "dev", "holdout"]
    assert gold[0].expected_flag is True and len(gold[0].statements) == 2
    assert gold[2].ineligible[0].reason == "third_party"
    assert isinstance(gold[0], LiquidityGoldDocument)


def test_scorer_reports_statement_and_document_levels_per_split(tmp_path):
    gold = _gold(tmp_path)
    rows = [
        # L-1: the negative found (overlapping span, right status), the positive found with a wrong status,
        # plus one invalid extra statement
        {
            "gold_id": "L-1",
            "run_status": "ok",
            "has_liquidity_statements": True,
            "statements": [
                _st("concern", 12, 40),
                _st("improved", 60, 98),
                _st("concern", 200, 210, valid=False, reason="POLARITY_MISMATCH: x"),
            ],
        },
        # L-2: the generic mention read as concern but rejected by the validator, nothing valid
        {
            "gold_id": "L-2",
            "run_status": "ok",
            "has_liquidity_statements": True,
            "statements": [_st("concern", 5, 60, valid=False, reason="POLARITY_MISMATCH: y")],
        },
        # L-3 (holdout): the third party sentence proposed as stable: valid for the validator, but no gold statement
        {
            "gold_id": "L-3",
            "run_status": "ok",
            "has_liquidity_statements": True,
            "statements": [_st("stable", 0, 40)],
        },
    ]
    m = score_liquidity(gold, rows)
    dev, hold = m["dev"], m["holdout"]
    assert dev["documents"] == 2 and hold["documents"] == 1
    s = dev["statements"]
    assert (
        s["gold"] == 3 and s["predicted"] == 4 and s["predicted_valid"] == 2 and s["matched"] == 2
    )
    assert s["precision"] == 1.0 and s["recall"] == 2 / 3
    assert s["status_accuracy"] == 0.5  # concern right, stable read as improved
    n = dev["negative_statements"]
    assert n == {"gold": 1, "predicted_valid": 1, "matched": 1, "precision": 1.0, "recall": 1.0}
    d = dev["document_flag"]
    assert d == {
        "gold_true": 1,
        "predicted_true": 1,
        "true_positives": 1,
        "precision": 1.0,
        "recall": 1.0,
        "false_positive_rate_on_negative_docs": 0.0,
        "negative_docs": 1,
    }
    assert dev["rejections"] == {"POLARITY_MISMATCH": 2}
    assert hold["statements"]["gold"] == 0 and hold["statements"]["predicted_valid"] == 1
    assert hold["statements"]["precision"] == 0.0 and hold["ineligible_proposed"] == 1
    assert hold["document_flag"]["false_positive_rate_on_negative_docs"] == 0.0
    assert m["all"]["documents"] == 3
