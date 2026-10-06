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

from radar.extract.dates import find_dates, is_historical
from radar.extract.spans import iter_sentences
from radar.extract.structured import _issuer_named, _matches_issuer, find_named_entities
from radar.llm.schemas import CovenantStatement, GuidanceStatement, LiquidityStatement
from radar.models import RawDocument
from radar.ratings import RatingScales

SPAN_FUZZY_THRESHOLD = 90.0
FUTURE_TOLERANCE_DAYS = 3
PERIOD_PAST_YEARS = 1
PERIOD_FUTURE_YEARS = 3

# Numbers as issuers print them: optional sign (hyphen, en dash, figure dash or minus, possibly
# spaced, "+ 0%"), thousands separators, decimal point or comma.
_NUMBER_RE = re.compile(
    r"(?<![\w.])([-+\u2012\u2013\u2212])?\s{0,2}(\d{1,3}(?:,\d{3})+|\d+)(?:[.,]\d+)?(?![\w])"
)
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
    # auxiliary fields the quote does not support, dropped instead of invalidating the
    # statement (field-level salvage); the decisional fields are never salvaged
    salvaged: dict[str, str] = Field(default_factory=dict)
    match_kind: Literal["exact", "fuzzy", "none"]
    match_score: float
    matched_start: int | None = None
    matched_end: int | None = None


def _parse_number(token: str) -> float | None:
    cleaned = re.sub(
        r"^[-+\u2012\u2013\u2212]\s*", lambda m: "-" if m.group(0)[0] != "+" else "", token
    )
    cleaned = re.sub(r"(?<=\d),(?=\d{3}\b)", "", cleaned)  # thousands separator
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


def _segment_named(label: str, quote: str, segments: list[str]) -> str | None:
    """The configured segment the statement is about, if any: named in the metric label, or
    introduced by "for", "of", "in" or "at" in the sentence of the quote that names the
    metric ("Organic CAPEX for Chemicals"). A principal division is never listed here."""
    label_low = label.casefold()
    sentences = [s.text for s in iter_sentences(quote)] or [quote]
    metric_sentences = [
        s for s in sentences if label_low and label_low in s.casefold()
    ] or sentences
    for segment in sorted(segments, key=len, reverse=True):
        word = re.compile(r"(?<![\w&])" + re.escape(segment) + r"(?![\w&])", re.IGNORECASE)
        if word.search(label):
            return segment
        # the configured segment as the subject of the sentence ("Cat Financial was not in
        # compliance") or as the holder of the figure anywhere in it ("Cat Financial's
        # ratio", "the ratio of Cat Financial"); only configured names, never a generic rule
        subject = re.compile(
            r"^\s*(?:the\s+)?" + re.escape(segment) + r"(?:\u2019s|'s)?(?![\w&])", re.IGNORECASE
        )
        holder = re.compile(
            r"(?<![\w&])" + re.escape(segment) + r"(?:\u2019s|'s)(?![\w&])", re.IGNORECASE
        )
        if any(subject.match(sentence) or holder.search(sentence) for sentence in metric_sentences):
            return segment
        introduced = re.compile(
            r"\b(?:for|of|in|at)\s+(?:the\s+)?" + re.escape(segment) + r"(?![\w&])",
            re.IGNORECASE,
        )
        if any(introduced.search(sentence) for sentence in metric_sentences):
            return segment
    return None


def _entity_check(
    quote: str, issuer_names: list[str], scales: RatingScales | None
) -> tuple[bool, str | None]:
    sc = scales or _default_scales()
    others = [e for e in find_named_entities(quote, sc) if not _matches_issuer(e[0], issuer_names)]
    if others and not _issuer_named(quote, issuer_names, [(e[1], e[2]) for e in others]):
        return False, f"quote is about another entity: {others[0][0]}"
    return True, None


