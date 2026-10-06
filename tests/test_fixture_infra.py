"""Private fixture infrastructure: manifest committed, bytes optional, replay through the adapter."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from radar.config import Issuer
from radar.connectors.fixture import (
    FixtureAdapter,
    FixtureBytesMissing,
    fixture_bytes_available,
    load_fixture,
    write_fixture,
)
from radar.normalize import SecHtmlNormalizer
from radar.snapshot import FetchedBytes, build_raw_document

HTML = b"<html><body><h1>Issuer Test A places EUR 500 million bond</h1><p>Issuer Test A AG placed a EUR 500 million bond due 2031.</p></body></html>"


def make_fixture(directory: Path, tmp_raw: Path) -> Path:
    fetched = FetchedBytes(
        url="https://example.invalid/press/bond", content=HTML,
        retrieved_at=datetime(2026, 10, 6, 12, 0, tzinfo=UTC), content_type="text/html",
    )  # fmt: skip
    doc = build_raw_document(
        fetched, source_type="ir_feed", raw_dir=tmp_raw, normalizer=SecHtmlNormalizer(),
        title="Issuer Test A places EUR 500 million bond",
        extra={"source_id": "src_a", "kind": "rss", "document_type": "press_release", "published": "2026-05-06"},
    )  # fmt: skip
    return write_fixture(
        directory, fetched, doc, role="demo_watchlist", issuer_id="ISSUER_TEST_A",
        expected={"events": [{"family": "issuance", "event_type": "new_issue"}]},
    )  # fmt: skip


def test_manifest_carries_expected_and_title(tmp_path: Path):
    path = make_fixture(tmp_path / "fx", tmp_path / "raw")
    m = json.loads(path.read_text())
    assert m["expected"]["events"][0]["event_type"] == "new_issue"
    assert m["title"].startswith("Issuer Test A") and m["issuer_id"] == "ISSUER_TEST_A"
    assert fixture_bytes_available(tmp_path / "fx")


def test_adapter_replays_ir_fixture_with_provenance(tmp_path: Path):
    make_fixture(tmp_path / "fx", tmp_path / "raw")
    adapter = FixtureAdapter(tmp_path, tmp_path / "raw2", SecHtmlNormalizer())
    issuer = Issuer(id="ISSUER_TEST_A", name="Issuer Test A", sector="t", country="DE")
    docs = adapter.fetch(date(2026, 1, 1), [issuer])
    assert len(docs) == 1
    d = docs[0]
    assert d.source_type == "ir_feed" and d.issuer_hint == "ISSUER_TEST_A"
    assert d.extra["document_type"] == "press_release" and d.published_at == datetime(
        2026, 5, 6, tzinfo=UTC
    )
    assert d.title == "Issuer Test A places EUR 500 million bond"
    assert "EUR 500 million bond" in d.text
    assert adapter.fetch(date(2026, 6, 1), [issuer]) == []  # published before since


def test_missing_bytes_raise_and_are_skipped(tmp_path: Path):
    make_fixture(tmp_path / "fx", tmp_path / "raw")
    gz = next((tmp_path / "fx").glob("document.*"))
    gz.unlink()
    assert not fixture_bytes_available(tmp_path / "fx")
    with pytest.raises(FixtureBytesMissing):
        load_fixture(tmp_path / "fx")
    adapter = FixtureAdapter(tmp_path, tmp_path / "raw2", SecHtmlNormalizer())
    issuer = Issuer(id="ISSUER_TEST_A", name="Issuer Test A", sector="t", country="DE")
    assert adapter.fetch(date(2026, 1, 1), [issuer]) == []
    assert adapter.skipped and "private fixture" in adapter.skipped[0][1]


def test_private_fixture_bytes_are_gitignored():
    root = Path(__file__).resolve().parents[1]
    assert "tests/fixtures/ir/*/document.*" in (root / ".gitignore").read_text()
