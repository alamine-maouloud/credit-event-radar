"""Export the public demo bundle from a built demo database (no secret, no raw model output).

demo/bundle/documents.json   the public documents of the demo: fixture directory, official
                             URL, hashes (raw and normalised), issuer, role
demo/bundle/statements.jsonl the validated model statements of those documents: status and
                             fields, offsets in the normalised text, the SHA-256 of the quoted
                             passage (the passage itself is rebuilt from the document at load
                             time), model, prompt, schema, routed role and reason

uv run python scripts/export_demo_bundle.py --db data/demo.db
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "demo" / "bundle"
# the fixtures behind the four featured cases and the reserve case of docs/DEMO.md
DOCUMENTS = [
    ("tests/fixtures/edgar/harley_davidson_inc_10q_2026q2", "Harley-Davidson, 10-Q, S&P downgrade BBB- to BB+"),
    ("tests/fixtures/edgar/hydrofarm_10q_2026q2", "Hydrofarm, 10-Q, going concern doubt"),
    ("tests/fixtures/gold/guidance/omv_q4_2024_report", "OMV, Q4 2024 report, denied going concern doubt"),
    ("tests/fixtures/gold/guidance/vw_h1_2025_results", "Volkswagen, H1 2025 results, guidance cut"),
    ("tests/fixtures/edgar/ftc_solar_10q_2026q2", "FTC Solar, 10-Q, covenant breach waived (reserve)"),
]  # fmt: skip
KEEP = (
    "statement_id", "doc_id", "issuer_id", "model_id", "resolved_model", "prompt_version",
    "schema_version", "validation_json", "change_json", "validation_status", "statement_kind",
    "model_selection_json",
)  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "demo.db")
    args = parser.parse_args()
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from radar.db import Database

    db = Database(args.db)
    BUNDLE.mkdir(parents=True, exist_ok=True)
    documents = []
    statements = []
    for fixture, label in DOCUMENTS:
        manifest = json.loads((ROOT / fixture / "manifest.json").read_text(encoding="utf-8"))
        documents.append(
            {
                "fixture": fixture,
                "label": label,
                "issuer_id": manifest["issuer_id"],
                "role": manifest["role"],
                "source_url": manifest["source_url"],
                "raw_file": manifest["raw_file"],
                "raw_sha256": manifest["raw_sha256"],
                "normalized_sha256": manifest["normalized_sha256"],
                "normalizer_version": manifest["normalizer_version"],
            }
        )
        doc = db.get_document(manifest["normalized_sha256"])
        if doc is None:
            raise SystemExit(f"{fixture}: document not in {args.db}, build the demo first")
        for row in db.statements_for_document(doc.doc_id):
            if row["validation_status"] != "VALID":
                continue
            st = dict(row["statement_json"])
            quote = st.pop("evidence_quote")
            v = row["validation_json"]
            start, end = v.get("matched_start"), v.get("matched_end")
            if (
                start is None
                or end is None
                or doc.text[start:end] != quote
                and quote not in doc.text
            ):
                raise SystemExit(f"{row['statement_id'][:12]}: passage not located in the document")
            if doc.text[start:end] != quote:
                start = doc.text.index(quote)
                end = start + len(quote)
            statements.append(
                {
                    **{k: row.get(k) for k in KEEP},
                    "statement_json": st,
                    "quote_start": start,
                    "quote_end": end,
                    "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                }
            )
    db.close()
    (BUNDLE / "documents.json").write_text(
        json.dumps({"version": "1.0", "documents": documents}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (BUNDLE / "statements.jsonl").write_text(
        "".join(json.dumps(s, ensure_ascii=False, sort_keys=True) + "\n" for s in statements),
        encoding="utf-8",
    )
    print(f"{len(documents)} documents, {len(statements)} validated statements written to {BUNDLE}")


if __name__ == "__main__":
    main()
