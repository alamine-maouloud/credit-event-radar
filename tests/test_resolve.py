"""Issuer resolution: CIK first, then hint, then exact alias; never a guess."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from radar.config import Issuer, Universe
from radar.models import RawDocument
from radar.resolve import resolve_by_alias, resolve_document

HEX = "0" * 64


def universe() -> Universe:
    return Universe(
        name="test",
        disclosure="fictional",
        retrieved_as_of="2026-01-01",
        issuers=[
            Issuer(
                id="ISSUER_TEST_A",
                name="Issuer Test A",
                legal_entity="Issuer Test A, Inc.",
                aliases=["ITA"],
                sector="t",
                country="US",
                sec_cik="0000000001",
            ),
            Issuer(
                id="ISSUER_TEST_B", name="Issuer Test B", aliases=["VWB"], sector="t", country="DE"
            ),
            Issuer(
                id="ISSUER_TEST_B_FS",
                name="Issuer Test B Financial Services",
                aliases=["ITBFS"],
                sector="t",
                country="US",
            ),
        ],
    )


def doc(
    text: str = "", title: str | None = None, hint: str | None = None, cik: str | None = None
) -> RawDocument:
    return RawDocument(
        doc_id=HEX, source_type="edgar" if cik else "manual", url="https://example.invalid/d",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC), content_hash=HEX, raw_size_bytes=1,
        normalizer_version="t", text=text, raw_path="p", title=title, issuer_hint=hint,
        extra={"cik": cik} if cik else {},
    )  # fmt: skip


def test_cik_resolution():
    r = resolve_document(doc(cik="1"), universe())
    assert (r.issuer_id, r.method) == ("ISSUER_TEST_A", "cik")
    assert r.resolved


def test_cik_unknown_is_unresolved_even_with_alias_in_text():
    r = resolve_document(doc(text="Issuer Test A reported", cik="0000000009"), universe())
    assert r.method == "unresolved" and "not in universe" in r.detail


def test_hint_must_agree_with_cik():
    r = resolve_document(doc(cik="1", hint="ISSUER_TEST_B"), universe())
    assert r.method == "unresolved" and "disagrees" in r.detail
    assert resolve_document(doc(cik="1", hint="ISSUER_TEST_A"), universe()).method == "cik"


def test_hint_resolution():
    assert resolve_document(doc(hint="ISSUER_TEST_B"), universe()).method == "hint"
    assert resolve_document(doc(hint="UNKNOWN"), universe()).method == "unresolved"


def test_alias_resolution_unique():
    r = resolve_document(doc(text="Today Issuer Test A, Inc. announced results."), universe())
    assert (r.issuer_id, r.method) == ("ISSUER_TEST_A", "alias")


def test_alias_resolution_uses_title():
    r = resolve_document(doc(title="ITA second quarter"), universe())
    assert r.issuer_id == "ISSUER_TEST_A"


def test_alias_ambiguous_is_unresolved():
    r = resolve_document(doc(text="Issuer Test A and Issuer Test B signed."), universe())
    assert r.method == "unresolved" and "ambiguous" in r.detail


def test_parent_and_subsidiary_are_distinct():
    r = resolve_by_alias("Issuer Test B Financial Services issued notes", universe())
    assert (
        r.method == "unresolved" and "ISSUER_TEST_B" in r.detail and "ISSUER_TEST_B_FS" in r.detail
    )


@pytest.mark.parametrize("text", ["VWBX", "xVWB", "nothing here"])
def test_alias_needs_word_boundaries(text):
    assert resolve_by_alias(text, universe()).method == "unresolved"


def test_alias_is_case_insensitive():
    assert resolve_by_alias("issuer test a", universe()).issuer_id == "ISSUER_TEST_A"