def _temporal_check(
    quote: str, document_date: date | None, period: str | None
) -> tuple[bool, str | None]:
    if document_date is None:
        return True, None
    for found, _, _ in find_dates(quote):
        if found > document_date + timedelta(days=FUTURE_TOLERANCE_DAYS):
            return False, f"quote dated {found.isoformat()}, after the document date"
    if period:
        years = [int(y) for y in re.findall(r"\b(20\d\d)\b", period)]
        if years and not all(
            document_date.year - PERIOD_PAST_YEARS <= y <= document_date.year + PERIOD_FUTURE_YEARS
            for y in years
        ):
            return (
                False,
                f"period {period!r} is not a plausible period for {document_date.isoformat()}",
            )
    return True, None


# --------------------------------------------------------------- liquidity --- #

# Words of a deterioration or a worry about liquidity, as issuers write them.
_LIQUIDITY_NEGATIVE_RE = re.compile(
    r"(?i)\b(?:constrain(?:ed|ts?)|tighten(?:ed|ing|s)?|tight|strain(?:ed|s)?|stress(?:ed)?|"
    r"pressures?|shortfalls?|squeeze[sd]?|insufficien(?:t|cy)|deteriorat(?:ed|ing|ion|es)|"
    r"weaken(?:ed|ing|s)?|reduc(?:ed|ing|tion)\s+(?:in\s+|of\s+)?(?:\w+\s+){0,2}liquidity|"
    r"concerns?|uncertaint(?:y|ies)|substantial\s+doubt|"
    r"adversely\s+(?:impact|affect)(?:ed|ing|s)?|unable\s+to\s+(?:meet|fund|service|repay)|"
    r"difficult(?:y|ies)\s+(?:in\s+)?(?:meeting|funding|refinancing)|at\s+risk|risk\s+of)\b"
)
# A negated positive is itself a worry ("does not expect to have sufficient liquidity",
# "may not be sufficient", "no assurance that ... will be sufficient"): the negation is part
# of the marker, so the negation window rule does not apply to it.
_LIQUIDITY_NEGATED_POSITIVE_RE = re.compile(
    r"(?i)\b(?:not|no|never|cannot)\b[^.;]{0,60}?\b(?:sufficient|adequate|enough|able\s+to\s+"
    r"(?:meet|maintain|fund|service|continue)|assurance)\b"
)
_NEGATION_RE = re.compile(r"(?i)\b(?:no|not|without|never|neither|nor|free\s+of|absence\s+of)\b")
# The passage must speak of liquidity, cash, or committed lines and facilities of credit.
_LIQUIDITY_TOPIC_RE = re.compile(
    r"liquidity|\bcash\b|credit (?:line|lines|facilit(?:y|ies))|lines? of credit|"
    r"revolving|liquid assets"
)
NEGATIVE_LIQUIDITY_STATUSES = frozenset({"deteriorated", "concern"})


def _negated(sentence: str, marker_start: int) -> bool:
    """A negation word within the five words before the marker in the same sentence."""
    before = sentence[:marker_start].split()
    return any(_NEGATION_RE.fullmatch(w.strip(",;:()")) for w in before[-5:])


# "may / could / might / would adversely affect our liquidity" with nothing else negative in
# the window is a hypothetical risk factor, not a present worry (Phase 3.4a closing rule):
# the statement stays valid as mentioned, it never sets the flag.
_ADVERSE_EFFECT_RE = re.compile(r"(?i)^adversely\s+(?:impact|affect)")
# "May 8, 2026" is a date, not a modal
_HYPOTHETICAL_MODAL_RE = re.compile(r"(?i)\b(?:may|could|might|would)\b(?!\s+\d)")


