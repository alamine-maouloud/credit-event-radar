"""Deterministic extraction of bond issuance, redemption and non-call statements.

Version ``structured-issuance-1.0``: one sentence must name an instrument (bond, notes,
debentures, Schuldschein), an action (issued, placed, priced, launched, redeemed, decided
not to call, tapped) and, for an issuance, an amount with a currency. Nothing is inferred:
no currency conversion (``amount_eur_equiv`` only for EUR), no amount guessed from a total
that also covers a loan, no event from a mere intention.
"""

from __future__ import annotations

import re
from datetime import date

from radar.extract.dates import first_date
from radar.extract.spans import exact_span, iter_sentences
from radar.extract.structured import Extraction, Skipped, event_id_for
from radar.models import CreditEvent, EvidenceSpan, RawDocument

ISSUANCE_EXTRACTOR_VERSION = "structured-issuance-1.0"

_INSTRUMENT_RE = re.compile(r"(?i)\b(bonds?|notes|debentures|schuldschein(?:darlehen)?)\b")
_COMPLETION_RE = re.compile(
    r"(?i)\b(issued|issues|placed|places|priced|prices|launched|launches|completed|sold|raised|successfully)\b"
)
_ISSUE_NOUN_RE = re.compile(r"(?i)\b(issuance|issue|placement|offering)\b")
_REDEEM_RE = re.compile(
    r"(?i)\b(early redemption|redemption|redeem(?:s|ed|ing)?|repay(?:s|ed|ment|ing)?|will call|has called|"  # noqa: E501
    r"decided to call|exercis(?:es|ed|ing) (?:its|the) (?:call|redemption) option)\b"
)
_NON_CALL_RE = re.compile(
    r"(?i)\b(not to call|will not call|decided not to call|does not intend to call|non-call|"
    r"not to redeem|will not redeem)\b"
)
_TAP_RE = re.compile(
    r"(?i)\b(tap(?:s|ped)?|re-?open(?:s|ed|ing)?|increas(?:es|ed) the (?:size|volume))\b"
)
_INTENT_RE = re.compile(
    r"(?i)\b(considers?|considering|intends? to|plans? to|may issue|could issue|subject to market conditions|mandated)\b"  # noqa: E501
)
_LOAN_RE = re.compile(r"(?i)\b(loans?|facility|facilities)\b")
_TOTAL_RE = re.compile(r"(?i)\b(total|aggregate|combined)\b")
_CURRENCIES = {
    "EUR": "EUR", "€": "EUR", "EURO": "EUR", "EUROS": "EUR", "USD": "USD", "US$": "USD", "$": "USD",
    "DOLLAR": "USD", "DOLLARS": "USD", "GBP": "GBP", "£": "GBP", "CHF": "CHF", "SEK": "SEK",
    "NOK": "NOK", "DKK": "DKK", "JPY": "JPY",
}  # fmt: skip
_UNITS = {"billion": 1e9, "bn": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6}
_AMOUNT_CUR_FIRST = re.compile(
    r"(?P<cur>EUR|USD|GBP|CHF|SEK|NOK|DKK|JPY|US\$|\$|€|£)\s?(?P<num>\d(?:[\d,.]*\d)?)"
    r"(?:\s?(?P<unit>billion|bn|million|mn|m)\b)?",
    re.IGNORECASE,
)
_AMOUNT_NUM_FIRST = re.compile(
    r"(?P<num>\d(?:[\d,.]*\d)?)\s?(?P<unit>billion|bn|million|mn)\s?(?P<cur>euros?|EUR|US\s?dollars?|USD|dollars?)\b",
    re.IGNORECASE,
)
_COUPON_RE = re.compile(r"(?P<pct>\d+(?:\.\d+)?)\s?%")
_COUPON_CONTEXT_RE = re.compile(r"(?i)\b(coupon|interest|p\.a\.|per annum|carries|bears|rate)\b")
_DUE_RE = re.compile(r"(?i)\b(?:due|maturing|maturity(?:\s+date)?)\s+(?:on\s+|in\s+|of\s+)?")
_YEAR_RE = re.compile(r"\b(20\d\d)\b")
_TENOR_WORDS = {"three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30}  # fmt: skip  # noqa: E501
_TENOR_RE = re.compile(
    r"(?i)\b(?:term of\s+|tenor of\s+)?(?P<n>\d{1,2}|"
    + "|".join(_TENOR_WORDS)
    + r")[\s-]year(?:s)?\b"
)
_GREEN_RE = re.compile(r"(?i)\bgreen (?:bonds?|notes|loan)\b")
_SENIORITY = [
    ("AT1", re.compile(r"(?i)\b(additional tier 1|at1)\b")),
    ("T2", re.compile(r"(?i)\b(tier 2|t2)\b")),
    ("hybrid", re.compile(r"(?i)\bhybrid\b")),
    ("subordinated", re.compile(r"(?i)\bsubordinated\b")),
    ("senior", re.compile(r"(?i)\bsenior\b")),
]


def _parse_number(num: str) -> float | None:
    cleaned = num.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", cleaned):
        cleaned = cleaned.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", cleaned):
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _find_amount(text: str) -> tuple[float, str, int, int] | None:
    for pattern in (_AMOUNT_CUR_FIRST, _AMOUNT_NUM_FIRST):
        for m in pattern.finditer(text):
            value = _parse_number(m.group("num"))
            if value is None:
                continue
            unit = (m.group("unit") or "").lower()
            if unit:
                value *= _UNITS[unit]
            elif value < 1000:
                continue  # "EUR 3" is not an issuance amount
            currency = _CURRENCIES.get(m.group("cur").upper().replace(" ", ""))
            if currency is None:
                continue
            return value, currency, m.start(), m.end()
    return None


def _maturity(text: str) -> tuple[str | None, int, int] | None:
    m = _DUE_RE.search(text)
    if not m:
        return None
    rest = text[m.end() : m.end() + 40]
    found = first_date(rest)
    if found and found[1] == 0:
        return found[0].isoformat(), m.end(), m.end() + found[2]
    y = _YEAR_RE.match(rest)
    if y:
        return y.group(1), m.end(), m.end() + y.end()
    return None


def _is_terms_sentence(text: str) -> bool:
    """Instrument plus amount plus at least one term (coupon, maturity, tenor)."""
    if not _INSTRUMENT_RE.search(text) or _find_amount(text) is None:
        return False
    return bool(
        (_COUPON_CONTEXT_RE.search(text) and _COUPON_RE.search(text))
        or _maturity(text)
        or _TENOR_RE.search(text)
    )


def extract_issuance_events(doc: RawDocument, issuer_id: str) -> Extraction:
    candidates: list[tuple[int, CreditEvent]] = []
    skipped: list[Skipped] = []
    published = doc.published_at.date() if doc.published_at else None
    sentences = iter_sentences(doc.text)
    document_announces_issuance = any(
        _INSTRUMENT_RE.search(s.text)
        and _COMPLETION_RE.search(s.text)
        and not _NON_CALL_RE.search(s.text)
        and not _REDEEM_RE.search(s.text)
        for s in sentences
    )
    for sentence in sentences:
        text = sentence.text
        if not _INSTRUMENT_RE.search(text):
            continue
        if _NON_CALL_RE.search(text):
            event_type = "non_call"
        elif _REDEEM_RE.search(text):
            event_type = "redemption"
        elif _TAP_RE.search(text) and _COMPLETION_RE.search(text):
            event_type = "tap"
        elif _COMPLETION_RE.search(text) or _ISSUE_NOUN_RE.search(text):
            if _INTENT_RE.search(text) and not _COMPLETION_RE.search(text):
                skipped.append(Skipped("intent_only", sentence.start, sentence.end, text))
                continue
            if not _COMPLETION_RE.search(text):
                continue
            event_type = "new_issue"
        elif document_announces_issuance and _is_terms_sentence(text):
            event_type = "new_issue"  # terms of the announced instrument, same document
        else:
            continue
        if _LOAN_RE.search(text) and _TOTAL_RE.search(text):
            skipped.append(Skipped("ambiguous_total_with_loan", sentence.start, sentence.end, text))
            continue
        amount = _find_amount(text)
        if amount is None:
            if event_type in ("new_issue", "tap"):
                skipped.append(Skipped("no_amount", sentence.start, sentence.end, text))
                continue
        evidence: list[EvidenceSpan] = [
            exact_span(
                doc, sentence.start, sentence.end, extractor_version=ISSUANCE_EXTRACTOR_VERSION
            )
        ]
        fields: dict = {
            "amount": None, "currency": None, "amount_eur_equiv": None, "coupon": None, "maturity": None,  # noqa: E501
            "tenor_years": None, "seniority": None, "use_of_proceeds": None, "green": bool(_GREEN_RE.search(text)),  # noqa: E501
            "extractor_version": ISSUANCE_EXTRACTOR_VERSION,
        }  # fmt: skip
        if amount:
            value, currency, a0, a1 = amount
            fields.update(
                {
                    "amount": value,
                    "currency": currency,
                    "amount_eur_equiv": value if currency == "EUR" else None,
                }
            )
            evidence.append(
                exact_span(
                    doc,
                    sentence.start + a0,
                    sentence.start + a1,
                    extractor_version=ISSUANCE_EXTRACTOR_VERSION,
                    field="amount",
                )
            )
        if _COUPON_CONTEXT_RE.search(text):
            c = _COUPON_RE.search(text)
            if c:
                fields["coupon"] = float(c.group("pct"))
                evidence.append(
                    exact_span(
                        doc,
                        sentence.start + c.start(),
                        sentence.start + c.end(),
                        extractor_version=ISSUANCE_EXTRACTOR_VERSION,
                        field="coupon",
                    )
                )
        mat = _maturity(text)
        if mat:
            fields["maturity"] = mat[0]
            evidence.append(
                exact_span(
                    doc,
                    sentence.start + mat[1],
                    sentence.start + mat[2],
                    extractor_version=ISSUANCE_EXTRACTOR_VERSION,
                    field="maturity",
                )
            )
        t = _TENOR_RE.search(text)
        if t:
            n = t.group("n").lower()
            fields["tenor_years"] = int(n) if n.isdigit() else _TENOR_WORDS[n]
        for name, pattern in _SENIORITY:
            m = pattern.search(text)
            if m:
                fields["seniority"] = name
                evidence.append(
                    exact_span(
                        doc,
                        sentence.start + m.start(),
                        sentence.start + m.end(),
                        extractor_version=ISSUANCE_EXTRACTOR_VERSION,
                        field="seniority",
                    )
                )
                break
        effective: date | None = published
        if effective is None:
            found = first_date(text)
            if found:
                effective = found[0]
        key = {
            "event_type": event_type, "amount": fields["amount"], "currency": fields["currency"],
            "seniority": fields["seniority"], "maturity": fields["maturity"],
            "date": effective.isoformat() if effective else doc.doc_id,
        }  # fmt: skip
        specificity = sum(
            1
            for k in ("amount", "coupon", "maturity", "tenor_years", "seniority")
            if fields[k] is not None
        )
        candidates.append(
            (
                specificity,
                CreditEvent(
                    event_id=event_id_for(issuer_id, "issuance", event_type, key),
                    issuer_id=issuer_id,
                    family="issuance",
                    event_type=event_type,
                    effective_date=effective,
                    fields=fields,
                    evidence=evidence,
                    extraction_method="structured",
                    source_doc_ids=[doc.doc_id],
                ),
            )
        )
    events: list[CreditEvent] = []
    for event_type in ("new_issue", "tap", "redemption", "non_call"):
        same = [c for c in candidates if c[1].event_type == event_type]
        if not same:
            continue
        best = max(same, key=lambda c: c[0])  # first among equals
        events.append(best[1])
        for _spec, other in same:
            if other is not best[1]:
                span = other.evidence[0]
                skipped.append(
                    Skipped(
                        "superseded_by_more_specific_sentence",
                        span.char_start,
                        span.char_end,
                        other.event_type,
                    )
                )
    return Extraction(events, skipped)
