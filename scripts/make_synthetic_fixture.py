"""Build the synthetic EDGAR-like fixture used by the determinism tests.

Everything in it is fictional (ISSUER_TEST_A). It mimics the structure of a 10-Q liquidity
section: hidden iXBRL header, a ratings table and a footnote describing a rating action.
Run: uv run python scripts/make_synthetic_fixture.py
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from radar.connectors.fixture import write_fixture
from radar.normalize import SecHtmlNormalizer
from radar.snapshot import FetchedBytes, build_raw_document

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests" / "fixtures" / "edgar" / "synthetic_issuer_test_a_10q"

# fmt: off
# ruff: noqa: E501
HTML = b"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
<head><title>test-a-20260630</title><style>.x{display:none}</style>
<script>var hidden = "SHOULD NOT APPEAR";</script></head>
<body>
<div style="display:none"><ix:header><ix:hidden>HIDDEN IXBRL HEADER</ix:hidden></ix:header></div>
<div><span style="font-weight:700">ISSUER TEST A, INC.</span></div>
<div><span>FORM 10-Q</span></div>
<p><span>The Company&#8217;s short- and long-term credit ratings, as of June 30, 2026 were as </span><span>follows:</span></p>
<table>
<tr><td></td><td>Short-Term</td><td>Long-Term</td><td>Outlook</td></tr>
<tr><td>Moody&#8217;s</td><td>P3</td><td>Baa3</td><td>Stable</td></tr>
<tr><td>Standard &amp; Poor&#8217;s<sup>(a)</sup></td><td>A-3</td><td>BBB-</td><td>CreditWatch&#160;Negative</td></tr>
<tr><td>Fitch</td><td>F2</td><td>BBB</td><td>Negative</td></tr>
</table>
<p><span>(a)</span><span>On July 8, 2026, subsequent to June 30, 2026, S&amp;P Global Ratings lowered the Company&#8217;s long-term credit rating from BBB- to BB+, and revised the outlook to Stable.</span></p>
<p>Non-breaking\xc2\xa0spaces   and\ttabs are collapsed. Zero\xe2\x80\x8bwidth characters vanish.</p>
</body></html>
"""
# fmt: on


def main() -> None:
    fetched = FetchedBytes(
        url="https://example.invalid/edgar/ISSUER_TEST_A/test-a-20260630.htm",
        content=HTML,
        retrieved_at=datetime(2026, 10, 6, 12, 0, tzinfo=UTC),
        content_type="text/html",
    )
    with tempfile.TemporaryDirectory() as tmp:
        doc = build_raw_document(
            fetched,
            source_type="manual",
            raw_dir=Path(tmp),
            normalizer=SecHtmlNormalizer(),
            extra={"form": "10-Q", "accession_number": "0000000000-26-000001"},
        )
    needle = "lowered the Company’s long-term credit rating from BBB- to BB+"
    start = doc.text.index(needle)
    anchors = [{"quote": needle, "char_start": start, "char_end": start + len(needle)}]
    write_fixture(
        TARGET,
        fetched,
        doc,
        role="synthetic",
        issuer_id="ISSUER_TEST_A",
        anchors=anchors,
    )
    print(f"wrote {TARGET} ({doc.raw_size_bytes} raw bytes, {len(doc.text)} chars)")
    print(doc.text)


if __name__ == "__main__":
    main()
