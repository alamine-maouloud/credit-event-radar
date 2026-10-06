"""Liquidity gold set V1 (eval/gold/liquidity_v1.yaml): build, freeze, check.

build   compute offsets, check every quote (unique, status enum, polarity coherence of
        negative statuses, expected_flag consistency) and write liquidity_v1.jsonl
freeze  write liquidity_v1.lock.json (hashes of guide, labels and documents)
check   verify the lock still matches
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from radar.audit import stable_hash  # noqa: E402
from radar.config import load_universe  # noqa: E402
from radar.eval.gold import GoldDocument, load_gold_document  # noqa: E402
from radar.llm.validate import NEGATIVE_LIQUIDITY_STATUSES, liquidity_polarity_ok  # noqa: E402

GUIDE = ROOT / "eval" / "LIQUIDITY_LABELING_GUIDE.md"
LABELS = ROOT / "eval" / "gold" / "liquidity_v1.yaml"
JSONL = ROOT / "eval" / "gold" / "liquidity_v1.jsonl"
LOCK = ROOT / "eval" / "gold" / "liquidity_v1.lock.json"
STATUSES = {"deteriorated", "concern", "stable", "improved", "mentioned"}
REASONS = {"third_party", "segment", "not_liquidity"}


def _manifest(directory: Path) -> dict:
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


def _document_date(item: dict, manifest: dict) -> str:
    """The date printed in the labels, else the filing or publication date of the manifest,
    else the retrieval day (pages without a date)."""
    extra = manifest.get("extra") or {}
    return str(
        item.get("document_date")
        or extra.get("filing_date")
        or extra.get("published")
        or manifest.get("retrieved_at", "")[:10]
    )


def _locate(text: str, quote: str, label: str, problems: list[str]) -> tuple[int, int] | None:
    count = text.count(quote)
    if count == 0:
        problems.append(f"{label}: quote not found: {quote[:80]!r}")
        return None
    start = text.index(quote)  # identical repeats are labelled once, on the first occurrence
    return start, start + len(quote)


def build(args) -> None:
    spec = yaml.safe_load(LABELS.read_text(encoding="utf-8"))
    universe = load_universe()
    problems: list[str] = []
    rows = []
    for item in spec["documents"]:
        directory = ROOT / item["fixture"]
        manifest = _manifest(directory)
        probe = GoldDocument(
            gold_id=item["gold_id"], issuer_id=manifest["issuer_id"], fixture=item["fixture"],
            source_url=manifest["source_url"], document_date=_document_date(item, manifest),
            raw_sha256=manifest["raw_sha256"], normalized_sha256=manifest["normalized_sha256"],
            normalizer_version=manifest["normalizer_version"], behaviour="no_guidance",
        )  # fmt: skip
        doc = load_gold_document(probe, universe, ROOT)
        if item.get("split") not in {"dev", "holdout"}:
            problems.append(f"{item['gold_id']}: split must be dev or holdout")
        statements = []
        for st in item.get("statements", []):
            if st["status"] not in STATUSES:
                problems.append(f"{item['gold_id']}: unknown status {st['status']!r}")
            span = _locate(doc.text, st["evidence_quote"], item["gold_id"], problems)
            if span is None:
                continue
            if st["status"] in NEGATIVE_LIQUIDITY_STATUSES:
                ok, reason = liquidity_polarity_ok(st["evidence_quote"], st["status"])
                if not ok:
                    problems.append(f"{item['gold_id']}: {reason} ({st['evidence_quote'][:60]!r})")
            if st.get("value") is not None and str(st["value"]).rstrip("0").rstrip(".") not in st[
                "evidence_quote"
            ].replace(",", ""):
                problems.append(f"{item['gold_id']}: value {st['value']} not in the quote")
            statements.append({**st, "start_offset": span[0], "end_offset": span[1]})
        ineligible = []
        for p in item.get("ineligible", []):
            if p["reason"] not in REASONS:
                problems.append(f"{item['gold_id']}: unknown ineligible reason {p['reason']!r}")
            span = _locate(doc.text, p["evidence_quote"], item["gold_id"], problems)
            if span is None:
                continue
            ineligible.append({**p, "start_offset": span[0], "end_offset": span[1]})
        negative = any(s["status"] in NEGATIVE_LIQUIDITY_STATUSES for s in statements)
        if bool(item["expected_flag"]) != negative:
            problems.append(
                f"{item['gold_id']}: expected_flag {item['expected_flag']} but negative statements {negative}"
            )
        rows.append(
            {
                "gold_id": item["gold_id"],
                "issuer_id": manifest["issuer_id"],
                "fixture": item["fixture"],
                "source_url": manifest["source_url"],
                "document_date": _document_date(item, manifest),
                "raw_sha256": manifest["raw_sha256"],
                "normalized_sha256": manifest["normalized_sha256"],
                "normalizer_version": manifest["normalizer_version"],
                "split": item["split"],
                "expected_flag": bool(item["expected_flag"]),
                "statements": statements,
                "ineligible": ineligible,
                "notes": item.get("notes"),
            }
        )
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    JSONL.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    splits = {s: sum(1 for r in rows if r["split"] == s) for s in ("dev", "holdout")}
    n_st = sum(len(r["statements"]) for r in rows)
    n_neg = sum(
        1 for r in rows for s in r["statements"] if s["status"] in NEGATIVE_LIQUIDITY_STATUSES
    )
    flags = sum(1 for r in rows if r["expected_flag"])
    print(
        f"wrote {JSONL}: {len(rows)} documents {splits}, {n_st} statements ({n_neg} negative), "
        f"{sum(len(r['ineligible']) for r in rows)} ineligible passages, {flags} documents with the flag"
    )


def _lock_payload() -> dict:
    rows = [
        json.loads(line) for line in JSONL.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return {
        "guide_sha256": hashlib.sha256(GUIDE.read_bytes()).hexdigest(),
        "labels_sha256": hashlib.sha256(JSONL.read_bytes()).hexdigest(),
        "documents": {r["gold_id"]: r["normalized_sha256"] for r in rows},
        "n_documents": len(rows),
        "n_statements": sum(len(r["statements"]) for r in rows),
        "splits": {s: sum(1 for r in rows if r["split"] == s) for s in ("dev", "holdout")},
    }


def freeze(args) -> None:
    payload = {**_lock_payload(), "frozen_on": args.date, "frozen_by": args.by, "lock_sha256": None}
    payload["lock_sha256"] = stable_hash({k: v for k, v in payload.items() if k != "lock_sha256"})
    LOCK.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"frozen: {payload['n_documents']} documents, {payload['n_statements']} statements, lock {payload['lock_sha256'][:16]}"
    )


def check(args) -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    current = _lock_payload()
    diffs = [k for k in ("guide_sha256", "labels_sha256", "documents") if lock[k] != current[k]]
    if diffs:
        print(f"gold set changed since the freeze: {diffs}")
        sys.exit(1)
    print("gold set matches the freeze")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build").set_defaults(func=build)
    f = sub.add_parser("freeze")
    f.add_argument("--date", required=True)
    f.add_argument("--by", required=True)
    f.set_defaults(func=freeze)
    sub.add_parser("check").set_defaults(func=check)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
