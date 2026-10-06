"""Shared fixtures. All issuer identifiers are explicitly fictional (ISSUER_TEST_*)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from radar.config import load_rating_scales

__all__ = ["load_rating_scales", "make_rating"]
from radar.models import AgencyRating
from radar.ratings import RatingScales

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


@pytest.fixture(scope="session")
def scales() -> RatingScales:
    return load_rating_scales(CONFIG_DIR / "rating_scales.yaml")


def make_rating(
    agency: str,
    rating: str,
    *,
    issuer_id: str = "ISSUER_TEST_A",
    as_of: date | None = None,
    scope: str = "issuer",
    rating_type: str = "long_term_issuer",
    verification_status: str = "GOLDEN",
    outlook: str | None = "stable",
    watch: str = "none",
) -> AgencyRating:
    """Build a fictional AgencyRating for tests."""
    return AgencyRating(
        issuer_id=issuer_id,
        agency=agency,  # type: ignore[arg-type]
        rating=rating,
        scope=scope,  # type: ignore[arg-type]
        rating_type=rating_type,  # type: ignore[arg-type]
        outlook=outlook,  # type: ignore[arg-type]
        watch=watch,  # type: ignore[arg-type]
        as_of=as_of,
        source_url="https://example.invalid/ratings",
        retrieved_at=date(2026, 1, 1),
        verification_status=verification_status,  # type: ignore[arg-type]
    )
