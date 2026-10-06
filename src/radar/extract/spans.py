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


# sentence-window-1.1 (2026-10-06): a line break inside a wrapped sentence no longer ends the
# window. Some HTML filings wrap their paragraphs at a fixed width; the normalised text and
# its hashes are untouched, only the windows the extractors and the validator reason on.
SENTENCE_WINDOW_VERSION = "sentence-window-1.1"
_TERMINAL_END_RE = re.compile(r"[.!?;:][\"'\u201d\u2019)\]]*\s*$")
_CONTINUATION_START_RE = re.compile(r"^\s*[a-z\u00e0-\u00ff]")


def _continues(line: str, nxt: str) -> bool:
    """The next line continues this one: no terminal punctuation here, no table cell on
    either side, and the next line starts with a lowercase letter or this one ends with a
    hyphen before a letter. A heading followed by a capital keeps its own window."""
    tail = line.rstrip()
    if not tail or "|" in line or "|" in nxt:
        return False
    if _TERMINAL_END_RE.search(tail):
        return False
    if tail.endswith("-") and nxt[:1].isalpha():
        return True
    return bool(_CONTINUATION_START_RE.match(nxt))


def _logical_lines(text: str) -> list[tuple[int, str]]:
    lines = text.split("\n")
    out: list[tuple[int, str]] = []
    pos = 0
    start: int | None = None
    buffer: str | None = None
    for i, line in enumerate(lines):
        if buffer is None:
            start, buffer = pos, line
        else:
            buffer = buffer + "\n" + line
        pos += len(line) + 1
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if nxt is not None and _continues(buffer, nxt):
            continue
        assert start is not None
        out.append((start, buffer))
        buffer = None
    return out


def iter_sentences(text: str) -> list[Sentence]:
    """Split normalised text into sentence windows. A line ends a window unless it is a
    wrapped continuation (sentence-window-1.1); offsets refer to the original text."""
    sentences: list[Sentence] = []
    for line_start, line in _logical_lines(text):
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
