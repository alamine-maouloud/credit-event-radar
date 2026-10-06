"""Snapshots and RawDocument construction: hashes on raw bytes, idempotent writes."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from radar.normalize import SecHtmlNormalizer
from radar.snapshot import (
    FetchedBytes,
    build_raw_document,
    sha256_hex,
    snapshot_path,
    write_snapshot,
)

RAW = b"<html><body><p>Hello&#160;world</p></body></html>"
AT = datetime(2026, 10, 6, 9, 30, tzinfo=UTC)


def fetched(content: bytes = RAW, content_type: str | None = "text/html") -> FetchedBytes:
    return FetchedBytes(
        url="https://example.invalid/doc.htm",
        content=content,
        retrieved_at=AT,
        content_type=content_type,
    )


def test_sha256_hex_matches_hashlib():
    assert sha256_hex(RAW) == hashlib.sha256(RAW).hexdigest()
    assert sha256_hex("abc") == hashlib.sha256(b"abc").hexdigest()


@pytest.mark.parametrize(
    "content_type,suffix",
    [("text/html", ".htm"), ("text/html; charset=utf-8", ".htm"), ("application/pdf", ".pdf"),
     (None, ".bin"), ("image/png", ".bin")],
)  # fmt: skip
def test_suffix_from_content_type(content_type, suffix):
    assert fetched(content_type=content_type).suffix == suffix


def test_write_snapshot_is_idempotent(tmp_path: Path):
    p1 = write_snapshot(tmp_path, "edgar", RAW, ".htm")
    p2 = write_snapshot(tmp_path, "edgar", RAW, ".htm")
    assert p1 == p2 == snapshot_path(tmp_path, "edgar", sha256_hex(RAW), ".htm")
    assert p1.read_bytes() == RAW
    assert p1.parent.name == "edgar"


def test_write_snapshot_detects_corruption(tmp_path: Path):
    p = write_snapshot(tmp_path, "edgar", RAW, ".htm")
    p.write_bytes(b"tampered")
    with pytest.raises(RuntimeError):
        write_snapshot(tmp_path, "edgar", RAW, ".htm")


def test_build_raw_document(tmp_path: Path):
    doc = build_raw_document(
        fetched(),
        source_type="edgar",
        raw_dir=tmp_path,
        normalizer=SecHtmlNormalizer(),
        extra={"form": "10-Q"},
    )
    assert doc.content_hash == sha256_hex(RAW)
    assert doc.raw_size_bytes == len(RAW)
    assert doc.text == "Hello world"
    assert doc.doc_id == sha256_hex("Hello world")
    assert doc.normalizer_version == SecHtmlNormalizer.version
    assert Path(doc.raw_path).read_bytes() == RAW
    assert doc.retrieved_at == AT
    assert doc.extra == {"form": "10-Q"}


def test_same_bytes_same_document(tmp_path: Path):
    a = build_raw_document(
        fetched(), source_type="edgar", raw_dir=tmp_path, normalizer=SecHtmlNormalizer()
    )
    b = build_raw_document(
        fetched(), source_type="edgar", raw_dir=tmp_path, normalizer=SecHtmlNormalizer()
    )
    assert (a.doc_id, a.content_hash, a.raw_path, a.text) == (
        b.doc_id,
        b.content_hash,
        b.raw_path,
        b.text,
    )


def test_raw_hash_differs_when_bytes_differ_even_if_text_equal(tmp_path: Path):
    a = build_raw_document(
        fetched(RAW), source_type="edgar", raw_dir=tmp_path, normalizer=SecHtmlNormalizer()
    )
    b = build_raw_document(
        fetched(RAW.replace(b"<p>", b"<p >")),
        source_type="edgar",
        raw_dir=tmp_path,
        normalizer=SecHtmlNormalizer(),
    )
    assert a.text == b.text and a.doc_id == b.doc_id
    assert a.content_hash != b.content_hash
