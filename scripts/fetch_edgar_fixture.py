"""Fetch one EDGAR document and store it as a golden fixture.

Requires SEC_USER_AGENT in .env (project name and real contact). Example:

uv run python scripts/fetch_edgar_fixture.py \\
  --url https://www.sec.gov/Archives/edgar/data/793952/000079395226000061/hog-20260630.htm \\
  --issuer-id HARLEY_DAVIDSON_INC --role historical_control \\
  --form 10-Q --filing-date 2026-08-05 \\
  --anchor "lowered the Company's long-term credit rating from BBB- to BB+" \\
  --out tests/fixtures/edgar/harley_davidson_inc_10q_2026q2
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from radar.config import load_dotenv
from radar.connectors.edgar import EdgarAdapter
from radar.connectors.fixture import write_fixture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--issuer-id", required=True)
    parser.add_argument("--role", required=True, choices=["historical_control", "demo_watchlist"])
    parser.add_argument("--form", default=None)
    parser.add_argument("--filing-date", default=None)
    parser.add_argument("--anchor", action="append", default=[], help="quote that must appear once")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    load_dotenv()
    with tempfile.TemporaryDirectory() as tmp:
        adapter = EdgarAdapter(Path(tmp))
        doc = adapter.fetch_document(args.url)
        fetched_bytes = Path(doc.raw_path).read_bytes()

    from datetime import datetime

    from radar.snapshot import FetchedBytes

    fetched = FetchedBytes(
        url=args.url,
        content=fetched_bytes,
        retrieved_at=datetime.fromisoformat(doc.retrieved_at.isoformat()),
        content_type="text/html",
    )
    anchors = []
    for quote in args.anchor:
        quote = quote.replace("'", "’")
        count = doc.text.count(quote)
        if count != 1:
            raise SystemExit(f"anchor must appear exactly once, found {count}: {quote!r}")
        start = doc.text.index(quote)
        anchors.append({"quote": quote, "char_start": start, "char_end": start + len(quote)})
    extra = dict(doc.extra)
    if args.form:
        extra["form"] = args.form
    if args.filing_date:
        extra["filing_date"] = args.filing_date
    doc = doc.model_copy(update={"extra": extra})
    path = write_fixture(
        args.out, fetched, doc, role=args.role, issuer_id=args.issuer_id, anchors=anchors
    )
    print(f"wrote {path}")
    print(f"raw sha256 {doc.content_hash}, {doc.raw_size_bytes} bytes")
    print(f"normalised {len(doc.text)} chars, sha256 {doc.doc_id}")


if __name__ == "__main__":
    main()
