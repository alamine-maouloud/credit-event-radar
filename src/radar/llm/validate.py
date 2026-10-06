"""Span validator: text match plus semantic field validation (ADR-014).

A quote is accepted when it is found exactly in the document (at the stated offsets or
anywhere, uniquely) or with a rapidfuzz partial ratio of at least 90. That alone never
validates a statement: numbers, unit, metric, attachment of the numbers to the metric's
sentence, entity and dates are checked deterministically, and any failed check rejects.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from radar.extract.dates import find_dates
from radar.extract.spans import iter_sentences
from radar.extract.structured import _issuer_named, _matches_issuer, find_named_entities
from radar.llm.schemas import GuidanceStatement
from radar.models import RawDocument
from radar.ratings import RatingScales

SPAN_FUZZY_THRESHOLD = 90.0
FUTURE_TOLERANCE_DAYS = 3
PERIOD_PAST_YEARS = 1
PERIOD_FUTURE_YEARS = 3

_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_METRIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "revenues", "sales", "turnover"),
    "ebitda": ("ebitda",),
    "ebit": ("ebit", "operating result", "operating profit", "operating income"),
    "fcf": ("free cash flow", "net cash flow", "cash flow"),
    "margin": ("margin", "return on sales"),
    "capex": ("capex", "capital expenditure", "investment ratio", "investments"),
    "other": (),
}
_UNIT_TOKENS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "EUR_BN": (("eur", "€", "euro", "euros"), ("billion", "bn")),
    "EUR_MN": (("eur", "€", "euro", "euros"), ("million", "mn", "millions")),
    "USD_BN": (("usd", "$", "dollar", "dollars"), ("billion", "bn")),
    "USD_MN": (("usd", "$", "dollar", "dollars"), ("million", "mn", "millions")),
    "PCT": ((), ("%", "percent", "per cent", "percentage points", "basis points")),
}


_SCALES: list[RatingScales] = []


def _default_scales() -> RatingScales:
    if not _SCALES:
        from radar.config import load_rating_scales

        _SCALES.append(load_rating_scales())
    return _SCALES[0]


class ValidationResult(BaseModel):
    status: Literal["VALID", "INVALID"]
    checks: dict[str, bool]
    reasons: list[str] = Field(default_factory=list)
    match_kind: Literal["exact", "fuzzy", "none"]
    match_score: float
    matched_start: int | None = None
    matched_end: int | None = None


def _parse_number(token: str) -> float | None:
    cleaned = token.replace("+", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUMBER_RE.finditer(text):
        value = _parse_number(m.group(0))
        if value is not None:
            out.append(value)
    return out


def _span_match(
    quote: str, text: str, start: int, end: int
) -> tuple[str, float, int | None, int | None]:
    if 0 <= start < end <= len(text) and text[start:end] == quote:
        return "exact", 100.0, start, end
    first = text.find(quote)
    if first >= 0 and text.find(quote, first + 1) < 0:
        return "exact", 100.0, first, first + len(quote)
    alignment = fuzz.partial_ratio_alignment(quote, text, score_cutoff=SPAN_FUZZY_THRESHOLD)
    if alignment is not None and alignment.score >= SPAN_FUZZY_THRESHOLD:
        return "fuzzy", float(alignment.score), alignment.dest_start, alignment.dest_end
    return "none", float(alignment.score) if alignment else 0.0, None, None


def _bounds(st: GuidanceStatement) -> list[float]:
    return [
        v
        for v in (st.previous_lower, st.previous_upper, st.current_lower, st.current_upper)
        if v is not None
    ]


def _contains(values: list[float], wanted: float) -> bool:
    return any(abs(v - wanted) < 1e-9 for v in values)


def validate_statement(
    st: GuidanceStatement,
    doc: RawDocument,
    issuer_names: list[str],
    *,
    document_date: date | None,
    scales: RatingScales | None = None,
) -> ValidationResult:
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    quote = st.evidence_quote
    low = quote.casefold()

    kind, score, m_start, m_end = _span_match(quote, doc.text, st.start_offset, st.end_offset)
    checks["span_match"] = kind != "none"
    if kind == "none":
        reasons.append(f"quote not found in the document (best partial ratio {score:.0f})")

    numbers = _numbers_in(quote)
    bounds = _bounds(st)
    missing = [b for b in bounds if not _contains(numbers, b)]
    checks["numbers_match"] = not missing
    if missing:
        reasons.append(f"numbers not in the quote: {missing}")

    currency_tokens, scale_tokens = _UNIT_TOKENS[st.unit]
    unit_ok = any(t in low for t in scale_tokens) and (
        not currency_tokens or any(t in low for t in currency_tokens)
    )
    checks["unit_match"] = unit_ok
    if not unit_ok:
        reasons.append(f"unit {st.unit} not supported by the quote")

    label = st.metric_label.casefold().strip()
    keywords = _METRIC_KEYWORDS[st.metric]
    label_in_quote = bool(label) and label in low
    label_fits_metric = st.metric == "other" or any(k in label for k in keywords)
    checks["metric_match"] = label_in_quote and label_fits_metric
    if not checks["metric_match"]:
        reasons.append(f"metric {st.metric} ({st.metric_label!r}) not supported by the quote")

    attached = True
    if bounds and label_in_quote:
        sentences = [s.text.casefold() for s in iter_sentences(quote)] or [low]
        label_sentences = [s for s in sentences if label in s]
        pool = [n for s in label_sentences for n in _numbers_in(s)]
        attached = all(_contains(pool, b) for b in bounds)
    checks["numbers_attached"] = attached
    if not attached:
        reasons.append("numbers are not in the sentence that names the metric")

    entity_ok = True
    sc = scales or _default_scales()
    others = [e for e in find_named_entities(quote, sc) if not _matches_issuer(e[0], issuer_names)]
    if others and not _issuer_named(quote, issuer_names, [(e[1], e[2]) for e in others]):
        entity_ok = False
        reasons.append(f"quote is about another entity: {others[0][0]}")
    checks["entity_match"] = entity_ok

    temporal_ok = True
    if document_date is not None:
        for found, _, _ in find_dates(quote):
            if found > document_date + timedelta(days=FUTURE_TOLERANCE_DAYS):
                temporal_ok = False
                reasons.append(f"quote dated {found.isoformat()}, after the document date")
                break
        if st.period:
            years = [int(y) for y in re.findall(r"\b(20\d\d)\b", st.period)]
            if years and not all(
                document_date.year - PERIOD_PAST_YEARS
                <= y
                <= document_date.year + PERIOD_FUTURE_YEARS
                for y in years
            ):
                temporal_ok = False
                reasons.append(
                    f"period {st.period!r} is not a plausible guidance period for {document_date.isoformat()}"  # noqa: E501
                )
    checks["temporal_consistency"] = temporal_ok

    status = "VALID" if all(checks.values()) else "INVALID"
    return ValidationResult(
        status=status, checks=checks, reasons=reasons, match_kind=kind, match_score=score,
        matched_start=m_start, matched_end=m_end,
    )  # fmt: skip
