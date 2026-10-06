"""Rating scales, notches and composite rating (docs/SPEC.md, sections 9.1 and 9.2).

Pure functions only: no I/O, no LLM. The scales are loaded from
``config/rating_scales.yaml`` by :mod:`radar.config` and passed in explicitly.

Notch convention: 1 is the strongest rating (AAA / Aaa), 22 is default. A positive
``notch_delta`` therefore means a downgrade.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from statistics import fmean

from pydantic import BaseModel, ConfigDict, Field, model_validator

from radar.models import (
    AgencyRating,
    CompositeMethod,
    CompositeRating,
    CompositeRounding,
    RatingCategory,
    SeedVerification,
)

DEFAULT_COMPOSITE_AGENCIES: tuple[str, ...] = ("SP", "MOODYS", "FITCH")
DEFAULT_ROUNDING: CompositeRounding = "nearest_weaker"
DEFAULT_MIN_VERIFICATION: SeedVerification = "GOLDEN"


class UnknownRatingError(ValueError):
    """Raised when an agency, rating label or notch is not in the configured scales."""


def _rating_key(raw: str) -> str:
    """Normalise a rating label for lookup: no whitespace, case-folded."""
    return re.sub(r"\s+", "", raw).casefold()


def _rating_key_exact(raw: str) -> str:
    """Whitespace-insensitive but case-sensitive key."""
    return re.sub(r"\s+", "", raw)


def _agency_key(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.replace("’", "'")).strip().casefold()


class AgencyScale(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    aliases: list[str] = Field(default_factory=list)
    scale: dict[str, int] = Field(description="Canonical label to notch, strongest first")

    def canonical_label(self, raw: str) -> str | None:
        """Match a raw label, whitespace-insensitive.

        Case is ignored only when the input is entirely upper or lower case
        ("baa3", "BBB (LOW)"). Mixed-case input such as "Aaa" must match exactly,
        so a Moody's label is never accepted as an S&P one.
        """
        exact = _rating_key_exact(raw)
        for label in self.scale:
            if _rating_key_exact(label) == exact:
                return label
        if raw.isupper() or raw.islower():
            key = _rating_key(raw)
            for label in self.scale:
                if _rating_key(label) == key:
                    return label
        return None

    def label_for_notch(self, notch: int) -> str | None:
        for label, value in self.scale.items():
            if value == notch:
                return label
        return None


class Boundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_ig_notch: int = Field(ge=1)
    first_hy_notch: int = Field(ge=2)

    @model_validator(mode="after")
    def _adjacent(self) -> Boundary:
        if self.first_hy_notch != self.last_ig_notch + 1:
            raise ValueError("first_hy_notch must be last_ig_notch + 1")
        return self


class CategoryRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: int = Field(ge=1)
    max: int = Field(ge=1)


class RatingScales(BaseModel):
    """Validated content of ``config/rating_scales.yaml``."""

    model_config = ConfigDict(extra="forbid")

    version: str
    boundary: Boundary
    categories: dict[RatingCategory, CategoryRange]
    unrated_tokens: list[str] = Field(default_factory=list)
    agencies: dict[str, AgencyScale]

    @model_validator(mode="after")
    def _consistent(self) -> RatingScales:
        if set(self.categories) != {"IG", "HY", "DEFAULT"}:
            raise ValueError("categories must define exactly IG, HY and DEFAULT")
        if self.categories["IG"].max != self.boundary.last_ig_notch:
            raise ValueError("IG category must end at boundary.last_ig_notch")
        if self.categories["HY"].min != self.boundary.first_hy_notch:
            raise ValueError("HY category must start at boundary.first_hy_notch")
        for name, agency in self.agencies.items():
            for label, notch in agency.scale.items():
                if not self.min_notch <= notch <= self.max_notch:
                    raise ValueError(f"{name} {label}: notch {notch} outside categories")
        return self

    @property
    def min_notch(self) -> int:
        return min(c.min for c in self.categories.values())

    @property
    def max_notch(self) -> int:
        return max(c.max for c in self.categories.values())

    def is_unrated(self, token: str) -> bool:
        key = _rating_key(token)
        return any(_rating_key(t) == key for t in self.unrated_tokens)

    def resolve_agency(self, name: str) -> str | None:
        """Map a display name or alias (e.g. "S&P Global") to the agency key (e.g. "SP")."""
        key = _agency_key(name)
        for agency_id, agency in self.agencies.items():
            candidates = [agency_id, agency.display_name, *agency.aliases]
            if any(_agency_key(c) == key for c in candidates):
                return agency_id
        return None

    def agency(self, agency_id: str) -> AgencyScale:
        try:
            return self.agencies[agency_id]
        except KeyError as exc:
            raise UnknownRatingError(f"unknown agency: {agency_id!r}") from exc

    def canonical_label(self, notch: int) -> str:
        """S&P-style label for a notch, used for composite ratings."""
        preferred = self.agencies.get("SP") or next(iter(self.agencies.values()))
        label = preferred.label_for_notch(notch)
        if label is None:
            raise UnknownRatingError(f"no label for notch {notch}")
        return label


# --------------------------------------------------------------------------- #
# Scale functions
# --------------------------------------------------------------------------- #


def normalize_rating(agency_id: str, raw: str, scales: RatingScales) -> str:
    """Return the canonical label for a raw rating string, or raise UnknownRatingError."""
    if scales.is_unrated(raw):
        raise UnknownRatingError(f"{agency_id}: {raw!r} is an unrated token")
    label = scales.agency(agency_id).canonical_label(raw)
    if label is None:
        raise UnknownRatingError(f"{agency_id}: unknown rating {raw!r}")
    return label


def to_notch(agency_id: str, raw: str, scales: RatingScales) -> int:
    label = normalize_rating(agency_id, raw, scales)
    return scales.agency(agency_id).scale[label]


def from_notch(agency_id: str, notch: int, scales: RatingScales) -> str:
    label = scales.agency(agency_id).label_for_notch(notch)
    if label is None:
        raise UnknownRatingError(f"{agency_id}: no label for notch {notch}")
    return label


def category(notch: int, scales: RatingScales) -> RatingCategory:
    for name, rng in scales.categories.items():
        if rng.min <= notch <= rng.max:
            return name
    raise ValueError(f"notch {notch} outside [{scales.min_notch}, {scales.max_notch}]")


def is_investment_grade(notch: int, scales: RatingScales) -> bool:
    return category(notch, scales) == "IG"


def is_at_boundary(notch: int, scales: RatingScales) -> bool:
    """True for the last investment-grade notch (BBB- / Baa3)."""
    return notch == scales.boundary.last_ig_notch


def notch_delta(*, old_notch: int, new_notch: int) -> int:
    """Positive for a downgrade, negative for an upgrade, zero for no change."""
    return new_notch - old_notch


# --------------------------------------------------------------------------- #
# Composite rating
# --------------------------------------------------------------------------- #


def _select_inputs(
    ratings: Iterable[AgencyRating],
    scales: RatingScales,
    agencies: Sequence[str] | None,
    min_verification: SeedVerification,
) -> list[AgencyRating]:
    """Keep composite-eligible, sufficiently verified, rated entries; one per agency.

    Eligible means an issuer-level rating type (see COMPOSITE_ELIGIBLE_RATING_TYPES).
    When several rows exist for one agency, the latest fully dated one wins.
    """
    allowed = set(agencies) if agencies is not None else None
    per_agency: dict[str, list[AgencyRating]] = {}
    for r in ratings:
        if not r.composite_eligible or not r.verified_at_least(min_verification):
            continue
        if allowed is not None and r.agency not in allowed:
            continue
        if scales.is_unrated(r.rating):
            continue
        per_agency.setdefault(r.agency, []).append(r)

    selected: list[AgencyRating] = []
    for candidates in per_agency.values():
        dated = [c for c in candidates if c.as_of is not None]
        chosen = max(dated, key=lambda c: c.as_of) if dated else candidates[0]  # type: ignore[arg-type,return-value]
        selected.append(chosen)
    return selected


def _round_average(mean: float, rounding: CompositeRounding) -> int:
    if rounding == "nearest_weaker":
        return math.floor(mean + 0.5)
    if rounding == "ceil":
        return math.ceil(mean)
    if rounding == "floor":
        return math.floor(mean)
    raise ValueError(f"unknown rounding: {rounding!r}")


def composite_rating(
    ratings: Iterable[AgencyRating],
    scales: RatingScales,
    *,
    method: CompositeMethod = "middle",
    rounding: CompositeRounding = DEFAULT_ROUNDING,
    agencies: Sequence[str] | None = DEFAULT_COMPOSITE_AGENCIES,
    min_verification: SeedVerification = DEFAULT_MIN_VERIFICATION,
) -> CompositeRating | None:
    """Compute the issuer composite rating (SPEC 9.2). Analytical metadata only (ADR-001).

    ``middle``: the median notch. With one rating, that rating; with two, the weaker one;
    with an even count above two, the weaker of the two middle values.

    ``average``: the mean notch rounded with ``rounding``. ``nearest_weaker`` (default)
    rounds to the nearest notch and sends ties to the weaker side, so a single downgrade
    out of three agencies does not flip the composite. ``ceil`` is the strict reading
    (any fraction goes to the weaker notch), ``floor`` rounds toward the stronger notch.

    Only issuer-level rating types from ``agencies`` (``None`` means all agencies) that
    reached ``min_verification`` on the seed ladder are used. Returns ``None`` when nothing
    usable remains.
    """
    if method not in ("middle", "average"):
        raise ValueError(f"unknown composite method: {method!r}")
    if rounding not in ("nearest_weaker", "ceil", "floor"):
        raise ValueError(f"unknown rounding: {rounding!r}")

    ratings = list(ratings)
    issuers = {r.issuer_id for r in ratings}
    if len(issuers) > 1:
        raise ValueError(f"ratings belong to several issuers: {sorted(issuers)}")

    inputs = _select_inputs(ratings, scales, agencies, min_verification)
    if not inputs:
        return None

    notches = sorted(to_notch(r.agency, r.rating, scales) for r in inputs)
    if method == "middle":
        notch = notches[len(notches) // 2]
        used_rounding: CompositeRounding | None = None
    else:
        notch = _round_average(fmean(notches), rounding)
        used_rounding = rounding
    notch = max(scales.min_notch, min(scales.max_notch, notch))

    return CompositeRating(
        notch=notch,
        label=scales.canonical_label(notch),
        category=category(notch, scales),
        method=method,
        rounding=used_rounding,
        inputs=sorted(inputs, key=lambda r: r.agency),
    )
