"""Covenant gold set tooling: rows with status and resolution, the family scorer with
resolution accuracy, dev and holdout reported separately."""

from __future__ import annotations

import json
from pathlib import Path

from radar.eval.covenant import CovenantGoldDocument, load_covenant_gold, score_covenant

BREACH = "The Company was not in compliance with the minimum net worth covenant, and the lender waived this covenant violation."
COMPL = "As of June 30, 2026, we were in compliance with all covenants."


def _gold(tmp_path: Path):
    rows = [
        {
            "gold_id": "C-1",
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
            "ineligible": [],
            "statements": [
                {
                    "status": "breached",
                    "resolution": "waived",
                    "evidence_quote": BREACH,
                    "start_offset": 10,
                    "end_offset": 10 + len(BREACH),
                },
                {
                    "status": "compliant",
                    "resolution": "none",
                    "evidence_quote": COMPL,
                    "start_offset": 200,
                    "end_offset": 200 + len(COMPL),
                },
            ],
        },
        {
            "gold_id": "C-2",
            "issuer_id": "X",
            "fixture": "f",
            "source_url": "u",
            "document_date": "2026-05-12",
            "raw_sha256": "a" * 64,
            "normalized_sha256": "c" * 64,
            "normalizer_version": "t",
            "split": "holdout",
            "expected_flag": False,
            "notes": None,
            "statements": [],
            "ineligible": [],
        },
    ]
    p = tmp_path / "gold.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return load_covenant_gold(p)


def _st(status, resolution, start, end, valid=True):
    return {
        "statement": {
            "risk_type": "covenant",
            "status": status,
            "resolution": resolution,
            "evidence_quote": "q",
            "start_offset": start,
            "end_offset": end,
        },
        "validation": {
            "status": "VALID" if valid else "INVALID",
            "checks": {},
            "reasons": [] if valid else ["STATUS_MISMATCH: x"],
            "match_kind": "exact",
            "match_score": 100.0,
            "matched_start": start,
            "matched_end": end,
        },
        "change": {},
    }


def test_rows_carry_status_and_resolution(tmp_path):
    gold = _gold(tmp_path)
    assert isinstance(gold[0], CovenantGoldDocument)
    assert gold[0].statements[0].resolution == "waived" and gold[0].expected_flag is True
    assert gold[1].split == "holdout"


def test_scorer_adds_resolution_accuracy_and_breach_metrics(tmp_path):
    gold = _gold(tmp_path)
    rows = [
        {
            "gold_id": "C-1",
            "run_status": "ok",
            "has_covenant_statements": True,
            "statements": [
                _st("breached", "cured", 12, 60),
                _st("compliant", "none", 200, 240),
                _st("breached", "none", 400, 420, valid=False),
            ],
        },
        {"gold_id": "C-2", "run_status": "ok", "has_covenant_statements": False, "statements": []},
    ]
    m = score_covenant(gold, rows)
    dev = m["dev"]
    assert dev["statements"]["matched"] == 2 and dev["statements"]["status_accuracy"] == 1.0
    assert dev["statements"]["resolution_accuracy"] == 0.5  # waived read as cured
    assert dev["negative_statements"] == {
        "gold": 1,
        "predicted_valid": 1,
        "matched": 1,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert dev["document_flag"]["true_positives"] == 1 and dev["document_flag"]["precision"] == 1.0
    assert dev["rejections"] == {"STATUS_MISMATCH": 1}
    assert m["holdout"]["document_flag"]["false_positive_rate_on_negative_docs"] == 0.0
