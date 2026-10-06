"""Builders for the LLM layer tests. Everything is fictional and offline."""

from __future__ import annotations

from datetime import UTC, date, datetime

from radar.models import RawDocument
from radar.snapshot import sha256_hex

ISSUER_NAMES = ["Issuer Test A", "Issuer Test A AG", "ITA"]
VW_LIKE = (
    "First Quarter: Issuer Test A AG makes progress in a challenging environment\n"
    "Outlook for 2026\n"
    "The Issuer Test A Group expects sales revenue in 2026 to develop within a range of 0 and +3 percent "
    "compared with the previous year. The Group's operating return on sales is expected to range between "
    "4.0 and 5.5 percent. In March the company had guided for an operating return on sales of between 5.5 "
    "and 6.5 percent.\n"
    "Net cash flow for the year 2026 is expected to range between EUR 3 billion and EUR 6 billion.\n"
    "ITA Financial Services AG expects its operating result to reach EUR 2.5 billion."
)


def doc(text: str = VW_LIKE, published: date | None = date(2026, 4, 30)) -> RawDocument:
    return RawDocument(
        doc_id=sha256_hex(text),
        source_type="ir_feed",
        url="https://example.invalid/pr",
        title=text.split("\n", 1)[0],
        published_at=datetime(published.year, published.month, published.day, tzinfo=UTC)
        if published
        else None,
        retrieved_at=datetime(2026, 10, 6, tzinfo=UTC),
        content_hash=sha256_hex(text.encode()),
        raw_size_bytes=len(text),
        normalizer_version="t",
        text=text,
        raw_path="p",
        extra={"document_type": "press_release"},
    )


def statement(text: str = VW_LIKE, quote: str | None = None, **overrides):
    from radar.llm.schemas import GuidanceStatement

    quote = (
        quote
        or "The Group's operating return on sales is expected to range between 4.0 and 5.5 percent."
    )
    start = text.index(quote) if quote in text else 0
    base = dict(
        metric="margin",
        metric_label="operating return on sales",
        basis="margin_pct",
        unit="PCT",
        previous_lower=5.5,
        previous_upper=6.5,
        current_lower=4.0,
        current_upper=5.5,
        period="2026",
        status="cut",
        direction_claimed="down",
        evidence_quote=quote,
        start_offset=start,
        end_offset=start + len(quote),
    )
    base.update(overrides)
    return GuidanceStatement(**base)
