"""Issuer rating state at a date D, built from dated candidates (ADR-007, anti look-ahead).

Pure: candidates come from the caller (seed rows, document observations, prior rating
events). Every candidate that is not used is kept in ``ignored`` with its reason so the
explanation can show exactly what the decision saw and what it refused to see.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from radar.config import RatingStateParams
from radar.models import Outlook, RatingCategory, RatingScope, Watch
from radar.ratings import RatingScales, UnknownRatingError, category, to_notch

Origin = Literal["seed", "observation", "event"]
ORIGIN_PRIORITY: dict[str, int] = {"event": 0, "observation": 1, "seed": 2}


class RatingCandidate(BaseModel):
    agency: str
    rating: str
    outlook: Outlook | None = None
    watch: Watch = "none"
    rating_type: str
    scope: RatingScope = "issuer"
    as_of: date | None = None
    as_of_raw: str | None = None
    origin: Origin
    source: str = Field(description="Seed URL, document id or event id")
    verification: str = Field(description="e.g. seed:DATE_VERIFIED, structured_table, event")


class RatingStateEntry(BaseModel):
    agency: str
    rating: str
    notch: int
    category: RatingCategory
    outlook: Outlook | None = None
    watch: Watch = "none"
    as_of: date
    age_days: int
    origin: Origin
    source: str
    verification: str


class IgnoredRating(BaseModel):
    agency: str
    rating: str
    as_of: date | None
    origin: Origin
    reason: str


class RatingState(BaseModel):
    as_of: date
    entries: dict[str, RatingStateEntry]
    ignored: list[IgnoredRating] = Field(default_factory=list)
    params: RatingStateParams

    def weakest(self) -> RatingStateEntry | None:
        if not self.entries:
            return None
        return max(self.entries.values(), key=lambda e: (e.notch, e.agency))

    def after_action(
        self,
        agency: str,
        rating: str,
        scales: RatingScales,
        *,
        outlook: Outlook | None = None,
        watch: Watch = "none",
    ) -> RatingState:
        """State at D once the acting agency's new rating replaces its previous one."""
        notch = to_notch(agency, rating, scales)
        entry = RatingStateEntry(
            agency=agency,
            rating=rating,
            notch=notch,
            category=category(notch, scales),
            outlook=outlook,
            watch=watch,
            as_of=self.as_of,
            age_days=0,
            origin="event",
            source="event",
            verification="event",
        )
        entries = dict(self.entries)
        entries[agency] = entry
        return self.model_copy(update={"entries": entries})


def _reject(c: RatingCandidate, reason: str) -> IgnoredRating:
    return IgnoredRating(
        agency=c.agency, rating=c.rating, as_of=c.as_of, origin=c.origin, reason=reason
    )


def build_rating_state(
    candidates: list[RatingCandidate],
    as_of: date,
    params: RatingStateParams,
    scales: RatingScales,
) -> RatingState:
    ignored: list[IgnoredRating] = []
    kept: dict[str, list[tuple[RatingCandidate, int]]] = {}
    for c in candidates:
        if c.agency not in params.admissible_agencies:
            ignored.append(_reject(c, "non-admissible agency"))
            continue
        if c.scope != "issuer" or c.rating_type not in params.eligible_rating_types:
            ignored.append(
                _reject(c, f"rating type not eligible ({c.rating_type}, scope {c.scope})")
            )
            continue
        if scales.is_unrated(c.rating):
            ignored.append(_reject(c, "unrated token"))
            continue
        try:
            notch = to_notch(c.agency, c.rating, scales)
        except UnknownRatingError:
            ignored.append(_reject(c, "unknown rating label"))
            continue
        if c.as_of is None:
            if params.require_complete_date:
                ignored.append(_reject(c, "incomplete date"))
                continue
            raise ValueError("candidates without a complete date are not supported")
        if c.as_of > as_of and not params.allow_future_observation:
            ignored.append(
                _reject(c, f"observed after D ({c.as_of.isoformat()} > {as_of.isoformat()})")
            )
            continue
        age = (as_of - c.as_of).days
        if age > params.max_rating_age_days:
            ignored.append(_reject(c, f"stale ({age} days > {params.max_rating_age_days})"))
            continue
        kept.setdefault(c.agency, []).append((c, notch))

    entries: dict[str, RatingStateEntry] = {}
    for agency, items in kept.items():
        items.sort(
            key=lambda item: (
                -item[0].as_of.toordinal(),
                ORIGIN_PRIORITY[item[0].origin],
                item[0].source,
            )
        )  # type: ignore[union-attr]
        winner, notch = items[0]
        assert winner.as_of is not None
        entries[agency] = RatingStateEntry(
            agency=agency,
            rating=winner.rating,
            notch=notch,
            category=category(notch, scales),
            outlook=winner.outlook,
            watch=winner.watch,
            as_of=winner.as_of,
            age_days=(as_of - winner.as_of).days,
            origin=winner.origin,
            source=winner.source,
            verification=winner.verification,
        )
        for other, _ in items[1:]:
            ignored.append(
                _reject(other, f"superseded by newer rating as_of {winner.as_of.isoformat()}")
            )
    return RatingState(
        as_of=as_of, entries=dict(sorted(entries.items())), ignored=ignored, params=params
    )