def liquidity_polarity_ok(quote: str, status: str) -> tuple[bool, str | None]:
    """A negative status needs a present negative marker that is not negated in its window.
    Positive and neutral statuses are never rejected here: "no liquidity concerns" read as
    stable or mentioned is right, read as concern it is a POLARITY_MISMATCH; a conditional
    adverse effect alone is a HYPOTHETICAL_RISK that fits mentioned."""
    if status not in NEGATIVE_LIQUIDITY_STATUSES:
        return True, None
    sentences = [s.text for s in iter_sentences(quote)] or [quote]
    found_negated = found_hypothetical = False
    for sentence in sentences:
        if _LIQUIDITY_NEGATED_POSITIVE_RE.search(sentence):
            return True, None
        for m in _LIQUIDITY_NEGATIVE_RE.finditer(sentence):
            if _negated(sentence, m.start()):
                found_negated = True
                continue
            if _ADVERSE_EFFECT_RE.match(m.group(0)) and _HYPOTHETICAL_MODAL_RE.search(
                sentence[: m.start()]
            ):
                found_hypothetical = True
                continue
            return True, None
    if found_hypothetical:
        return (
            False,
            "POLARITY_MISMATCH: HYPOTHETICAL_RISK, only a conditional adverse effect and no "
            f"present deterioration, status {status} is not supported (mentioned fits)",
        )
    if found_negated:
        return (
            False,
            f"POLARITY_MISMATCH: the passage negates the worry, status {status} contradicts it",
        )
    return (
        False,
        f"POLARITY_MISMATCH: no deterioration or worry wording supports status {status}",
    )


def validate_liquidity_statement(
    st: LiquidityStatement,
    doc: RawDocument,
    issuer_names: list[str],
    *,
    document_date: date | None,
    scales: RatingScales | None = None,
    segments: list[str] | None = None,
) -> ValidationResult:
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    quote = st.evidence_quote
    low = quote.casefold()

    kind, score, m_start, m_end = _span_match(quote, doc.text, st.start_offset, st.end_offset)
    checks["span_match"] = kind != "none"
    if kind == "none":
        reasons.append(f"quote not found in the document (best partial ratio {score:.0f})")

    numbers_ok = True
    if st.value is not None:
        numbers_ok = _contains(_numbers_in(quote), float(st.value))
        if not numbers_ok:
            reasons.append(f"numbers not in the quote: [{st.value}]")
    checks["numbers_match"] = numbers_ok

    unit_ok = True
    if st.value is not None and st.unit is not None:
        currency_tokens, scale_tokens = _UNIT_TOKENS[st.unit]
        unit_ok = any(t in low for t in scale_tokens) and (
            not currency_tokens or any(t in low for t in currency_tokens)
        )
        if not unit_ok:
            reasons.append(f"unit {st.unit} not supported by the quote")
    checks["unit_match"] = unit_ok

    label = (st.metric_label or "").casefold().strip()
    label_ok = not label or label in low
    checks["metric_match"] = label_ok
    if not label_ok:
        reasons.append(f"metric label {st.metric_label!r} not in the quote")

    checks["liquidity_named"] = bool(_LIQUIDITY_TOPIC_RE.search(low))
    if not checks["liquidity_named"]:
        reasons.append("the passage does not speak of liquidity, cash or credit lines")

    # An agency report (S&P, Moody's, Fitch, DBRS) is a third party: whatever it says about
    # the issuer's liquidity is never the issuer's own statement (gold V1 rule third_party).
    document_type = (doc.extra or {}).get("document_type") if isinstance(doc.extra, dict) else None
    checks["issuer_voice"] = document_type != "rating_report"
    if not checks["issuer_voice"]:
        reasons.append(
            "THIRD_PARTY_DOCUMENT: an agency report never carries the issuer's own "
            "liquidity statement"
        )

    polarity_ok, polarity_reason = liquidity_polarity_ok(quote, st.status)
    checks["polarity_match"] = polarity_ok
    if polarity_reason:
        reasons.append(polarity_reason)

    entity_ok, entity_reason = _entity_check(quote, issuer_names, scales)
    checks["entity_match"] = entity_ok
    if entity_reason:
        reasons.append(entity_reason)

    temporal_ok, temporal_reason = _temporal_check(quote, document_date, st.period)
    checks["temporal_consistency"] = temporal_ok
    if temporal_reason:
        reasons.append(temporal_reason)

    segment = _segment_named(st.metric_label or "", quote, segments or [])
    checks["scope_match"] = segment is None
    if segment is not None:
        reasons.append(
            f"OUT_OF_SCOPE_SEGMENT: {segment} is a segment of the issuer, liquidity is read at "
            "Group level or for the principal division"
        )

    status = "VALID" if all(checks.values()) else "INVALID"
    return ValidationResult(
        status=status, checks=checks, reasons=reasons, match_kind=kind, match_score=score,
        matched_start=m_start, matched_end=m_end,
    )  # fmt: skip


