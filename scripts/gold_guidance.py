"""Guidance gold set tooling (eval/LABELING_GUIDE.md).

candidates  print forward-looking sentences of every gold document, to annotate by hand
build       turn eval/gold/guidance_v1.yaml (hand-written quotes) into guidance_v1.jsonl with
            computed offsets, checks and document hashes
freeze      write eval/gold/guidance_v1.lock.json (hashes of guide, labels and documents)
check       verify the lock still matches guide, labels and documents
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

from radar.audit import stable_hash
from radar.config import ROOT, load_universe
from radar.connectors.fixture import FixtureAdapter, list_fixtures, load_manifest
from radar.extract.spans import iter_sentences
from radar.normalize import SecHtmlNormalizer

GOLD_DIRS = [ROOT / "tests" / "fixtures" / "gold" / "guidance", ROOT / "tests" / "fixtures" / "ir"]
GUIDE = ROOT / "eval" / "LABELING_GUIDE.md"
LABELS_YAML = ROOT / "eval" / "gold" / "guidance_v1.yaml"
LABELS_JSONL = ROOT / "eval" / "gold" / "guidance_v1.jsonl"
LOCK = ROOT / "eval" / "gold" / "guidance_v1.lock.json"
_FORWARD_RE = re.compile(
    r"(?i)\b(expect|expects|expected|outlook|guidance|forecast|anticipat|target|confirm|reaffirm|maintain|"
    r"raise|lower|cut|withdraw|range of|between|around|approximately|at the lower end|upper end|"
    r"full year|fiscal year|financial year|for 2026|for 2025|in 2026|in 2025|remain)\b"
)
_NUMBER_RE = re.compile(r"\d")


def load_document(directory: Path):
    universe = load_universe()
    with tempfile.TemporaryDirectory() as tmp:
        docs = FixtureAdapter(directory, Path(tmp), SecHtmlNormalizer()).fetch(
            date(2000, 1, 1), universe.issuers
        )
    return docs[0] if docs else None


def fixture_dirs(names: list[str] | None = None) -> list[Path]:
    out = []
    for root in GOLD_DIRS:
        for d in list_fixtures(root):
            if names and d.name not in names:
                continue
            out.append(d)
    return out


def candidates(args) -> None:
    for d in fixture_dirs(args.names):
        doc = load_document(d)
        if doc is None:
            print(f"## {d.name}: bytes missing")
            continue
        print(
            f"## {d.name} | {doc.title} | published {doc.published_at.date() if doc.published_at else None} | {len(doc.text)} chars"
        )
        shown = 0
        for s in iter_sentences(doc.text):
            if _FORWARD_RE.search(s.text) and _NUMBER_RE.search(s.text) and 30 < len(s.text) < 600:
                print(f"  [{s.start}] {s.text[: args.width]}")
                shown += 1
                if shown >= args.max:
                    break


def build(args) -> None:
    spec = yaml.safe_load(LABELS_YAML.read_text(encoding="utf-8"))
    rows = []
    problems = []
    for item in spec["documents"]:
        directory = ROOT / item["fixture"]
        manifest = load_manifest(directory)
        doc = load_document(directory)
        if doc is None:
            problems.append(f"{item['fixture']}: bytes missing")
            continue
        if (
            doc.content_hash != manifest["raw_sha256"]
            or doc.doc_id != manifest["normalized_sha256"]
        ):
            problems.append(f"{item['fixture']}: document hashes differ from the manifest")
        occurrences = []
        for occ in item.get("occurrences", []):
            quote = occ["evidence_quote"]
            count = doc.text.count(quote)
            if count != 1:
                problems.append(f"{item['fixture']}: quote found {count} times: {quote[:80]!r}")
                continue
            start = doc.text.index(quote)
            numbers = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", quote)]
            for key in ("previous_lower", "previous_upper", "current_lower", "current_upper"):
                value = occ.get(key)
                if value is not None and not any(abs(n - float(value)) < 1e-9 for n in numbers):
                    problems.append(f"{item['fixture']}: {key}={value} not in quote {quote[:60]!r}")
            occurrences.append({**occ, "start_offset": start, "end_offset": start + len(quote)})
        rows.append(
            {
                "gold_id": item["gold_id"],
                "issuer_id": manifest["issuer_id"],
                "fixture": item["fixture"],
                "source_url": manifest["source_url"],
                "document_date": item["document_date"],
                "raw_sha256": manifest["raw_sha256"],
                "normalized_sha256": manifest["normalized_sha256"],
                "normalizer_version": manifest["normalizer_version"],
                "behaviour": item["behaviour"],
                "occurrences": occurrences,
                "notes": item.get("notes"),
            }
        )
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    LABELS_JSONL.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    behaviours = {}
    for r in rows:
        behaviours[r["behaviour"]] = behaviours.get(r["behaviour"], 0) + 1
    print(
        f"wrote {LABELS_JSONL}: {len(rows)} documents, {sum(len(r['occurrences']) for r in rows)} occurrences, behaviours {behaviours}"
    )


def _lock_payload() -> dict:
    rows = [
        json.loads(line)
        for line in LABELS_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {
        "guide_sha256": hashlib.sha256(GUIDE.read_bytes()).hexdigest(),
        "labels_sha256": hashlib.sha256(LABELS_JSONL.read_bytes()).hexdigest(),
        "documents": {
            r["gold_id"]: {
                "raw_sha256": r["raw_sha256"],
                "normalized_sha256": r["normalized_sha256"],
            }
            for r in rows
        },
        "n_documents": len(rows),
        "n_occurrences": sum(len(r["occurrences"]) for r in rows),
    }


def freeze(args) -> None:
    payload = {**_lock_payload(), "frozen_on": args.date, "frozen_by": args.by, "lock_sha256": None}
    payload["lock_sha256"] = stable_hash({k: v for k, v in payload.items() if k != "lock_sha256"})
    LOCK.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"frozen: {payload['n_documents']} documents, {payload['n_occurrences']} occurrences, lock {payload['lock_sha256'][:16]}"
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
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("candidates")
    c.add_argument("--names", nargs="*", default=None)
    c.add_argument("--max", type=int, default=8)
    c.add_argument("--width", type=int, default=220)
    c.set_defaults(func=candidates)
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
