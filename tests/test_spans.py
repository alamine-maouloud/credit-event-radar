"""Sentence segmentation and exact spans."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from radar.extract.spans import exact_span, find_quote, iter_sentences, verify_span
from radar.models import RawDocument

HEX = "1" * 64


def doc(text: str) -> RawDocument:
    return RawDocument(
        doc_id=HEX, source_type="manual", url="https://example.invalid/d",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC), content_hash=HEX, raw_size_bytes=1,
        normalizer_version="t", text=text, raw_path="p",
    )  # fmt: skip


def test_sentences_have_exact_offsets():
    text = "First one. Second one! Third?\nA line. Another."
    sentences = iter_sentences(text)
    assert [s.text for s in sentences] == [
        "First one.",
        "Second one!",
        "Third?",
        "A line.",
        "Another.",
    ]
    for s in sentences:
        assert text[s.start : s.end] == s.text


def test_abbreviations_do_not_split():
    text = "Issuer Test A, Inc. reported. S&P Global Ratings lowered it. Mr. J. Smith left."
    assert [s.text for s in iter_sentences(text)] == [
        "Issuer Test A, Inc. reported.",
        "S&P Global Ratings lowered it.",
        "Mr. J. Smith left.",
    ]


def test_footnote_marker_and_table_row_are_separate_sentences():
    text = "Fitch | F2 | BBB | Negative\n(a)On July 8, 2026, S&P lowered it to BB+, and revised the outlook. Next."
    texts = [s.text for s in iter_sentences(text)]
    assert texts[0] == "Fitch | F2 | BBB | Negative"
    assert texts[1].startswith("(a)On July 8, 2026") and texts[1].endswith("outlook.")
    assert texts[2] == "Next."


def test_exact_span_and_verify():
    d = doc("Hello world")
    span = exact_span(d, 6, 11, extractor_version="t", field="x")
    assert span.quote == "world" and span.match_score == 100.0 and span.field == "x"
    assert verify_span(d, span)
    assert not verify_span(d, span.model_copy(update={"quote": "World"}))


@pytest.mark.parametrize("start,end", [(0, 0), (5, 3), (0, 99), (-1, 2)])
def test_exact_span_rejects_bad_offsets(start, end):
    with pytest.raises(ValueError):
        exact_span(doc("Hello world"), start, end, extractor_version="t")


def test_find_quote_requires_uniqueness():
    d = doc("a b a")
    assert find_quote(d, "b", extractor_version="t").char_start == 2
    assert find_quote(d, "a", extractor_version="t") is None
    assert find_quote(d, "zzz", extractor_version="t") is None