# --------------------------------------------------------------- covenants --- #

_COVENANT_TOPIC_RE = re.compile(
    r"(?i)covenant|non-?compliance|in\s+compliance\s+with|compl(?:y|ied)\s+with|waiv(?:er|ed)|forbearance"
)
# A breach actually stated (present or past). "event of default" counts only when the window
# ties it to a covenant or a non-compliance.
_BREACH_RE = re.compile(
    r"(?i)\bnot\s+in\s+compliance\b|\bnon-?compliance\b|\bbreach(?:ed|es|ing)?\b|"
    r"\bviolat(?:ed|ion|ions)\b|\bfail(?:ed|ure)\s+to\s+(?:comply|meet|maintain|satisfy)\b|"
    r"\b(?:has|have|had)\s+not\s+met\b|\bdid\s+not\s+(?:meet|comply|satisfy)\b|"
    r"\bevents?\s+of\s+default\b"
)
_DEFAULT_RE = re.compile(r"(?i)\bevents?\s+of\s+default\b")
_COVENANT_LINK_RE = re.compile(r"(?i)covenant|compliance")
_COMPLIANT_RE = re.compile(
    r"(?i)(?<!not )(?<!not\n)\bin\s+compliance\s+with\b|"
    r"\b(?:are|were|is|was|remain(?:s|ed)?)\s+in\s+compliance\b(?!\s+with\s+(?:certain\s+)?nasdaq)|"
    r"\bcomplied\s+with\b|\bno\s+(?:breach|violation|default|event\s+of\s+default)\b|"
    r"\bnot\s+in\s+(?:breach|default)\b"
)
_RISK_RE = re.compile(
    r"(?i)\bwill\s+not\s+(?:remain|be)\s+in\s+compliance\b|\banticipates?\s+that\s+it\s+will\s+not\b|"
    r"\b(?:does|do)\s+not\s+expect\s+to\s+be\s+able\b|\bexpects?\s+(?:to\s+be\s+unable|not\s+to\s+be\s+able)\b|"
    r"\bmay\s+(?:not\s+)?(?:breach|comply|be\s+able\s+to\s+(?:meet|comply|maintain))\b|"
    r"\bcould\s+(?:breach|result\s+in)\b|\brisk\s+of\s+(?:a\s+)?(?:breach|non-?compliance|default)\b|"
    r"\b(?:future|potential)\s+non-?compliance\b|\bunable\s+to\s+(?:meet|comply|maintain)\b"
)
_HYPOTHETICAL_PREFIX_RE = re.compile(r"(?i)\b(?:future|potential|any|if|should|were)\b")
# wording of a forward-looking passage (a future covenant test period is then legitimate)
_PROSPECTIVE_RE = re.compile(
    r"(?i)\b(?:expects?|expected|anticipates?|anticipated|will|would|may|might|could|shall|"
    r"forecasts?|projected?|going\s+forward|for\s+the\s+(?:fiscal\s+)?quarter(?:s)?\s+ending)\b"
)
SALVAGEABLE_CHECKS = frozenset({"agreement_match", "covenant_label_match"})
_RESOLUTION_RE = {
    "waived": re.compile(r"(?i)\bwaiv(?:ed|er|ers)\b"),
    "cured": re.compile(r"(?i)\bcured?\b|\bremed(?:ied|iation|y)\b"),
    "amended": re.compile(r"(?i)\bamend(?:ed|ment|ments)\b"),
}


# "net worth was above the required covenant", "ratio was below the maximum": compliance by
# an explicit comparison, accepted only when the same window names a covenant or a
# contractual requirement (a bare "ratio above 10%" is not a compliance statement)
_COMPLIANT_COMPARISON_RE = re.compile(
    r"(?i)\b(?:above|exceed(?:s|ed)?|in\s+excess\s+of|below|within|less\s+than|"
    r"did\s+not\s+exceed|does\s+not\s+exceed)\b[^;\n]{0,80}?\b(?:required|requirement|minimum|"
    r"maximum|threshold)\b"
)
_COVENANT_WORD_RE = re.compile(
    r"(?i)\bcovenants?\b|\bcontractual\s+requirement|\bcredit\s+(?:agreement|facility)\b"
)


