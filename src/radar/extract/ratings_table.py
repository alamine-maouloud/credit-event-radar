"""Deterministic extraction of agency rating tables from documents.

Version ``structured-ratings-table-1.0`` handles three layouts (``profile``):

- ``sec_as_of`` (SEC 10-Q/10-K liquidity sections): a sentence mentioning ratings
  "as of <Month D, YYYY>", an optional header row, then one row per agency. Observations
  are dated by the stated as-of date (``as_of_basis`` stated).
- ``current_by_agency`` (issuer ratings pages such as Volkswagen's): a header row whose
  first cell is the agency and which lists Short-Term / Long-Term / Outlook columns, then
  a row whose first cell is the rated entity. No date is stated: observations carry
  ``rating_date`` null, ``observed_at`` = retrieval day, ``as_of_basis`` retrieval
  (prospective use only, ADR-011).
- ``dated_by_agency`` (issuer pages such as OMV's): the agency name on its own line, a
  "Date | Rating | Outlook" header, then dated rows (one observation each, stated basis).

Column rule for the long-term label: the last cell that is a label of the agency's
long-term scale when the header lists Short-Term before Long-Term, the first such cell
otherwise. Row continuation: a line starting with "|" continues the previous row.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from radar.audit import stable_hash
from radar.config import parse_outlook
from radar.extract.dates import first_date
from radar.extract.spans import exact_span
from radar.extract.structured import Skipped
from radar.models import RatingObservation, RawDocument
from radar.ratings import AgencyScale, RatingScales

TABLE_EXTRACTOR_VERSION = "structured-ratings-table-1.0"
PROFILES = ("sec_as_of", "current_by_agency", "dated_by_agency")
MAX_LINES_BEFORE_TABLE = 3
MAX_ROWS = 8
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_AS_OF_RE = re.compile(rf"\bas of\s+({_MONTHS})\s+\d{{1,2}},\s+\d{{4}}\b", re.IGNORECASE)
_FOOTNOTE_RE = re.compile(r"(\(\w{1,2}\)|\*+|†|‡)+$")


@dataclass
class ObservationExtraction:
    observations: list[RatingObservation]
    skipped: list[Skipped]


Line = tuple[int, int, str]


def observed_at(doc: RawDocument) -> date:
    """Day the document was published, else the day it was retrieved."""
    return (doc.published_at or doc.retrieved_at).date()


def _lines(text: str) -> list[Line]:
    out, start = [], 0
    for line in text.split("\n"):
        out.append((start, start + len(line), line))
        start += len(line) + 1
    return out


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.split("|")]


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


def _label_cells(
    cells: Sequence[str], scale: AgencyScale, scales: RatingScales
) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for k, c in enumerate(cells):
        if c and not scales.is_unrated(c):
            label = scale.canonical_label(c)
            if label:
                out.append((k, label))
    return out


def _outlook_after(cells: Sequence[str], k: int) -> tuple[str | None, str]:
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
    return outlook, watch


class _Builder:
    def __init__(self, doc: RawDocument, issuer_id: str, method: str) -> None:
        self.doc = doc
        self.issuer_id = issuer_id
        self.method = method
        self.observations: list[RatingObservation] = []
        self.skipped: list[Skipped] = []

    def add(
        self,
        agency: str,
        label: str,
        outlook: str | None,
        watch: str,
        *,
        rating_date: date | None,
        start: int,
        end: int,
    ) -> None:
        span = exact_span(
            self.doc, start, end, extractor_version=TABLE_EXTRACTOR_VERSION,
            evidence_type="table_row", field="rating_row",
        )  # fmt: skip
        span_id = (
            "span_" + stable_hash({"doc_id": self.doc.doc_id, "start": start, "end": end})[:16]
        )
        key = {
            "issuer_id": self.issuer_id, "agency": agency, "rating": label,
            "rating_date": rating_date.isoformat() if rating_date else None,
            "doc_id": self.doc.doc_id, "start": start,
        }  # fmt: skip
        self.observations.append(
            RatingObservation(
                observation_id="obs_" + stable_hash(key)[:20],
                issuer_id=self.issuer_id,
                agency=agency,  # type: ignore[arg-type]
                rating=label,
                outlook=outlook,  # type: ignore[arg-type]
                watch=watch,  # type: ignore[arg-type]
                rating_type="long_term_issuer",
                scope="issuer",
                rating_date=rating_date,
                observed_at=observed_at(self.doc),
                as_of_basis="stated" if rating_date else "retrieval",
                doc_id=self.doc.doc_id,
                evidence_span_id=span_id,
                evidence=span,
                extractor_version=TABLE_EXTRACTOR_VERSION,
                verification_method=self.method,  # type: ignore[arg-type]
            )
        )


# ------------------------------------------------------------- sec_as_of --- #


def _profile_sec_as_of(lines: list[Line], b: _Builder, scales: RatingScales) -> None:
    i = 0
    while i < len(lines):
        _, _, line = lines[i]
        i += 1
        if "rating" not in line.casefold() or not _AS_OF_RE.search(line):
            continue
        as_of_match = _AS_OF_RE.search(line)
        assert as_of_match is not None
        parsed = first_date(as_of_match.group(0))
        if parsed is None:
            continue
        as_of = parsed[0]
        prefer_last = False
        j, waited, in_table, rows = i, 0, False, 0
        while (
            j < len(lines)
            and waited <= MAX_LINES_BEFORE_TABLE
            and (not in_table or rows < MAX_ROWS)
        ):
            row_start, row_end, row = lines[j]
            cells = _cells(row)
            agency = _agency_of(cells[0], scales) if len(cells) > 1 else None
            if agency is None and "|" not in row and j + 1 < len(lines):
                _, next_end, next_row = lines[j + 1]
                next_cells = _cells(next_row)
                if len(next_cells) > 1 and next_cells[0] == "" and _agency_of(row, scales):
                    agency = _agency_of(row, scales)
                    cells = next_cells
                    row_end, row = next_end, b.doc.text[row_start:next_end]
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
            rows += 1
            j += 1
            labels = _label_cells(cells[1:], scales.agency(agency), scales)
            if not labels:
                b.skipped.append(Skipped("no_long_term_label", row_start, row_end, row))
                continue
            k, label = labels[-1] if prefer_last else labels[0]
            outlook, watch = _outlook_after(cells, k + 1)
            b.add(agency, label, outlook, watch, rating_date=as_of, start=row_start, end=row_end)
        i = max(i, j)


# ----------------------------------------------------- current_by_agency --- #


def _entity_matches(cell: str, aliases: Sequence[str] | None) -> bool:
    if not aliases:
        return True
    key = re.sub(r"\s+", " ", cell).strip().casefold()
    return any(key == re.sub(r"\s+", " ", a).strip().casefold() for a in aliases)


def _profile_current_by_agency(
    lines: list[Line], b: _Builder, scales: RatingScales, aliases: Sequence[str] | None
) -> None:
    i = 0
    while i < len(lines):
        _, _, header = lines[i]
        i += 1
        cells = _cells(header)
        if len(cells) < 2:
            continue
        agency = _agency_of(cells[0], scales)
        prefer_last = _header_prefers_last(header)
        if agency is None or prefer_last is None:
            continue
        if i >= len(lines):
            break
        row_start, row_end, row = lines[i]
        row_cells = _cells(row)
        if len(row_cells) < 2 or row_cells[0] == "" or _agency_of(row_cells[0], scales):
            continue
        i += 1
        while i < len(lines) and lines[i][2].startswith("|"):
            row_end = lines[i][1]
            row_cells += _cells(lines[i][2])[1:]
            i += 1
        row = b.doc.text[row_start:row_end]
        if not _entity_matches(row_cells[0], aliases):
            b.skipped.append(Skipped("entity_mismatch", row_start, row_end, row))
            continue
        labels = _label_cells(row_cells[1:], scales.agency(agency), scales)
        if not labels:
            b.skipped.append(Skipped("no_long_term_label", row_start, row_end, row))
            continue
        k, label = labels[-1] if prefer_last else labels[0]
        outlook, watch = _outlook_after(row_cells, k + 1)
        b.add(agency, label, outlook, watch, rating_date=None, start=row_start, end=row_end)


# ------------------------------------------------------- dated_by_agency --- #


def _profile_dated_by_agency(lines: list[Line], b: _Builder, scales: RatingScales) -> None:
    i = 0
    while i < len(lines):
        _, _, line = lines[i]
        i += 1
        if "|" in line:
            continue
        agency = _agency_of(line, scales)
        if agency is None or i >= len(lines):
            continue
        header = lines[i][2].casefold()
        if "date" not in header or "rating" not in header or "|" not in header:
            continue
        i += 1
        scale = scales.agency(agency)
        while i < len(lines):
            row_start, row_end, row = lines[i]
            cells = _cells(row)
            when = first_date(cells[0]) if len(cells) > 1 else None
            if (
                when is None
                or when[2] != len(cells[0].strip())
                and cells[0].strip()[when[2] :].strip()
            ):
                break
            i += 1
            labels = _label_cells(cells[1:], scale, scales)
            if not labels:
                b.skipped.append(Skipped("no_long_term_label", row_start, row_end, row))
                continue
            k, label = labels[0]
            outlook, watch = _outlook_after(cells, k + 1)
            b.add(agency, label, outlook, watch, rating_date=when[0], start=row_start, end=row_end)


def extract_rating_observations(
    doc: RawDocument,
    issuer_id: str,
    scales: RatingScales,
    *,
    profile: str = "sec_as_of",
    entity_aliases: Sequence[str] | None = None,
) -> ObservationExtraction:
    if profile not in PROFILES:
        raise ValueError(f"unknown table profile {profile!r}, expected one of {PROFILES}")
    method = "structured_table_current" if profile == "current_by_agency" else "structured_table"
    b = _Builder(doc, issuer_id, method)
    lines = _lines(doc.text)
    if profile == "sec_as_of":
        _profile_sec_as_of(lines, b, scales)
    elif profile == "current_by_agency":
        _profile_current_by_agency(lines, b, scales, entity_aliases)
    else:
        _profile_dated_by_agency(lines, b, scales)
    return ObservationExtraction(b.observations, b.skipped)
