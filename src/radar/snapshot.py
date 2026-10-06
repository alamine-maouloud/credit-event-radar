"""Raw snapshots, hashes and RawDocument construction (SPEC section 8, step 1).

``content_hash`` is computed on the bytes exactly as delivered by the source, before any
decoding. ``doc_id`` is the hash of the normalised text and therefore depends on the
normaliser version recorded on the document.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from radar.models import RawDocument, SourceType
from radar.normalize import Normalizer

SUFFIX_BY_CONTENT_TYPE = {
    "text/html": ".htm",
    "application/xhtml+xml": ".htm",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "application/json": ".json",
}


@dataclass(frozen=True)
class FetchedBytes:
    """What an HTTP layer hands over: the bytes and when they were retrieved."""

    url: str
    content: bytes
    retrieved_at: datetime
    content_type: str | None = None

    @property
    def suffix(self) -> str:
        media = (self.content_type or "").split(";")[0].strip().lower()
        return SUFFIX_BY_CONTENT_TYPE.get(media, ".bin")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def snapshot_path(raw_dir: Path, source_type: SourceType, content_hash: str, suffix: str) -> Path:
    return raw_dir / source_type / f"{content_hash}{suffix}"


def write_snapshot(raw_dir: Path, source_type: SourceType, data: bytes, suffix: str) -> Path:
    """Write the raw bytes under their own hash. Idempotent: same bytes, same path."""
    content_hash = sha256_hex(data)
    path = snapshot_path(raw_dir, source_type, content_hash, suffix)
    if path.exists():
        existing = path.read_bytes()
        if sha256_hex(existing) != content_hash:
            raise RuntimeError(f"snapshot {path} is corrupted: hash mismatch")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def build_raw_document(
    fetched: FetchedBytes,
    *,
    source_type: SourceType,
    raw_dir: Path,
    normalizer: Normalizer,
    title: str | None = None,
    published_at: datetime | None = None,
    issuer_hint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> RawDocument:
    """Snapshot the bytes, normalise them and return a fully traceable RawDocument."""
    path = write_snapshot(raw_dir, source_type, fetched.content, fetched.suffix)
    text = normalizer.normalize(fetched.content)
    return RawDocument(
        doc_id=sha256_hex(text),
        source_type=source_type,
        url=fetched.url,
        title=title,
        published_at=published_at,
        retrieved_at=fetched.retrieved_at,
        content_hash=sha256_hex(fetched.content),
        raw_size_bytes=len(fetched.content),
        normalizer_version=normalizer.version,
        text=text,
        raw_path=str(path),
        issuer_hint=issuer_hint,
        extra=dict(extra or {}),
    )