def _compliance_stated(sentence: str) -> bool:
    if _COMPLIANT_RE.search(sentence):
        return True
    return bool(_COMPLIANT_COMPARISON_RE.search(sentence) and _COVENANT_WORD_RE.search(sentence))


def _actual_breach(sentence: str) -> bool:
    """A breach stated for the issuer, neither negated nor hypothetical; an event of default
    only with a covenant or compliance word in the window."""
    for m in _BREACH_RE.finditer(sentence):
        if _negated(sentence, m.start()):
            continue
        before = sentence[: m.start()]
        words = before.split()[-4:]
        if any(_HYPOTHETICAL_PREFIX_RE.fullmatch(w.strip(",;:()")) for w in words):
            continue
        if _HYPOTHETICAL_MODAL_RE.search(before) and not re.search(
            r"(?i)\b(?:was|were|is|are|has|have|had|received|obtained|occurred|arose|"
            r"entered|remained|failed|did)\b",
            before,
        ):
            continue
        if _DEFAULT_RE.fullmatch(m.group(0)) and not _COVENANT_LINK_RE.search(sentence):
            continue
        return True
    return False


def covenant_status_ok(quote: str, status: str) -> tuple[bool, str | None]:
    """status against the words of the passage: breached needs a breach actually stated,
    compliant needs a compliance statement and no stated breach, risk_of_breach needs an
    anticipation, mentioned is never rejected here."""
    if status == "mentioned":
        return True, None
    sentences = [s.text for s in iter_sentences(quote)] or [quote]
    breach = any(_actual_breach(s) for s in sentences)
    compliant = any(_compliance_stated(s) for s in sentences)
    risk = any(_RISK_RE.search(s) for s in sentences)
    if status == "breached":
        if breach:
            return True, None
        return False, (
            "STATUS_MISMATCH: no breach or non-compliance actually stated for a covenant "
            "(a payment default, a hypothetical or a negated breach does not count)"
        )
    if status == "compliant":
        if compliant and not breach:
            return True, None
        if breach:
            return False, "STATUS_MISMATCH: the passage states a breach, compliant contradicts it"
        return False, "STATUS_MISMATCH: no compliance statement in the passage"
    if status == "risk_of_breach":
        if risk:
            return True, None
        return False, "STATUS_MISMATCH: no anticipation of a breach in the passage"
    return True, None


def covenant_resolution_ok(quote: str, resolution: str) -> tuple[bool, str | None]:
    pattern = _RESOLUTION_RE.get(resolution)
    if pattern is None or pattern.search(quote):
        return True, None
    return (
        False,
        f"RESOLUTION_MISMATCH: resolution {resolution} has no supporting word in the passage",
    )


