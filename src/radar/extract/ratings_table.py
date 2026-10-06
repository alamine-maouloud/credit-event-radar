"""Deterministic extraction of agency rating tables from filings.

Version ``structured-ratings-table-1.0`` recognises the pattern used in SEC 10-Q and 10-K
liquidity sections: a sentence mentioning ratings "as of <Month D, YYYY>", an optional
header row, then one table row per agency ("<agency> | <short-term> | | <long-term> | |
<outlook>"). Each row becomes a :class:`RatingObservation` with the table row as
``table_row`` evidence and the "as of" date as ``as_of``.

Column rule: the long-term rating is the last cell that is a label of the agency's
long-term scale when the header lists Short-Term before Long-Term (the usual layout),
the first such cell otherwise. Short-term labels (P-3, A-3, F2) are never in the
long-term scale; the S&P short-term label "B" is handled by the column rule.

Row continuation: when the agency name carries a footnote marker the normaliser may put
it on its own line, followed by a line whose first cell is empty. Such a pair is read as
one row and the evidence span covers both lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from radar.audit import stable_hash
from radar.config import parse_outlook
from radar.extract.spans import exact_span
from radar.extract.structured import Skipped, parse_us_date
from radar.models import RatingObservation, RawDocument
from radar.ratings import RatingScales

TABLE_EXTRACTOR_VERSION = "structured-ratings-table-1.0"
MAX_LINES_BEFORE_TABLE = 3
MAX_ROWS = 8
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_AS_OF_RE = re.compile(rf"\bas of\s+({_MONTHS})\s+\d{{1,2}},\s+\d{{4}}\b", re.IGNORECASE)
_FOOTNOTE_RE = re.compile(r"(\(\w{1,2}\)|\*+|†|‡)+$")


@dataclass
class ObservationExtraction:
    observations: list[RatingObservation]
    skipped: list[Skipped]


def _lines(text: str) -> list[tuple[int, int, str]]:
    out, start = [], 0
    for line in text.split("\n"):
        out.append((start, start + len(line), line))
        start += len(line) + 1
    return out


def _agency_of(cell: str, scales: RatingScales) -> str | None:
    cleaned = _FOOTNOTE_RE.sub("", cell.strip()).strip()
    return scales.resolve_agency(cleaned) if cleaned else None


def _header_prefers_last(line: str) -> bool | None:
    low = line.casefold().replace("-", " ")
    if "long term" not in low:
        return None
    if "short term" not in low:
        return False
    return low.index("short term") < low.index("long term")


def extract_rating_observations(
    doc: RawDocument, issuer_id: str, scales: RatingScales
) -> ObservationExtraction:
    observations: list[RatingObservation] = []
    skipped: list[Skipped] = []
    lines = _lines(doc.text)
    i = 0
    while i < len(lines):
        start, end, line = lines[i]
        i += 1
        if "rating" not in line.casefold() or not _AS_OF_RE.search(line):
            continue
        as_of_match = _AS_OF_RE.search(line)
        assert as_of_match is not None
        parsed = parse_us_date(as_of_match.group(0))
        if parsed is None:
            continue
        as_of = parsed[0]
        prefer_last = False
        j = i
        waited = 0
        in_table = False
        while (
            j < len(lines)
            and waited <= MAX_LINES_BEFORE_TABLE
            and (not in_table or len(observations) < MAX_ROWS)
        ):
            row_start, row_end, row = lines[j]
            cells = [c.strip() for c in row.split("|")]
            agency = _agency_of(cells[0], scales) if len(cells) > 1 else None
            if agency is None and "|" not in row and j + 1 < len(lines):
                # agency name alone on its line, cells on the next line (footnote layout)
                _, next_end, next_row = lines[j + 1]
                next_cells = [c.strip() for c in next_row.split("|")]
                if len(next_cells) > 1 and next_cells[0] == "" and _agency_of(row, scales):
                    agency = _agency_of(row, scales)
                    cells = next_cells
                    row_end, row = next_end, doc.text[row_start:next_end]
                    j += 1
            if agency is None:
                if in_table:
                    break
                header = _header_prefers_last(row)
                if header is not None:
                    prefer_last = header
                waited += 1
                j += 1
                continue
            in_table = True
            j += 1
            scale = scales.agency(agency)
            label_cells = [
                (k, scale.canonical_label(c))
                for k, c in enumerate(cells[1:], start=1)
                if c and not scales.is_unrated(c) and scale.canonical_label(c)
            ]
            if not label_cells:
                skipped.append(Skipped("no_long_term_label", row_start, row_end, row))
                continue
            k, label = label_cells[-1] if prefer_last else label_cells[0]
            assert label is not None
            outlook, watch = None, "none"
            for cell in cells[k + 1 :]:
                if not cell:
                    continue
                try:
                    outlook, watch = parse_outlook(cell)
                except ValueError:
                    continue
                if outlook or watch != "none":
                    break
            span = exact_span(
                doc,
                row_start,
                row_end,
                extractor_version=TABLE_EXTRACTOR_VERSION,
                evidence_type="table_row",
                field="rating_row",
            )
            span_id = (
                "span_"
                + stable_hash({"doc_id": doc.doc_id, "start": row_start, "end": row_end})[:16]
            )
            observations.append(
                RatingObservation(
                    observation_id="obs_"
                    + stable_hash(
                        {
                            "issuer_id": issuer_id,
                            "agency": agency,
                            "rating": label,
                            "as_of": as_of.isoformat(),
                            "doc_id": doc.doc_id,
                            "start": row_start,
                        }
                    )[:20],
                    issuer_id=issuer_id,
                    agency=agency,  # type: ignore[arg-type]
                    rating=label,
                    outlook=outlook,
                    watch=watch,
                    rating_type="long_term_issuer",
                    scope="issuer",
                    as_of=as_of,
                    doc_id=doc.doc_id,
                    evidence_span_id=span_id,
                    evidence=span,
                    extractor_version=TABLE_EXTRACTOR_VERSION,
                    verification_method="structured_table",
                )
            )
        i = max(i, j)
    return ObservationExtraction(observations, skipped)
