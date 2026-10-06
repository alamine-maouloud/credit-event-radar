"""Sentence segmentation and exact evidence spans over normalised text.

Offsets are absolute positions in ``RawDocument.text``. Spans built here are exact
(``match_score`` 100); fuzzy matching of LLM-quoted passages arrives with Phase 3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from radar.models import EvidenceSpan, EvidenceType, RawDocument

ABBREVIATIONS = frozenset(
    {"inc", "co", "corp", "ltd", "plc", "no", "vs", "u.s", "s.a", "s.p.a", "e.g", "i.e",
     "mr", "ms", "mrs", "dr", "jr", "sr", "st", "approx", "n.v", "a.g", "llc", "l.p"}
)  # fmt: skip
_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"“'])")
_LAST_TOKEN_RE = re.compile(r"([A-Za-z.&]+)[.!?]$")


@dataclass(frozen=True)
class Sentence:
    start: int
    end: int
    text: str


def _is_false_boundary(before: str) -> bool:
    match = _LAST_TOKEN_RE.search(before)
    if not match:
        return False
    token = match.group(1).rstrip(".").casefold()
    if token in ABBREVIATIONS:
        return True
    return len(token) == 1 and token.isalpha()  # initials such as "J."


def iter_sentences(text: str) -> list[Sentence]:
    """Split normalised text into sentences; each line is a sentence boundary too."""
    sentences: list[Sentence] = []
    line_start = 0
    for line in text.split("\n"):
        cursor = 0
        for m in _BOUNDARY_RE.finditer(line):
            before = line[cursor : m.start()]
            if _is_false_boundary(before):
                continue
            sentences.append(Sentence(line_start + cursor, line_start + m.start(), before))
            cursor = m.end()
        tail = line[cursor:]
        if tail.strip():
            sentences.append(Sentence(line_start + cursor, line_start + len(line), tail))
        line_start += len(line) + 1
    return [s for s in sentences if s.text.strip()]


def exact_span(
    doc: RawDocument,
    start: int,
    end: int,
    *,
    extractor_version: str,
    evidence_type: EvidenceType = "sentence",
    field: str | None = None,
) -> EvidenceSpan:
    if not 0 <= start < end <= len(doc.text):
        raise ValueError(f"span [{start}, {end}) outside document of {len(doc.text)} chars")
    return EvidenceSpan(
        doc_id=doc.doc_id,
        char_start=start,
        char_end=end,
        quote=doc.text[start:end],
        evidence_type=evidence_type,
        extractor_version=extractor_version,
        match_score=100.0,
        field=field,
    )


def find_quote(
    doc: RawDocument, quote: str, *, extractor_version: str, field: str | None = None
) -> EvidenceSpan | None:
    """Exact span of ``quote`` when it occurs exactly once in the document."""
    first = doc.text.find(quote)
    if first < 0 or doc.text.find(quote, first + 1) >= 0:
        return None
    return exact_span(
        doc, first, first + len(quote), extractor_version=extractor_version, field=field
    )


def verify_span(doc: RawDocument, span: EvidenceSpan) -> bool:
    """True when the span's quote is exactly the text at its offsets."""
    return span.doc_id == doc.doc_id and doc.text[span.char_start : span.char_end] == span.quote
