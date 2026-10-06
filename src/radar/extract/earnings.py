"""Deterministic classification of results releases (``structured-earnings-1.1``).

A document is a results release when its title (or first line) names a reporting period.
The event carries only what the text states explicitly: a guidance statement
(reaffirmed, in line, raised, cut, withdrawn) found by fixed patterns, and warning flags
(going concern, covenant, impairment, liquidity). Numbers are not extracted here; without
an explicit statement the event has no applicable rule (ADR-012).
"""

from __future__ import annotations

import re
from datetime import date

from radar.extract.spans import exact_span, find_quote, iter_sentences
from radar.extract.structured import Extraction, Skipped, event_id_for
from radar.models import CreditEvent, EvidenceSpan, RawDocument

EARNINGS_EXTRACTOR_VERSION = "structured-earnings-1.1"

_RESULTS_TITLE_RE = re.compile(
    r"(?i)\b(?:(?:first|second|third|fourth)\s+quarter|Q[1-4]|half[- ]year|(?:first|second)\s+half|H[12]\b|nine[- ]months|9M\b|"  # noqa: E501
    r"full[- ]year|fiscal[- ]year|FY\s?20\d\d|annual results|results|interim report|trading update)\b"  # noqa: E501
)
_WEAK_PERIOD_RE = re.compile(r"(?i)(?:first|second)\s+half")
_RESULTS_NOUN_RE = re.compile(
    r"(?i)\b(?:results?|profit(?:ability)?|revenues?|sales|earnings|orders?|deliveries|ebit(?:da)?|"
    r"margins?|cash flow|outlook|guidance|quarter|interim|report)\b"
)
_STATEMENTS: list[tuple[str, re.Pattern[str]]] = [
    (
        "reaffirmed",
        re.compile(
            r"(?i)\b(?:confirm(?:s|ed|ing)?|reaffirm(?:s|ed|ing)?|maintain(?:s|ed|ing)?|reiterat(?:es|ed|ing)|uphold(?:s|ing)?)\b[^.;]{0,60}?\b(?:outlook|guidance|forecast|targets?|expectations)\b"
        ),
    ),
    (
        "in_line",
        re.compile(
            r"(?i)\bin line with\b[^.;]{0,40}?\b(?:guidance|expectations|outlook|forecast|targets?)\b"  # noqa: E501
        ),
    ),
    (
        "raised",
        re.compile(
            r"(?i)\b(?:rais(?:es|ed|ing)|increas(?:es|ed|ing)|lift(?:s|ed|ing)|upgrad(?:es|ed|ing))\b[^.;]{0,60}?\b(?:outlook|guidance|forecast|targets?)\b"
        ),
    ),
    (
        "cut",
        re.compile(
            r"(?i)\b(?:lower(?:s|ed|ing)?|cut(?:s|ting)?|reduc(?:es|ed|ing)|downgrad(?:es|ed|ing)|revis(?:es|ed|ing)\s+down(?:wards?)?)\b[^.;]{0,60}?\b(?:outlook|guidance|forecast|targets?)\b"
        ),
    ),
    (
        "withdrawn",
        re.compile(
            r"(?i)\bwithdr(?:aws?|ew|awn|awing)\b[^.;]{0,40}?\b(?:outlook|guidance|forecast)\b"
        ),
    ),
]
# A sentence that denies the doubt is a reassurance, not a warning.
_FLAG_NEGATIONS: dict[str, re.Pattern[str]] = {
    "liquidity": re.compile(
        r"(?i)\bno\s+(?:\w+\s+){0,2}liquidity\s+(?:concern|constraint|pressure|shortfall)s?\b"
        r"|\bnot\s+(?:exposed\s+to|subject\s+to|facing|impacted|affected)\b"
        r"|\bwithout\s+(?:any\s+)?liquidity\b"
    ),
    "going_concern": re.compile(
        r"(?i)\bno\s+(?:substantial|significant|material)\s+(?:doubt|uncertaint(?:y|ies))\b"
        r"|\bnot\s+(?:impacted|affected|in\s+doubt)\b|\bdoes\s+not\s+cast\b"
    ),
}
_FLAGS: list[tuple[str, re.Pattern[str]]] = [
    (
        # Only doubt wording: "prepared on a going concern basis" or "ability to continue as a
        # going concern is not impacted" (OMV quarterly reports) are reassurances, not warnings.
        "going_concern",
        re.compile(
            r"(?i)\b(?:substantial|significant|material)\s+(?:doubt|uncertaint(?:y|ies))\b"
            r"[^.;]{0,120}?\bgoing concern\b"
            r"|\bgoing concern\b[^.;]{0,80}?\b(?:substantial|significant|material)"
            r"\s+(?:doubt|uncertaint(?:y|ies))\b"
            r"|\b(?:unable|not\s+be\s+able)\s+to\s+continue\s+as\s+a\s+going\s+concern\b"
        ),
    ),
    (
        "covenant",
        re.compile(
            r"(?i)\bcovenant\b[^.;]{0,40}?\b(?:breach|waiver|violation|default)\b|\b(?:breach|waiver)\b[^.;]{0,40}?\bcovenant"
        ),
    ),
    (
        "impairment",
        re.compile(
            r"(?i)\b(?:material|significant|substantial)\b[^.;]{0,30}?\bimpairment\b|\bimpairment\b[^.;]{0,40}?\b(?:material|significant|substantial)\b"
        ),
    ),
    (
        "liquidity",
        re.compile(
            # a worry or a deterioration, never the bare "liquidity risk" of a risk section
            r"(?i)\bliquidity\b[^.;]{0,30}?\b(?:concerns?|constraints?|constrained|shortfalls?|"
            r"pressures?|tighten(?:ed|ing)|strained|insufficient)\b"
            r"|\b(?:constrained|tight|strained|insufficient)\s+liquidity\b"
        ),
    ),
]


