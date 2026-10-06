"""Deterministic extraction without any LLM (docs/SPEC.md section 8, step 4, structured path).

Two extractors:

- ``extract_rating_actions``: rating transitions written as "<agency> ... lowered/raised
  ... from <label> to <label>" inside one sentence, with labels validated against
  ``rating_scales.yaml``. Version ``structured-rating-1.0`` only looks inside a sentence;
  table rows and footnotes are future evidence types (EvidenceSpan.evidence_type).
- ``extract_edgar_items``: 8-K item codes and prospectus forms mapped to event skeletons
  (docs/SPEC.md section 6.1).

Everything that does not match exactly is dropped and reported through ``Skipped`` so the
caller can log it. Nothing is inferred.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from radar.audit import stable_hash
from radar.extract.dates import find_dates, first_date
from radar.extract.spans import exact_span, iter_sentences
from radar.models import CreditEvent, EvidenceSpan, RatingFields, RawDocument
from radar.ratings import RatingScales, category, is_at_boundary, notch_delta, to_notch

EXTRACTOR_VERSION = "structured-rating-1.1"
EDGAR_EXTRACTOR_VERSION = "structured-edgar-1.0"

DOWNGRADE_VERBS = ("lowered", "downgraded", "cut", "reduced")
UPGRADE_VERBS = ("raised", "upgraded")
_VERB_RE = re.compile(r"\b(" + "|".join(DOWNGRADE_VERBS + UPGRADE_VERBS) + r")\b", re.IGNORECASE)
_SHORT_TERM_RE = re.compile(r"short[\s-]term", re.IGNORECASE)
_OUTLOOK_WORDS = r"(stable|negative|positive|developing)"
_OUTLOOK_RE = re.compile(
    rf"\boutlook\b[^.;]{{0,40}}?\b{_OUTLOOK_WORDS}\b|\b{_OUTLOOK_WORDS}\s+outlook\b", re.IGNORECASE
)
_WATCH_RE = re.compile(
    r"\b(?:CreditWatch|credit watch|watch)\s+(?:with\s+)?(negative|positive|developing)\b"
    r"|\breview for (downgrade|upgrade)\b",
    re.IGNORECASE,
)

EDGAR_ITEM_EVENTS: dict[str, tuple[str, str]] = {
    "2.02": ("earnings", "earnings_release"),
    "2.03": ("issuance", "direct_financial_obligation"),
    "2.04": ("other", "obligation_acceleration"),
    "2.06": ("earnings", "material_impairment"),
    "1.01": ("other", "material_agreement"),
    "2.01": ("other", "acquisition_or_disposition"),
    "5.02": ("other", "management_change"),
}
EDGAR_FORM_EVENTS: dict[str, tuple[str, str]] = {
    "424B2": ("issuance", "prospectus_supplement"),
    "424B5": ("issuance", "prospectus_supplement"),
    "FWP": ("issuance", "free_writing_prospectus"),
}


@dataclass(frozen=True)
class Skipped:
    """A candidate that was found but rejected, with the deterministic reason."""

    reason: str
    char_start: int
    char_end: int
    detail: str


@dataclass
class Extraction:
    events: list[CreditEvent]
    skipped: list[Skipped]


class _Patterns:
    """Regexes compiled once per RatingScales instance."""

    def __init__(self, scales: RatingScales) -> None:
        names: list[tuple[str, str]] = []
        for agency_id, agency in scales.agencies.items():
            for name in [agency.display_name, *agency.aliases]:
                names.append((name, agency_id))
        names.sort(key=lambda n: len(n[0]), reverse=True)
        self.by_name = {n.casefold(): a for n, a in names}
        self.agency_re = re.compile(
            r"(?<![A-Za-z])(" + "|".join(re.escape(n) for n, _ in names) + r")(?![A-Za-z])",
            re.IGNORECASE,
        )
        self.transition_re: dict[str, re.Pattern[str]] = {}
        self.label_alt: dict[str, str] = {}
        for agency_id, agency in scales.agencies.items():
            labels = sorted(agency.scale, key=len, reverse=True)
            alt = "|".join(_label_pattern(label) for label in labels)
            self.label_alt[agency_id] = alt
            self.transition_re[agency_id] = re.compile(
                rf"\bfrom\s+(?P<old>{alt})(?![A-Za-z0-9+\-])\s+to\s+(?P<new>{alt})(?![A-Za-z0-9+\-])"
            )


_DASH_CLASS = "[-\u2010\u2011\u2012\u2013\u2212]"


def _label_pattern(label: str) -> str:
    """Escaped label whose hyphens also match typographic dashes."""
    return "".join(_DASH_CLASS if ch == "-" else re.escape(ch) for ch in label)


_LABEL_BOUNDARY_BEFORE = r"(?<![A-Za-z0-9+\-])"
_LABEL_BOUNDARY_AFTER = r"(?![A-Za-z0-9+\-])"
_OUTLOOK_WORDS = r"(stable|negative|positive|developing|evolving)"
_OUTLOOK_REVISION_RES = [
    re.compile(
        r"(?i:\b(?:revis(?:ed|es|ing)|chang(?:ed|es|ing)|lower(?:ed|s|ing)|rais(?:ed|es|ing)|mov(?:ed|es|ing)|set)"
        r"\s+(?:the\s+|its\s+|their\s+)?(?:rating\s+|long-term\s+)?outlooks?\b[^.;]{0,120}?\bto\s+)"
        rf"(?i:{_OUTLOOK_WORDS})\b(?i:(?:\s+from\s+{_OUTLOOK_WORDS}\b)?)"
    ),
    re.compile(
        r"(?i:\boutlooks?\s+(?:(?:was|were|has been|have been|is|are)\s+)?(?:revised|changed|lowered|raised|moved)\s+to\s+)"  # noqa: E501
        rf"(?i:{_OUTLOOK_WORDS})\b(?i:(?:\s+from\s+{_OUTLOOK_WORDS}\b)?)"
    ),
    re.compile(rf"(?i:\boutlooks?\s+to\s+{_OUTLOOK_WORDS}\s+from\s+{_OUTLOOK_WORDS}\b)"),
]
_WATCH_RES = [
    re.compile(
        r"(?i:\b(?:placed|places|put|puts|remains?|kept|keeps)\b[^.;]{0,120}?\bon\s+"
        r"(?:creditwatch|credit\s+watch|rating\s+watch|watch)\s+(?:with\s+)?"
        r"(negative|positive|developing|evolving)\b)"
    ),
    re.compile(r"(?i:\b(?:under|on)\s+review\s+for\s+(downgrade|upgrade)\b)"),
    re.compile(r"(?i:\bcreditwatch\s+(negative|positive|developing)\b)"),
]
_WATCH_VALUE = {"downgrade": "negative", "upgrade": "positive", "evolving": "developing"}
_RATING_NOUN_RE = re.compile(r"(?i:\b(?:ratings?|idr|issuer|long-term|senior|notes|debt)\b)")
_QUOTES = "'\"\u2018\u2019\u201c\u201d"

_PATTERN_CACHE: dict[int, _Patterns] = {}


def _patterns(scales: RatingScales) -> _Patterns:
    key = id(scales)
    if key not in _PATTERN_CACHE:
        _PATTERN_CACHE[key] = _Patterns(scales)
    return _PATTERN_CACHE[key]


def parse_us_date(text: str) -> tuple[date, int, int] | None:
    """First date in ``text`` with its offsets (see radar.extract.dates for the forms)."""
    return first_date(text)


HEADER_CHARS = 600


def document_header_date(doc: RawDocument) -> tuple[date, int, int] | None:
    """First date in the document header, used when a sentence carries no date."""
    found = find_dates(doc.text[:HEADER_CHARS])
    return found[0] if found else None


def event_id_for(issuer_id: str, family: str, event_type: str, key: dict[str, object]) -> str:
    return (
        "evt_"
        + stable_hash({"issuer_id": issuer_id, "family": family, "type": event_type, **key})[:20]
    )


def extract_rating_actions(doc: RawDocument, issuer_id: str, scales: RatingScales) -> Extraction:
    pats = _patterns(scales)
    events: list[CreditEvent] = []
    skipped: list[Skipped] = []
    sentences_with_transition: set[int] = set()
    for sentence in iter_sentences(doc.text):
        mentions = [
            (m.start(), m.end(), pats.by_name[m.group(1).casefold()])
            for m in pats.agency_re.finditer(sentence.text)
        ]
        if not mentions:
            continue
        for agency_id in sorted({a for _, _, a in mentions}):
            for m in pats.transition_re[agency_id].finditer(sentence.text):
                abs_start = sentence.start + m.start()
                preceding = [a for s, _, a in mentions if s < m.start()]
                owner = (
                    preceding[-1]
                    if preceding
                    else (mentions[0][2] if len({a for _, _, a in mentions}) == 1 else None)
                )
                if owner != agency_id:
                    continue  # this transition belongs to another agency mentioned in the sentence
                verbs = [v for v in _VERB_RE.finditer(sentence.text) if v.end() <= m.start()]
                if not verbs:
                    skipped.append(
                        Skipped("no_action_verb", abs_start, sentence.start + m.end(), m.group(0))
                    )
                    continue
                verb = verbs[-1]
                between = sentence.text[verb.end() : m.start()]
                if _SHORT_TERM_RE.search(between):
                    skipped.append(
                        Skipped(
                            "short_term_rating", abs_start, sentence.start + m.end(), m.group(0)
                        )
                    )
                    continue
                old_label = scales.agency(agency_id).canonical_label(m.group("old"))
                new_label = scales.agency(agency_id).canonical_label(m.group("new"))
                if old_label is None or new_label is None:
                    skipped.append(
                        Skipped(
                            "label_not_in_scale", abs_start, sentence.start + m.end(), m.group(0)
                        )
                    )
                    continue
                old_notch = to_notch(agency_id, old_label, scales)
                new_notch = to_notch(agency_id, new_label, scales)
                delta = notch_delta(old_notch=old_notch, new_notch=new_notch)
                verb_is_down = verb.group(1).lower() in DOWNGRADE_VERBS
                if delta == 0 or verb_is_down != (delta > 0):
                    skipped.append(
                        Skipped(
                            "verb_direction_mismatch",
                            abs_start,
                            sentence.start + m.end(),
                            m.group(0),
                        )
                    )
                    continue
                event_type = "downgrade" if delta > 0 else "upgrade"

                evidence: list[EvidenceSpan] = [
                    exact_span(
                        doc, sentence.start, sentence.end, extractor_version=EXTRACTOR_VERSION
                    ),
                    exact_span(
                        doc,
                        sentence.start + m.start("old"),
                        sentence.start + m.end("old"),
                        extractor_version=EXTRACTOR_VERSION,
                        field="old_rating",
                    ),
                    exact_span(
                        doc,
                        sentence.start + m.start("new"),
                        sentence.start + m.end("new"),
                        extractor_version=EXTRACTOR_VERSION,
                        field="new_rating",
                    ),
                ]
                agency_mention = [
                    (s, e) for s, e, a in mentions if a == agency_id and s < m.start()
                ]
                if agency_mention:
                    s, e = agency_mention[-1]
                    evidence.append(
                        exact_span(
                            doc,
                            sentence.start + s,
                            sentence.start + e,
                            extractor_version=EXTRACTOR_VERSION,
                            field="agency",
                        )
                    )

                effective: date | None = None
                found_date = parse_us_date(sentence.text)
                if found_date:
                    effective, ds, de = found_date
                    evidence.append(
                        exact_span(
                            doc,
                            sentence.start + ds,
                            sentence.start + de,
                            extractor_version=EXTRACTOR_VERSION,
                            field="effective_date",
                        )
                    )
                else:
                    header = document_header_date(doc)
                    if header:
                        effective, ds, de = header
                        evidence.append(
                            exact_span(
                                doc,
                                ds,
                                de,
                                extractor_version=EXTRACTOR_VERSION,
                                field="effective_date",
                            )
                        )

                new_outlook = None
                om = _OUTLOOK_RE.search(sentence.text, m.end())
                if om:
                    new_outlook = (om.group(1) or om.group(2)).lower()
                    evidence.append(
                        exact_span(
                            doc,
                            sentence.start + om.start(),
                            sentence.start + om.end(),
                            extractor_version=EXTRACTOR_VERSION,
                            field="new_outlook",
                        )
                    )
                watch = None
                wm = _WATCH_RE.search(sentence.text)
                if wm:
                    word = (wm.group(1) or wm.group(2)).lower()
                    watch = {"downgrade": "negative", "upgrade": "positive"}.get(word, word)
                    evidence.append(
                        exact_span(
                            doc,
                            sentence.start + wm.start(),
                            sentence.start + wm.end(),
                            extractor_version=EXTRACTOR_VERSION,
                            field="watch",
                        )
                    )

                fields = RatingFields(
                    agency=agency_id,  # type: ignore[arg-type]
                    old_rating=old_label,
                    new_rating=new_label,
                    new_outlook=new_outlook,  # type: ignore[arg-type]
                    watch=watch,  # type: ignore[arg-type]
                    scope="issuer",
                ).model_dump()
                fields.update(
                    {
                        "old_notch": old_notch,
                        "new_notch": new_notch,
                        "notch_delta": delta,
                        "old_category": category(old_notch, scales),
                        "new_category": category(new_notch, scales),
                        "crosses_ig_to_hy": category(old_notch, scales) == "IG"
                        and category(new_notch, scales) != "IG",
                        "crosses_hy_to_ig": category(old_notch, scales) != "IG"
                        and category(new_notch, scales) == "IG",
                        "old_at_boundary": is_at_boundary(old_notch, scales),
                        "action_verb": verb.group(1).lower(),
                        "extractor_version": EXTRACTOR_VERSION,
                    }
                )
                key = {
                    "agency": agency_id,
                    "old": old_label,
                    "new": new_label,
                    "date": effective.isoformat() if effective else f"{doc.doc_id}:{abs_start}",
                }
                events.append(
                    CreditEvent(
                        event_id=event_id_for(issuer_id, "rating", event_type, key),
                        issuer_id=issuer_id,
                        family="rating",
                        event_type=event_type,
                        effective_date=effective,
                        fields=fields,
                        evidence=evidence,
                        extraction_method="structured",
                        source_doc_ids=[doc.doc_id],
                    )
                )
                sentences_with_transition.add(sentence.start)
        if sentence.start not in sentences_with_transition:
            event = _non_transition_event(doc, issuer_id, scales, pats, sentence, mentions)
            if event is not None:
                events.append(event)
    return Extraction(events, skipped)


def _find_rating_label(text: str, alt: str) -> tuple[str, int, int] | None:
    """Rating label named in a non-transition sentence (quoted, or next to a rating noun)."""
    quoted = re.compile(rf"[{_QUOTES}](?P<label>{alt})(?:/[A-Za-z0-9+\-]+)?[{_QUOTES}]")
    m = quoted.search(text)
    if m:
        return m.group("label"), m.start("label"), m.end("label")
    bare = re.compile(rf"{_LABEL_BOUNDARY_BEFORE}(?P<label>{alt}){_LABEL_BOUNDARY_AFTER}")
    for m in bare.finditer(text):
        label = m.group("label")
        if len(label) == 1 and not text[max(0, m.start() - 3) : m.start()].lower().endswith("at "):
            continue
        if _RATING_NOUN_RE.search(text[m.end() : m.end() + 60]):
            return label, m.start("label"), m.end("label")
    return None


def _non_transition_event(doc, issuer_id, scales, pats, sentence, mentions) -> CreditEvent | None:
    """Watch placements, outlook revisions and affirmations stated by an agency."""
    text = sentence.text
    agency_ids = [a for _, _, a in mentions]
    watch = None
    watch_span = None
    for pattern in _WATCH_RES:
        m = pattern.search(text)
        if m:
            raw = m.group(1).lower()
            watch = _WATCH_VALUE.get(raw, raw)
            watch_span = (m.start(), m.end())
            break
    new_outlook = old_outlook = None
    outlook_span = None
    for pattern in _OUTLOOK_REVISION_RES:
        m = pattern.search(text)
        if m:
            new_outlook = m.group(1).lower()
            old_outlook = (
                m.group(2).lower() if m.lastindex and m.lastindex >= 2 and m.group(2) else None
            )
            outlook_span = (m.start(), m.end())
            break
    if new_outlook == "evolving":
        new_outlook = "developing"
    if old_outlook == "evolving":
        old_outlook = "developing"
    affirmed = re.search(r"(?i:\baffirm(?:ed|s|ing)?\b)", text) is not None
    if watch is None and new_outlook is None and not affirmed:
        return None
    if watch is not None:
        event_type = "watch"
        anchor = watch_span[0]
    elif new_outlook is not None:
        event_type = "outlook_change"
        anchor = outlook_span[0]
    else:
        event_type = "affirmation"
        anchor = text.lower().index("affirm")
    preceding = [a for s, _, a in mentions if s < anchor]
    agency_id = preceding[-1] if preceding else agency_ids[0]
    alt = pats.label_alt[agency_id]

    label = None
    label_span = None
    affirm_a = re.compile(
        rf"(?i:\baffirm(?:ed|s|ing)?\b)[^.;]{{0,120}}?(?:(?i:\bat)\s+)?[{_QUOTES}]?"
        rf"{_LABEL_BOUNDARY_BEFORE}(?P<label>{alt}){_LABEL_BOUNDARY_AFTER}"
    )
    affirm_b = re.compile(
        rf"[{_QUOTES}]?(?P<label>{alt})(?:/[A-Za-z0-9+\-]+)?[{_QUOTES}]?\s+(?:[A-Za-z-]+\s+){{0,3}}?(?i:ratings?\s+affirmed)"
    )
    for pattern in (affirm_a, affirm_b):
        m = pattern.search(text)
        if m:
            label, label_span = m.group("label"), (m.start("label"), m.end("label"))
            break
    if label is None:
        found = _find_rating_label(text, alt)
        if found:
            label, label_span = found[0], (found[1], found[2])
    canonical = scales.agency(agency_id).canonical_label(label) if label else None
    if event_type == "affirmation" and canonical is None:
        return None  # an affirmation without an identifiable rating is not an event

    evidence = [exact_span(doc, sentence.start, sentence.end, extractor_version=EXTRACTOR_VERSION)]
    agency_mention = [(s, e) for s, e, a in mentions if a == agency_id]
    if agency_mention:
        s0, e0 = agency_mention[0]
        evidence.append(
            exact_span(
                doc,
                sentence.start + s0,
                sentence.start + e0,
                extractor_version=EXTRACTOR_VERSION,
                field="agency",
            )
        )
    if canonical and label_span:
        evidence.append(
            exact_span(
                doc,
                sentence.start + label_span[0],
                sentence.start + label_span[1],
                extractor_version=EXTRACTOR_VERSION,
                field="rating",
            )
        )
    if outlook_span:
        evidence.append(
            exact_span(
                doc,
                sentence.start + outlook_span[0],
                sentence.start + outlook_span[1],
                extractor_version=EXTRACTOR_VERSION,
                field="new_outlook",
            )
        )
        if old_outlook:
            evidence.append(
                exact_span(
                    doc,
                    sentence.start + outlook_span[0],
                    sentence.start + outlook_span[1],
                    extractor_version=EXTRACTOR_VERSION,
                    field="old_outlook",
                )
            )
    if watch_span:
        evidence.append(
            exact_span(
                doc,
                sentence.start + watch_span[0],
                sentence.start + watch_span[1],
                extractor_version=EXTRACTOR_VERSION,
                field="watch",
            )
        )

    effective: date | None = None
    found_date = parse_us_date(text)
    if found_date:
        effective, ds, de = found_date
        evidence.append(
            exact_span(
                doc,
                sentence.start + ds,
                sentence.start + de,
                extractor_version=EXTRACTOR_VERSION,
                field="effective_date",
            )
        )
    else:
        header = document_header_date(doc)
        if header:
            effective, ds, de = header
            evidence.append(
                exact_span(doc, ds, de, extractor_version=EXTRACTOR_VERSION, field="effective_date")
            )

    if event_type == "affirmation" and watch is None and new_outlook is None:
        m = re.search(rf"(?i:\b{_OUTLOOK_WORDS}\s+outlook\b)", text)
        if m:
            new_outlook = m.group(1).lower()
    fields = RatingFields(
        agency=agency_id,  # type: ignore[arg-type]
        new_outlook=new_outlook,  # type: ignore[arg-type]
        old_outlook=old_outlook,  # type: ignore[arg-type]
        watch=watch,  # type: ignore[arg-type]
    ).model_dump()
    fields.update({"rating": canonical, "extractor_version": EXTRACTOR_VERSION})
    key = {
        "agency": agency_id, "rating": canonical, "new_outlook": new_outlook, "watch": watch,
        "date": effective.isoformat() if effective else f"{doc.doc_id}:{sentence.start}",
    }  # fmt: skip
    return CreditEvent(
        event_id=event_id_for(issuer_id, "rating", event_type, key),
        issuer_id=issuer_id,
        family="rating",
        event_type=event_type,
        effective_date=effective,
        fields=fields,
        evidence=evidence,
        extraction_method="structured",
        source_doc_ids=[doc.doc_id],
    )


def extract_edgar_items(doc: RawDocument, issuer_id: str) -> Extraction:
    """Event skeletons from 8-K item codes and prospectus forms (SPEC 6.1)."""
    if doc.source_type != "edgar":
        return Extraction([], [])
    form = str(doc.extra.get("form") or "")
    raw_items = doc.extra.get("items") or []
    items = [str(i) for i in raw_items] if isinstance(raw_items, list) else []
    effective_raw = doc.extra.get("report_date") or doc.extra.get("filing_date")
    effective = date.fromisoformat(str(effective_raw)) if effective_raw else None
    candidates: list[tuple[str, str, str]] = []
    if form in ("8-K", "8-K/A"):
        candidates += [
            (code, *EDGAR_ITEM_EVENTS[code]) for code in items if code in EDGAR_ITEM_EVENTS
        ]
    elif form in EDGAR_FORM_EVENTS:
        candidates.append((form, *EDGAR_FORM_EVENTS[form]))
    events: list[CreditEvent] = []
    for code, family, event_type in candidates:
        evidence: list[EvidenceSpan] = []
        if form.startswith("8-K"):
            m = re.search(rf"\bItem\s+{re.escape(code)}\b", doc.text)
            if m:
                evidence.append(
                    exact_span(
                        doc,
                        m.start(),
                        m.end(),
                        extractor_version=EDGAR_EXTRACTOR_VERSION,
                        field="edgar_item",
                    )
                )
        key = {"form": form, "code": code, "accession": doc.extra.get("accession_number")}
        events.append(
            CreditEvent(
                event_id=event_id_for(issuer_id, family, event_type, key),
                issuer_id=issuer_id,
                family=family,  # type: ignore[arg-type]
                event_type=event_type,
                effective_date=effective,
                fields={
                    "form": form,
                    "edgar_item": code if form.startswith("8-K") else None,
                    "evidence_basis": "edgar_filing_metadata",
                    "extractor_version": EDGAR_EXTRACTOR_VERSION,
                },
                evidence=evidence,
                extraction_method="structured",
                source_doc_ids=[doc.doc_id],
            )
        )
    return Extraction(events, [])
