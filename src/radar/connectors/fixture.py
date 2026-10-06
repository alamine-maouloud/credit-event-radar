"""Golden integration fixtures: raw bytes gzipped next to a manifest.

A fixture is a directory holding ``document.<suffix>.gz`` (the bytes exactly as fetched)
and ``manifest.json`` (provenance and expected hashes). Tests replay the full snapshot and
normalisation chain from the gzipped bytes and compare with the manifest, so a change in
the normaliser or in the source is caught immediately.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from radar.config import Issuer
from radar.models import RawDocument
from radar.snapshot import FetchedBytes, sha256_hex

MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class Fixture:
    directory: Path
    manifest: dict[str, Any]
    raw: bytes

    @property
    def fetched(self) -> FetchedBytes:
        return FetchedBytes(
            url=self.manifest["source_url"],
            content=self.raw,
            retrieved_at=datetime.fromisoformat(self.manifest["retrieved_at"]),
            content_type=self.manifest.get("content_type"),
        )


def write_fixture(
    directory: Path,
    fetched: FetchedBytes,
    doc: RawDocument,
    *,
    role: str,
    anchors: list[dict[str, Any]] | None = None,
    **provenance: Any,
) -> Path:
    """Store the raw bytes gzipped and a manifest with hashes and provenance."""
    directory.mkdir(parents=True, exist_ok=True)
    raw_name = f"document{fetched.suffix}.gz"
    # mtime=0 keeps the gzip bytes identical across rebuilds (reproducible fixture files)
    with open(directory / raw_name, "wb") as raw_fh:
        with gzip.GzipFile(fileobj=raw_fh, mode="wb", mtime=0) as fh:
            fh.write(fetched.content)
    manifest = {
        "role": role,
        "source_type": doc.source_type,
        "source_url": fetched.url,
        "content_type": fetched.content_type,
        "retrieved_at": fetched.retrieved_at.isoformat(),
        "raw_file": raw_name,
        "raw_sha256": doc.content_hash,
        "raw_size_bytes": doc.raw_size_bytes,
        "normalizer_version": doc.normalizer_version,
        "normalized_sha256": doc.doc_id,
        "normalized_chars": len(doc.text),
        "extra": doc.extra,
        "anchors": anchors or [],
        **provenance,
    }
    path = directory / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_fixture(directory: Path) -> Fixture:
    manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    with gzip.open(directory / manifest["raw_file"], "rb") as fh:
        raw = fh.read()
    if sha256_hex(raw) != manifest["raw_sha256"]:
        raise RuntimeError(f"{directory}: raw bytes do not match manifest raw_sha256")
    return Fixture(directory=directory, manifest=manifest, raw=raw)


def list_fixtures(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.rglob(MANIFEST_NAME))


class FixtureAdapter:
    """SourceAdapter over golden fixture directories: fully offline ingestion.

    Documents are rebuilt from the gzipped raw bytes with the current normaliser, so the
    pipeline runs exactly as it would on a live fetch, minus the network.
    """

    source_type = "edgar"

    def __init__(self, root: Path, raw_dir: Path, normalizer: Any) -> None:
        self.root = root
        self.raw_dir = raw_dir
        self.normalizer = normalizer

    def _build(self, fixture: Fixture) -> RawDocument:
        from radar.snapshot import build_raw_document

        m = fixture.manifest
        extra = dict(m.get("extra") or {})
        published = extra.get("filing_date")
        form = extra.get("form") or "fixture"
        title = f"{form} {extra.get('accession_number') or fixture.directory.name}"
        return build_raw_document(
            fixture.fetched,
            source_type=m["source_type"],
            raw_dir=self.raw_dir,
            normalizer=self.normalizer,
            title=title,
            published_at=datetime.fromisoformat(f"{published}T00:00:00+00:00")
            if published
            else None,
            issuer_hint=m.get("issuer_id"),
            extra=extra,
        )

    def fetch(self, since: date, issuers: Sequence[Issuer]) -> list[RawDocument]:
        wanted = {i.id for i in issuers}
        docs: list[RawDocument] = []
        for directory in list_fixtures(self.root):
            fixture = load_fixture(directory)
            m = fixture.manifest
            if wanted and m.get("issuer_id") not in wanted:
                continue
            filed = (m.get("extra") or {}).get("filing_date")
            if filed and date.fromisoformat(filed) < since:
                continue
            docs.append(self._build(fixture))
        return docs

    def fetch_document(self, url: str) -> RawDocument:
        for directory in list_fixtures(self.root):
            fixture = load_fixture(directory)
            if fixture.manifest["source_url"] == url:
                return self._build(fixture)
        raise KeyError(f"no fixture for {url}")
