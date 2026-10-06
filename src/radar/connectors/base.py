"""Source adapter interface (docs/SPEC.md section 8, step 1).

Every data source (EDGAR, issuer IR pages, news feeds, and later any licensed provider)
implements :class:`SourceAdapter`. Adapters return :class:`RawDocument` objects built by
:mod:`radar.snapshot`, so provenance (raw bytes hash, normalised text hash, timestamps)
is identical whatever the source.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from typing import ClassVar

from radar.config import Issuer
from radar.models import RawDocument, SourceType


class SourceAdapter(ABC):
    """Fetch documents for a set of issuers, or one document by URL."""

    source_type: ClassVar[SourceType]

    @abstractmethod
    def fetch(self, since: date, issuers: Sequence[Issuer]) -> list[RawDocument]:
        """Return every document published on or after ``since`` for ``issuers``."""

    @abstractmethod
    def fetch_document(self, url: str) -> RawDocument:
        """Fetch one document by URL (used for fixtures and manual ingestion)."""
