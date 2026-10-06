"""Every golden fixture replays the snapshot and normalisation chain against its manifest.

If the normaliser changes, normalized_sha256 and the anchors change and this test fails:
the manifest must then be regenerated on purpose, with the new normalizer_version.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from radar.connectors.fixture import (
    fixture_bytes_available,
    list_fixtures,
    load_fixture,
    load_manifest,
)
from radar.normalize import normalizer_for
from radar.snapshot import build_raw_document, sha256_hex

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"
FIXTURE_DIRS = list_fixtures(FIXTURES_ROOT)


def test_at_least_one_fixture_exists():
    assert FIXTURE_DIRS


@pytest.mark.parametrize("directory", FIXTURE_DIRS, ids=[d.name for d in FIXTURE_DIRS])
def test_fixture_replays_identically(directory: Path, tmp_path: Path):
    if not fixture_bytes_available(directory):
        pytest.skip(f"private fixture bytes not present for {directory.name}")
    fixture = load_fixture(directory)
    m = fixture.manifest
    normalizer = normalizer_for(m.get("content_type"), fixture.raw)
    assert m["normalizer_version"] == normalizer.version, "regenerate the manifest on purpose"
    assert sha256_hex(fixture.raw) == m["raw_sha256"]
    assert len(fixture.raw) == m["raw_size_bytes"]

    runs = [
        build_raw_document(
            fixture.fetched,
            source_type=m["source_type"],
            raw_dir=tmp_path,
            normalizer=normalizer,
        )
        for _ in range(2)
    ]
    assert runs[0].text == runs[1].text
    assert runs[0].doc_id == runs[1].doc_id == m["normalized_sha256"]
    assert runs[0].content_hash == m["raw_sha256"]
    assert len(runs[0].text) == m["normalized_chars"]

    for anchor in m["anchors"]:
        assert runs[0].text[anchor["char_start"] : anchor["char_end"]] == anchor["quote"]
        assert runs[0].text.count(anchor["quote"]) == 1


@pytest.mark.parametrize("directory", FIXTURE_DIRS, ids=[d.name for d in FIXTURE_DIRS])
def test_fixture_manifest_has_provenance(directory: Path):
    m = load_manifest(directory)
    for key in (
        "role",
        "source_url",
        "retrieved_at",
        "raw_sha256",
        "normalized_sha256",
        "issuer_id",
    ):
        assert m.get(key), key
    assert m["role"] in {"synthetic", "historical_control", "demo_watchlist"}
