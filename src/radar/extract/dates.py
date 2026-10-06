"""Deterministic date spotting in English text, with offsets (used for evidence spans).

Recognised forms: "July 8, 2026", "Dec. 17, 2025", "7 April 2025", "07 Apr 2025",
"08 Apr, 2025", "17-Dec-2025". A month without a day is never a date.
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_MONTH_FIRST = re.compile(
    rf"\b(?P<m>{_MONTH_ALT})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>\d{{4}})\b",
    re.IGNORECASE,
)
_DAY_FIRST = re.compile(
    rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?[\s-](?P<m>{_MONTH_ALT})\.?,?[\s-](?P<y>\d{{4}})\b",
    re.IGNORECASE,
)


def find_dates(text: str) -> list[tuple[date, int, int]]:
    """All dates in ``text`` as (date, start, end), sorted by position, overlaps removed."""
    found: list[tuple[date, int, int]] = []
    for pattern in (_MONTH_FIRST, _DAY_FIRST):
        for m in pattern.finditer(text):
            try:
                value = date(int(m.group("y")), _MONTHS[m.group("m").lower()], int(m.group("d")))
            except ValueError:
                continue
            found.append((value, m.start(), m.end()))
    found.sort(key=lambda item: (item[1], -item[2]))
    out: list[tuple[date, int, int]] = []
    last_end = -1
    for item in found:
        if item[1] < last_end:
            continue
        out.append(item)
        last_end = item[2]
    return out


def first_date(text: str) -> tuple[date, int, int] | None:
    found = find_dates(text)
    return found[0] if found else None


_QUARTER_RE = re.compile(
    r"(?i)\b(first|second|third|fourth)\s+quarter\s+(?:of|ended|ending)?\s*(?:fiscal\s+)?(20\d\d)\b"
)
_QUARTER_END = {"first": (3, 31), "second": (6, 30), "third": (9, 30), "fourth": (12, 31)}
HISTORICAL_DAYS = 365  # a passage dated more than a year before the document recalls history


def latest_reference_date(text: str) -> date | None:
    """The most recent date the passage refers to: explicit dates and quarter-of-year
    expressions ("second quarter of 2025" is 2025-06-30). None when the passage is undated."""
    found = [d for d, _, _ in find_dates(text)]
    for m in _QUARTER_RE.finditer(text):
        month, day = _QUARTER_END[m.group(1).lower()]
        found.append(date(int(m.group(2)), month, day))
    return max(found) if found else None


def is_historical(text: str, document_date: date | None, days: int = HISTORICAL_DAYS) -> bool:
    """True when every date the passage refers to lies more than ``days`` before the document
    date. An undated passage is never assumed historical."""
    if document_date is None:
        return False
    latest = latest_reference_date(text)
    return latest is not None and (document_date - latest).days > days
