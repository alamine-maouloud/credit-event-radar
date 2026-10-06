"""Ratings-table profiles for issuer pages: current_by_agency (VW layout), dated_by_agency (OMV layout)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from radar.extract.ratings_table import extract_rating_observations
from radar.extract.spans import verify_span
from radar.models import RawDocument
from radar.snapshot import sha256_hex

VW_PAGE = """Ratings
Credit ratings conducted on a regular basis by Fitch, Moody's, Standard & Poor's Ratings Services, DBRS and provide our investors with additional transparency.
Fitch | Short-Term | Long-Term | Outlook
Issuer Test A AG | F-1 | A- | Negative
Moody's | Short-Term | Long-Term | Outlook
Issuer Test A AG | P-2 | Baa1 | Stable
Standard & Poor's | Short-Term | Long-Term | Outlook
Issuer Test A AG | A-2 | BBB+ | Negative
DBRS Morningstar | Short-Term | Long-Term | Outlook
Issuer Test A AG | R-2 (high)* | BBB (high)
| Stable
*Short-Term Rating of DBRS Morningstar only applicable for a subsidiary.
Financial Services AG and Bank GmbH have an individual rating."""

EN = chr(0x2013)
MINUS = chr(0x2212)

OMV_PAGE = """Credit ratings
Issuer Test B is rated by Moody's "A3" and Fitch "A-"
Moody's
Date | Rating | Outlook
June 1, 2026 | A3 | Stable
July 23, 2025 | A3 | Stable
Fitch
Date | Rating | Outlook
July 9, 2026 | A@EN@ | Stable
July 15, 2025 | A@EN@ | Stable
Issuer Test B Debt Structure
Senior Bonds
Date of issue | Publicly traded bonds | Amount (EUR mn) | Coupon (fix) | Maturity
November 2025 | XS0000000001 | 500 | 3.875% | 11/10/2040""".replace("@EN@", EN)


def doc(text: str, published: datetime | None = None) -> RawDocument:
    return RawDocument(
        doc_id=sha256_hex(text), source_type="ir_feed", url="https://example.invalid/ratings", retrieved_at=datetime(2026, 10, 6, 11, 0, tzinfo=UTC),
        published_at=published, content_hash=sha256_hex(text.encode()), raw_size_bytes=len(text), normalizer_version="t", text=text, raw_path="p",
        extra={"document_type": "ratings_page"},
    )  # fmt: skip


ALIASES = ["Issuer Test A", "Issuer Test A AG"]


def test_current_by_agency_reads_four_agencies(scales):
    result = extract_rating_observations(
        doc(VW_PAGE), "ISSUER_TEST_A", scales, profile="current_by_agency", entity_aliases=ALIASES
    )
    obs = {o.agency: o for o in result.observations}
    assert set(obs) == {"FITCH", "MOODYS", "SP", "DBRS"}
    assert (obs["FITCH"].rating, obs["FITCH"].outlook) == ("A-", "negative")
    assert (obs["MOODYS"].rating, obs["MOODYS"].outlook) == ("Baa1", "stable")
    assert (obs["SP"].rating, obs["SP"].outlook) == ("BBB+", "negative")
    assert (obs["DBRS"].rating, obs["DBRS"].outlook) == ("BBB (high)", "stable")
    for o in obs.values():
        assert (
            o.as_of_basis == "retrieval"
            and o.rating_date is None
            and o.observed_at == date(2026, 10, 6)
        )
        assert o.as_of == date(2026, 10, 6)
        assert o.verification_method == "structured_table_current"
        assert o.evidence.evidence_type == "table_row" and verify_span(doc(VW_PAGE), o.evidence)
    assert obs["DBRS"].evidence.quote == "Issuer Test A AG | R-2 (high)* | BBB (high)\n| Stable"
    assert result.skipped == []


def test_current_by_agency_rejects_other_entities(scales):
    text = VW_PAGE.replace(
        "Issuer Test A AG | P-2 | Baa1 | Stable",
        "Issuer Test A Financial Services AG | P-2 | A3 | Stable",
    )
    result = extract_rating_observations(
        doc(text), "ISSUER_TEST_A", scales, profile="current_by_agency", entity_aliases=ALIASES
    )
    assert "MOODYS" not in {o.agency for o in result.observations}
    assert [s.reason for s in result.skipped] == ["entity_mismatch"]


def test_current_by_agency_short_term_b_is_not_long_term(scales):
    text = VW_PAGE.replace(
        "Issuer Test A AG | A-2 | BBB+ | Negative", "Issuer Test A AG | B | BB+ | Negative"
    )
    obs = {
        o.agency: o
        for o in extract_rating_observations(
            doc(text), "ISSUER_TEST_A", scales, profile="current_by_agency", entity_aliases=ALIASES
        ).observations
    }
    assert obs["SP"].rating == "BB+"


def test_dated_by_agency_reads_history_with_stated_dates(scales):
    result = extract_rating_observations(
        doc(OMV_PAGE), "ISSUER_TEST_B", scales, profile="dated_by_agency"
    )
    rows = [(o.agency, o.rating, o.rating_date, o.outlook) for o in result.observations]
    assert rows == [
        ("MOODYS", "A3", date(2026, 6, 1), "stable"),
        ("MOODYS", "A3", date(2025, 7, 23), "stable"),
        ("FITCH", "A-", date(2026, 7, 9), "stable"),
        ("FITCH", "A-", date(2025, 7, 15), "stable"),
    ]
    for o in result.observations:
        assert o.as_of_basis == "stated" and o.as_of == o.rating_date
        assert o.observed_at == date(2026, 10, 6) and o.verification_method == "structured_table"
        assert verify_span(doc(OMV_PAGE), o.evidence)
    assert result.observations[2].evidence.quote == f"July 9, 2026 | A{EN} | Stable"


def test_dated_by_agency_ignores_bond_tables(scales):
    result = extract_rating_observations(
        doc(OMV_PAGE), "ISSUER_TEST_B", scales, profile="dated_by_agency"
    )
    assert all(o.rating != "500" for o in result.observations) and len(result.observations) == 4


def test_en_dash_labels_are_canonical(scales):
    assert scales.agency("FITCH").canonical_label(f"A{EN}") == "A-"
    assert scales.agency("SP").canonical_label(f"BBB{MINUS}") == "BBB-"


def test_profiles_do_not_cross_match(scales):
    assert (
        extract_rating_observations(
            doc(VW_PAGE), "ISSUER_TEST_A", scales, profile="dated_by_agency"
        ).observations
        == []
    )
    assert (
        extract_rating_observations(
            doc(OMV_PAGE), "ISSUER_TEST_B", scales, profile="sec_as_of"
        ).observations
        == []
    )


def test_unknown_profile_rejected(scales):
    with pytest.raises(ValueError):
        extract_rating_observations(doc(VW_PAGE), "ISSUER_TEST_A", scales, profile="nope")