def _title_and_span(doc: RawDocument) -> tuple[str, EvidenceSpan | None]:
    first_line = doc.text.split("\n", 1)[0]
    title = doc.title or first_line
    span = (
        find_quote(doc, title, extractor_version=EARNINGS_EXTRACTOR_VERSION, field="title")
        if title in doc.text
        else None
    )
    if span is None and first_line:
        span = exact_span(
            doc, 0, len(first_line), extractor_version=EARNINGS_EXTRACTOR_VERSION, field="title"
        )
    return title, span


def extract_earnings_events(doc: RawDocument, issuer_id: str) -> Extraction:
    title, title_span = _title_and_span(doc)
    period_match = _RESULTS_TITLE_RE.search(title)
    if not period_match:
        return Extraction([], [])
    if _WEAK_PERIOD_RE.fullmatch(period_match.group(0)) and not _RESULTS_NOUN_RE.search(title):
        return Extraction([], [])  # "expected in the first half of 2027" is not a results release
    skipped: list[Skipped] = []
    evidence: list[EvidenceSpan] = [title_span] if title_span else []
    statuses: list[tuple[str, int, int]] = []
    flags: list[tuple[str, int, int]] = []
    for sentence in iter_sentences(doc.text):
        for status, pattern in _STATEMENTS:
            m = pattern.search(sentence.text)
            if m:
                statuses.append((status, sentence.start + m.start(), sentence.start + m.end()))
        for flag, pattern in _FLAGS:
            m = pattern.search(sentence.text)
            negated = _FLAG_NEGATIONS.get(flag)
            if m and negated is not None and negated.search(sentence.text):
                skipped.append(
                    Skipped(
                        "negated_flag:" + flag,
                        sentence.start + m.start(),
                        sentence.start + m.end(),
                        sentence.text[:160],
                    )
                )
                continue
            if m and flag not in {f for f, _, _ in flags}:
                flags.append((flag, sentence.start + m.start(), sentence.start + m.end()))
    guidance_status: str | None = None
    distinct = {s for s, _, _ in statuses}
    if len(distinct) == 1:
        guidance_status, s0, s1 = statuses[0]
        evidence.append(
            exact_span(
                doc, s0, s1, extractor_version=EARNINGS_EXTRACTOR_VERSION, field="guidance_status"
            )
        )
    elif len(distinct) > 1:
        s0, s1 = statuses[0][1], statuses[-1][2]
        skipped.append(
            Skipped("conflicting_guidance_statements", s0, s1, ", ".join(sorted(distinct)))
        )
    for flag, f0, f1 in flags:
        evidence.append(
            exact_span(
                doc, f0, f1, extractor_version=EARNINGS_EXTRACTOR_VERSION, field=f"flag:{flag}"
            )
        )
    effective: date | None = doc.published_at.date() if doc.published_at else None
    fields = {
        "period": period_match.group(0),
        "guidance_metric": None,
        "guidance_old": None,
        "guidance_new": None,
        "guidance_change_pct": None,
        "guidance_qualified_significant": None,
        "guidance_status": guidance_status,
        "flags": [f for f, _, _ in flags],
        "extractor_version": EARNINGS_EXTRACTOR_VERSION,
    }
    key = {"period": fields["period"], "date": effective.isoformat() if effective else doc.doc_id}
    event = CreditEvent(
        event_id=event_id_for(issuer_id, "earnings", "earnings_release", key),
        issuer_id=issuer_id,
        family="earnings",
        event_type="earnings_release",
        effective_date=effective,
        fields=fields,
        evidence=evidence,
        extraction_method="structured",
        source_doc_ids=[doc.doc_id],
    )
    return Extraction([event], skipped)