def validate_covenant_statement(
    st: CovenantStatement,
    doc: RawDocument,
    issuer_names: list[str],
    *,
    document_date: date | None,
    scales: RatingScales | None = None,
    segments: list[str] | None = None,
) -> ValidationResult:
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    quote = st.evidence_quote
    low = quote.casefold()

    kind, score, m_start, m_end = _span_match(quote, doc.text, st.start_offset, st.end_offset)
    checks["span_match"] = kind != "none"
    if kind == "none":
        reasons.append(f"quote not found in the document (best partial ratio {score:.0f})")

    checks["covenant_named"] = bool(_COVENANT_TOPIC_RE.search(quote))
    if not checks["covenant_named"]:
        reasons.append("the passage does not speak of covenants or contractual compliance")

    salvaged: dict[str, str] = {}
    for field_name, value in (("covenant_label", st.covenant_label), ("agreement", st.agreement)):
        if value and value.casefold().strip() not in low:
            checks[f"{field_name}_match"] = False
            salvaged[field_name] = value
            reasons.append(
                f"{field_name} {value!r} not in the quote: field dropped, statement kept "
                "(field-level salvage, the field is metadata, not the decision)"
            )
        else:
            checks[f"{field_name}_match"] = True

    status_ok, status_reason = covenant_status_ok(quote, st.status)
    checks["status_match"] = status_ok
    if status_reason:
        reasons.append(status_reason)
    # a breach the passage dates more than a year before the document recalls history: it
    # stays a mention, it never creates a covenant event today
    historical = st.status == "breached" and is_historical(quote, document_date)
    checks["historical_reference"] = not historical
    if historical:
        reasons.append(
            "HISTORICAL_REFERENCE: the breach is dated more than a year before the document, "
            "a mention of history, not a current breach"
        )
    resolution_ok, resolution_reason = covenant_resolution_ok(quote, st.resolution)
    checks["resolution_match"] = resolution_ok
    if resolution_reason:
        reasons.append(resolution_reason)

    document_type = (doc.extra or {}).get("document_type") if isinstance(doc.extra, dict) else None
    checks["issuer_voice"] = document_type != "rating_report"
    if not checks["issuer_voice"]:
        reasons.append(
            "THIRD_PARTY_DOCUMENT: an agency report never carries the issuer's own "
            "covenant statement"
        )

    entity_ok, entity_reason = _entity_check(quote, issuer_names, scales)
    checks["entity_match"] = entity_ok
    if entity_reason:
        reasons.append(entity_reason)

    temporal_ok, temporal_reason = _temporal_check(quote, document_date, st.period)
    if not temporal_ok and temporal_reason and "after the document date" in temporal_reason:
        # a future covenant test period is legitimate when the passage is prospective and
        # claims no breach already realised ("expects that it will not be in compliance as
        # of September 30"); a breach claimed at a future date stays inconsistent
        if st.status != "breached" and _PROSPECTIVE_RE.search(quote):
            temporal_ok, temporal_reason = True, None
    checks["temporal_consistency"] = temporal_ok
    if temporal_reason:
        reasons.append(temporal_reason)

    segment = _segment_named(st.covenant_label or "", quote, segments or [])
    checks["scope_match"] = segment is None
    if segment is not None:
        reasons.append(
            f"OUT_OF_SCOPE_SEGMENT: {segment} is a segment of the issuer, covenants are read at "
            "Group level or for the principal division"
        )

    decisive = {k: v for k, v in checks.items() if k not in SALVAGEABLE_CHECKS}
    status = "VALID" if all(decisive.values()) else "INVALID"
    return ValidationResult(
        status=status, checks=checks, reasons=reasons, match_kind=kind, match_score=score,
        matched_start=m_start, matched_end=m_end, salvaged=salvaged,
    )  # fmt: skip


def validate_statement(
    st: GuidanceStatement,
    doc: RawDocument,
    issuer_names: list[str],
    *,
    document_date: date | None,
    scales: RatingScales | None = None,
    segments: list[str] | None = None,
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
    if bounds:
        unit_ok = any(t in low for t in scale_tokens) and (
            not currency_tokens or any(t in low for t in currency_tokens)
        )
    else:
        unit_ok = True  # a mentioned statement carries no number, so no unit to support
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

    entity_ok, entity_reason = _entity_check(quote, issuer_names, scales)
    checks["entity_match"] = entity_ok
    if entity_reason:
        reasons.append(entity_reason)

    temporal_ok, temporal_reason = _temporal_check(quote, document_date, st.period)
    checks["temporal_consistency"] = temporal_ok
    if temporal_reason:
        reasons.append(temporal_reason)

    segment = _segment_named(st.metric_label, quote, segments or [])
    checks["scope_match"] = segment is None
    if segment is not None:
        reasons.append(
            f"OUT_OF_SCOPE_SEGMENT: {segment} is a segment of the issuer, guidance is read at "
            "Group level or for the principal division"
        )

    status = "VALID" if all(checks.values()) else "INVALID"
    return ValidationResult(
        status=status, checks=checks, reasons=reasons, match_kind=kind, match_score=score,
        matched_start=m_start, matched_end=m_end,
    )  # fmt: skip
