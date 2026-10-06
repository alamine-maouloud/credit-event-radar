"""Covenant gold sets (eval/gold/<name>.yaml): build, freeze, check.

build   compute offsets, check every quote (found, status and resolution coherent with the
        validator's wording rules, expected_flag consistent) and write <name>.jsonl
freeze  write <name>.lock.json (hashes of guide, labels and documents)
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
from radar.llm.validate import covenant_resolution_ok, covenant_status_ok  # noqa: E402

GUIDE = ROOT / "eval" / "COVENANT_LABELING_GUIDE.md"
GOLD_DIR = ROOT / "eval" / "gold"
STATUSES = {"compliant", "risk_of_breach", "breached", "mentioned"}
RESOLUTIONS = {"none", "waived", "cured", "amended"}
REASONS = {"third_party", "segment", "not_covenant"}
NEGATIVE = {"breached"}


def _paths(name: str) -> tuple[Path, Path, Path]:
    return GOLD_DIR / f"{name}.yaml", GOLD_DIR / f"{name}.jsonl", GOLD_DIR / f"{name}.lock.json"


def _manifest(directory: Path) -> dict:
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


def _document_date(item: dict, manifest: dict) -> str:
    extra = manifest.get("extra") or {}
    return str(
        item.get("document_date")
        or extra.get("filing_date")
        or extra.get("published")
        or manifest.get("retrieved_at", "")[:10]
    )


def _locate(text: str, quote: str, label: str, problems: list[str]) -> tuple[int, int] | None:
    if text.count(quote) == 0:
        problems.append(f"{label}: quote not found: {quote[:80]!r}")
        return None
    start = text.index(quote)  # identical repeats are labelled once, on the first occurrence
    return start, start + len(quote)


def build(args) -> None:
    labels, jsonl, _ = _paths(args.name)
    spec = yaml.safe_load(labels.read_text(encoding="utf-8"))
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
            status, resolution = st["status"], st.get("resolution", "none")
            if status not in STATUSES:
                problems.append(f"{item['gold_id']}: unknown status {status!r}")
            if resolution not in RESOLUTIONS:
                problems.append(f"{item['gold_id']}: unknown resolution {resolution!r}")
            span = _locate(doc.text, st["evidence_quote"], item["gold_id"], problems)
            if span is None:
                continue
            ok, reason = covenant_status_ok(st["evidence_quote"], status)
            if not ok:
                problems.append(f"{item['gold_id']}: {reason} ({st['evidence_quote'][:60]!r})")
            ok, reason = covenant_resolution_ok(st["evidence_quote"], resolution)
            if not ok:
                problems.append(f"{item['gold_id']}: {reason} ({st['evidence_quote'][:60]!r})")
            for key in ("covenant_label", "agreement"):
                value = st.get(key)
                if value and value.casefold() not in st["evidence_quote"].casefold():
                    problems.append(f"{item['gold_id']}: {key} {value!r} not in the quote")
            statements.append(
                {**st, "resolution": resolution, "start_offset": span[0], "end_offset": span[1]}
            )
        ineligible = []
        for p in item.get("ineligible", []):
            if p["reason"] not in REASONS:
                problems.append(f"{item['gold_id']}: unknown ineligible reason {p['reason']!r}")
            span = _locate(doc.text, p["evidence_quote"], item["gold_id"], problems)
            if span is None:
                continue
            ineligible.append({**p, "start_offset": span[0], "end_offset": span[1]})
        negative = any(s["status"] in NEGATIVE for s in statements)
        if bool(item["expected_flag"]) != negative:
            problems.append(
                f"{item['gold_id']}: expected_flag {item['expected_flag']} but breached statements {negative}"
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
    jsonl.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    splits = {s: sum(1 for r in rows if r["split"] == s) for s in ("dev", "holdout")}
    n_st = sum(len(r["statements"]) for r in rows)
    n_neg = sum(1 for r in rows for s in r["statements"] if s["status"] in NEGATIVE)
    print(
        f"wrote {jsonl}: {len(rows)} documents {splits}, {n_st} statements ({n_neg} breached), "
        f"{sum(len(r['ineligible']) for r in rows)} ineligible passages, "
        f"{sum(1 for r in rows if r['expected_flag'])} documents with the flag"
    )


def _lock_payload(name: str) -> dict:
    _, jsonl, _ = _paths(name)
    rows = [
        json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return {
        "guide_sha256": hashlib.sha256(GUIDE.read_bytes()).hexdigest(),
        "labels_sha256": hashlib.sha256(jsonl.read_bytes()).hexdigest(),
        "documents": {r["gold_id"]: r["normalized_sha256"] for r in rows},
        "n_documents": len(rows),
        "n_statements": sum(len(r["statements"]) for r in rows),
        "splits": {s: sum(1 for r in rows if r["split"] == s) for s in ("dev", "holdout")},
    }


def freeze(args) -> None:
    _, _, lock = _paths(args.name)
    payload = {
        **_lock_payload(args.name),
        "frozen_on": args.date,
        "frozen_by": args.by,
        "lock_sha256": None,
    }
    payload["lock_sha256"] = stable_hash({k: v for k, v in payload.items() if k != "lock_sha256"})
    lock.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"frozen: {payload['n_documents']} documents, {payload['n_statements']} statements, lock {payload['lock_sha256'][:16]}"
    )


def check(args) -> None:
    _, _, lock_path = _paths(args.name)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    current = _lock_payload(args.name)
    diffs = [k for k in ("guide_sha256", "labels_sha256", "documents") if lock[k] != current[k]]
    if diffs:
        print(f"gold set changed since the freeze: {diffs}")
        sys.exit(1)
    print("gold set matches the freeze")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--name", default="covenant_v1", help="gold set name, e.g. covenant_v1")
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
