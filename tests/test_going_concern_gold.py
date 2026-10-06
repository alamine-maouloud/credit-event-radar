"""Going concern gold set tooling: rows with four statuses, the family scorer with doubt as
the only negative status, dev and holdout reported separately."""

from __future__ import annotations

import json
from pathlib import Path

from radar.eval.going_concern import (
    NEGATIVE,
    GoingConcernGoldDocument,
    load_going_concern_gold,
    score_going_concern,
)

DOUBT = "These conditions raise substantial doubt about the Company's ability to continue as a going concern."
BASIS = "The financial statements have been prepared on a going concern basis."


def _gold(tmp_path: Path):
    rows = [
        {
            "gold_id": "G-1", "issuer_id": "X", "fixture": "f", "source_url": "u",
            "document_date": "2026-05-11", "raw_sha256": "a" * 64, "normalized_sha256": "b" * 64,
            "normalizer_version": "t", "split": "dev", "expected_flag": True, "notes": None,
            "ineligible": [],
            "statements": [
                {"status": "doubt", "evidence_quote": DOUBT, "start_offset": 10, "end_offset": 10 + len(DOUBT)},
                {"status": "mentioned", "evidence_quote": BASIS, "start_offset": 200, "end_offset": 200 + len(BASIS)},
            ],
        },
        {
            "gold_id": "G-2", "issuer_id": "X", "fixture": "f", "source_url": "u",
            "document_date": "2026-05-12", "raw_sha256": "a" * 64, "normalized_sha256": "c" * 64,
            "normalizer_version": "t", "split": "holdout", "expected_flag": False, "notes": None,
            "statements": [], "ineligible": [],
        },
    ]  # fmt: skip
    p = tmp_path / "gold.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return load_going_concern_gold(p)


def _st(status, start, end, valid=True):
    return {
        "statement": {"risk_type": "going_concern", "status": status, "evidence_quote": "q",
                      "start_offset": start, "end_offset": end},
        "validation": {"status": "VALID" if valid else "INVALID", "checks": {},
                       "reasons": [] if valid else ["NEGATED_DOUBT: x"], "match_kind": "exact",
                       "match_score": 100.0, "matched_start": start, "matched_end": end},
        "change": {},
    }  # fmt: skip


def test_rows_carry_the_four_statuses_and_doubt_is_the_only_negative(tmp_path):
    gold = _gold(tmp_path)
    assert isinstance(gold[0], GoingConcernGoldDocument) and gold[0].expected_flag is True
    assert gold[0].statements[0].status == "doubt" and gold[1].split == "holdout"
    assert NEGATIVE == frozenset({"doubt"})


def test_scorer_flags_on_doubt_and_reports_rejections(tmp_path):
    gold = _gold(tmp_path)
    rows = [
        {
            "gold_id": "G-1", "run_status": "ok", "has_going_concern_statements": True,
            "statements": [_st("doubt", 12, 60), _st("mentioned", 200, 240), _st("doubt", 400, 420, valid=False)],
        },
        {"gold_id": "G-2", "run_status": "ok", "has_going_concern_statements": False, "statements": []},
    ]  # fmt: skip
    m = score_going_concern(gold, rows)
    dev = m["dev"]
    assert dev["statements"]["matched"] == 2 and dev["statements"]["status_accuracy"] == 1.0
    assert dev["negative_statements"]["recall"] == 1.0 and dev["document_flag"]["recall"] == 1.0
    assert dev["rejections"] == {"NEGATED_DOUBT": 1}
    assert m["holdout"]["document_flag"]["false_positive_rate_on_negative_docs"] == 0.0
