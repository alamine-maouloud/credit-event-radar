"""Event deduplication (docs/SPEC.md section 8, step 2).

Two events are the same when issuer, family, event type, agency and key fields match and
their effective dates are within ``window_days`` of each other (or both unknown). Sources
of duplicates are merged on the first stored event.
"""

from __future__ import annotations

from datetime import date

from radar.db import Database
from radar.models import CreditEvent

KEY_FIELDS = ("agency", "old_rating", "new_rating", "form", "edgar_item")
DEFAULT_WINDOW_DAYS = 2


def business_key(event: CreditEvent) -> tuple:
    return (
        event.issuer_id,
        event.family,
        event.event_type,
        *(event.fields.get(f) for f in KEY_FIELDS),
    )


def _dates_match(a: date | None, b: date | None, window_days: int) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs((a - b).days) <= window_days


def find_duplicate(
    db: Database, event: CreditEvent, *, window_days: int = DEFAULT_WINDOW_DAYS
) -> CreditEvent | None:
    key = business_key(event)
    for existing in db.list_events(event.issuer_id):
        if existing.event_id == event.event_id:
            return existing
        if business_key(existing) == key and _dates_match(
            existing.effective_date, event.effective_date, window_days
        ):
            return existing
    return None
