"""Deterministic extraction of agency rating tables (structured-ratings-table-1.0)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from radar.connectors.fixture import load_fixture
from radar.extract.ratings_table import TABLE_EXTRACTOR_VERSION, extract_rating_observations
from radar.extract.spans import verify_span
from radar.models import RawDocument
from radar.normalize import SecHtmlNormalizer
from radar.snapshot import build_raw_document, sha256_hex

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "edgar"

TABLE = """Liquidity section.
The Company's short- and long-term credit ratings, as of June 30, 2026 were as follows:
| Short-Term | Long-Term | Outlook
Moody's | P3 | | Baa3 | | Stable
Standard & Poor's(a) | A-3 | | BBB- | | CreditWatch Negative
Fitch | F2 | | BBB | | Negative
(a)On July 8, 2026, S&P Global Ratings lowered the Company's long-term credit rating from BBB- to BB+.
Unrelated line."""


def doc(text: str) -> RawDocument:
    return RawDocument(
        doc_id=sha256_hex(text), source_type="edgar", url="https://example.invalid/d", retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_hash=sha256_hex(text.encode()), raw_size_bytes=len(text), normalizer_version="t", text=text, raw_path="p", extra={"form": "10-Q"},
    )  # fmt: skip


def test_three_observations_with_as_of_date_and_spans():
    d = doc(TABLE)
    result = extract_rating_observations(
        d, "ISSUER_TEST_A", __import__("tests.conftest", fromlist=["x"]).load_rating_scales()
    )
    obs = {o.agency: o for o in result.observations}
    assert set(obs) == {"MOODYS", "SP", "FITCH"}
    assert all(o.as_of == date(2026, 6, 30) for o in obs.values())
    assert (obs["MOODYS"].rating, obs["MOODYS"].outlook, obs["MOODYS"].watch) == (
        "Baa3",
        "stable",
        "none",
    )
    assert (obs["SP"].rating, obs["SP"].outlook, obs["SP"].watch) == ("BBB-", None, "negative")
    assert (obs["FITCH"].rating, obs["FITCH"].outlook) == ("BBB", "negative")
    for o in obs.values():
        assert (
            o.evidence.evidence_type == "table_row"
            and o.extractor_version == TABLE_EXTRACTOR_VERSION
        )
        assert verify_span(d, o.evidence) and o.evidence_span_id
        assert o.rating_type == "long_term_issuer" and o.scope == "issuer"
        assert o.verification_method == "structured_table" and o.doc_id == d.doc_id
        assert o.issuer_id == "ISSUER_TEST_A"
    assert (
        obs["SP"].evidence.quote == "Standard & Poor's(a) | A-3 | | BBB- | | CreditWatch Negative"
    )
    assert result.skipped == []


def test_observation_id_is_deterministic():
    scales = __import__("tests.conftest", fromlist=["x"]).load_rating_scales()
    a = extract_rating_observations(doc(TABLE), "ISSUER_TEST_A", scales).observations
    b = extract_rating_observations(doc(TABLE), "ISSUER_TEST_A", scales).observations
    assert [o.observation_id for o in a] == [o.observation_id for o in b]


def test_short_term_b_is_not_taken_as_long_term(scales):
    text = TABLE.replace(
        "Standard & Poor's(a) | A-3 | | BBB- | | CreditWatch Negative",
        "Standard & Poor's | B | | BB+ | | Stable",
    )
    obs = {
        o.agency: o
        for o in extract_rating_observations(doc(text), "ISSUER_TEST_A", scales).observations
    }
    assert obs["SP"].rating == "BB+"


def test_long_term_column_before_short_term(scales):
    text = TABLE.replace(
        "| Short-Term | Long-Term | Outlook", "| Long-Term | Short-Term | Outlook"
    ).replace(
        "Standard & Poor's(a) | A-3 | | BBB- | | CreditWatch Negative",
        "Standard & Poor's | BB+ | | B | | Stable",
    )
    obs = {
        o.agency: o
        for o in extract_rating_observations(doc(text), "ISSUER_TEST_A", scales).observations
    }
    assert obs["SP"].rating == "BB+"


def test_no_as_of_sentence_no_observation(scales):
    text = TABLE.replace(", as of June 30, 2026 were as follows:", " were as follows:")
    assert extract_rating_observations(doc(text), "ISSUER_TEST_A", scales).observations == []


def test_row_without_recognisable_label_is_skipped(scales):
    text = TABLE.replace("Fitch | F2 | | BBB | | Negative", "Fitch | F2 | | n/a | | Negative")
    result = extract_rating_observations(doc(text), "ISSUER_TEST_A", scales)
    assert {o.agency for o in result.observations} == {"MOODYS", "SP"}
    assert [s.reason for s in result.skipped] == ["no_long_term_label"]


def test_table_stops_at_first_non_row_line(scales):
    text = TABLE.replace(
        "Fitch | F2 | | BBB | | Negative\n", "Some prose line.\nFitch | F2 | | BBB | | Negative\n"
    )
    assert {
        o.agency
        for o in extract_rating_observations(doc(text), "ISSUER_TEST_A", scales).observations
    } == {"MOODYS", "SP"}


def test_non_edgar_text_without_table_gives_nothing(scales):
    assert (
        extract_rating_observations(
            doc("Nothing to see. As of June 30, 2026 the ratings were fine."),
            "ISSUER_TEST_A",
            scales,
        ).observations
        == []
    )


def test_harley_fixture_observations(scales, tmp_path):
    fx = load_fixture(FIXTURES / "harley_davidson_inc_10q_2026q2")
    d = build_raw_document(
        fx.fetched,
        source_type="edgar",
        raw_dir=tmp_path,
        normalizer=SecHtmlNormalizer(),
        extra=fx.manifest["extra"],
    )
    result = extract_rating_observations(d, "HARLEY_DAVIDSON_INC", scales)
    obs = {o.agency: o for o in result.observations}
    assert set(obs) == {"MOODYS", "SP", "FITCH"}
    assert all(o.as_of == date(2026, 6, 30) for o in obs.values())
    assert (obs["MOODYS"].rating, obs["MOODYS"].outlook) == ("Baa3", "stable")
    assert (obs["SP"].rating, obs["SP"].watch) == ("BBB-", "negative")
    assert (obs["FITCH"].rating, obs["FITCH"].outlook) == ("BBB", "negative")
    assert all(verify_span(d, o.evidence) for o in obs.values())
