"""SEC EDGAR adapter (docs/SPEC.md section 6).

Access rules enforced here, not left to the caller:

- every request carries the User-Agent read from ``SEC_USER_AGENT`` (a project name and a
  real contact address, as the SEC asks for automated access); a missing or placeholder
  value stops the adapter before any request;
- requests are rate limited below the SEC's 10 requests per second.

Phase 2 reads the ``filings.recent`` block of the submissions API (about the last 1000
filings per issuer) and downloads the primary document of the selected forms. Older
filings listed under ``filings.files`` are not paged yet.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar

import httpx

from radar.config import Issuer
from radar.connectors.base import SourceAdapter
from radar.models import RawDocument
from radar.normalize import Normalizer, SecHtmlNormalizer
from radar.snapshot import FetchedBytes, build_raw_document

SEC_USER_AGENT_ENV = "SEC_USER_AGENT"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{document}"
DEFAULT_FORMS: tuple[str, ...] = ("8-K", "8-K/A", "10-Q", "10-K", "6-K", "424B2", "424B5", "FWP")
DEFAULT_MAX_RPS = 8
TIMEOUT_SECONDS = 30.0

_ARCHIVE_URL_RE = re.compile(
    r"^https://www\.sec\.gov/Archives/edgar/data/(?P<cik>\d+)/(?P<accession>\d{18})/(?P<document>[^/?#]+)$"
)
_PLACEHOLDER_MARKERS = ("example.com", "example.invalid", "your-email", "<", ">")


class EdgarConfigError(RuntimeError):
    """The adapter cannot run because the SEC access rules are not satisfied."""


def user_agent_from_env(env: Mapping[str, str] | None = None) -> str:
    """Return the declared User-Agent, or raise if it is missing or a placeholder."""
    source = os.environ if env is None else env
    value = (source.get(SEC_USER_AGENT_ENV) or "").strip()
    if not value:
        raise EdgarConfigError(
            f"{SEC_USER_AGENT_ENV} is not set. Put a project name and a real contact e-mail in .env"
        )
    lowered = value.lower()
    if "@" not in value or any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        raise EdgarConfigError(
            f"{SEC_USER_AGENT_ENV} must contain a real contact address, got a placeholder"
        )
    return value


class RateLimiter:
    """Minimum interval between calls, with injectable clock and sleep for tests."""

    def __init__(
        self,
        max_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        self.min_interval = 1.0 / max_per_second
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self.min_interval - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last = now


@dataclass(frozen=True)
class Filing:
    cik: str
    accession_number: str
    form: str
    filing_date: date
    primary_document: str
    report_date: date | None = None
    items: tuple[str, ...] = field(default_factory=tuple)
    description: str | None = None

    @property
    def document_url(self) -> str:
        return ARCHIVES_URL.format(
            cik_int=int(self.cik),
            accession=self.accession_number.replace("-", ""),
            document=self.primary_document,
        )

    def extra(self) -> dict[str, str | list[str] | None]:
        return {
            "cik": self.cik,
            "accession_number": self.accession_number,
            "form": self.form,
            "items": list(self.items),
            "filing_date": self.filing_date.isoformat(),
            "report_date": self.report_date.isoformat() if self.report_date else None,
            "primary_document": self.primary_document,
        }


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def parse_submissions(payload: Mapping[str, object]) -> list[Filing]:
    """Turn the parallel arrays of ``filings.recent`` into Filing objects, newest first."""
    cik = str(payload["cik"]).zfill(10)
    recent = payload["filings"]["recent"]  # type: ignore[index]
    columns = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument", "items")
    rows = zip(*(recent.get(c, []) for c in columns), strict=True)  # type: ignore[union-attr]
    filings: list[Filing] = []
    descriptions = recent.get("primaryDocDescription", [])  # type: ignore[union-attr]
    for n, (accession, filed, report, form, primary, items) in enumerate(rows):
        filing_date = _parse_date(filed)
        if filing_date is None or not primary:
            continue
        filings.append(
            Filing(
                cik=cik,
                accession_number=accession,
                form=form,
                filing_date=filing_date,
                report_date=_parse_date(report),
                primary_document=primary,
                items=tuple(i.strip() for i in (items or "").split(",") if i.strip()),
                description=(descriptions[n] or None) if n < len(descriptions) else None,
            )
        )
    return filings


def select_filings(
    filings: Sequence[Filing], *, since: date, forms: Sequence[str] = DEFAULT_FORMS
) -> list[Filing]:
    wanted = set(forms)
    return [f for f in filings if f.filing_date >= since and f.form in wanted]


class EdgarAdapter(SourceAdapter):
    """Fetch SEC filings for issuers that have a ``sec_cik``."""

    source_type: ClassVar = "edgar"

    def __init__(
        self,
        raw_dir: Path,
        *,
        user_agent: str | None = None,
        forms: Sequence[str] = DEFAULT_FORMS,
        max_requests_per_second: float = DEFAULT_MAX_RPS,
        client: httpx.Client | None = None,
        normalizer: Normalizer | None = None,
        rate_limiter: RateLimiter | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_requests_per_second >= 10:
            raise EdgarConfigError("SEC access must stay below 10 requests per second")
        self.raw_dir = raw_dir
        self.user_agent = user_agent or user_agent_from_env()
        self.forms = tuple(forms)
        self.normalizer = normalizer or SecHtmlNormalizer()
        self.rate_limiter = rate_limiter or RateLimiter(max_requests_per_second)
        self._now = now
        self._client = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)

    @property
    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}

    def _get(self, url: str) -> FetchedBytes:
        self.rate_limiter.wait()
        response = self._client.get(url, headers=self.headers)
        response.raise_for_status()
        return FetchedBytes(
            url=url,
            content=response.content,
            retrieved_at=self._now(),
            content_type=response.headers.get("content-type"),
        )

    def list_filings(self, cik: str, *, since: date) -> list[Filing]:
        fetched = self._get(SUBMISSIONS_URL.format(cik=cik.zfill(10)))
        payload = httpx.Response(200, content=fetched.content).json()
        return select_filings(parse_submissions(payload), since=since, forms=self.forms)

    def fetch_filing(self, filing: Filing, *, issuer_id: str | None = None) -> RawDocument:
        fetched = self._get(filing.document_url)
        return build_raw_document(
            fetched,
            source_type="edgar",
            raw_dir=self.raw_dir,
            normalizer=self.normalizer,
            title=f"{filing.form} {filing.accession_number}",
            published_at=datetime(
                filing.filing_date.year,
                filing.filing_date.month,
                filing.filing_date.day,
                tzinfo=UTC,
            ),
            issuer_hint=issuer_id,
            extra=filing.extra(),
        )

    def fetch(self, since: date, issuers: Sequence[Issuer]) -> list[RawDocument]:
        documents: list[RawDocument] = []
        for issuer in issuers:
            if not issuer.sec_cik:
                continue
            for filing in self.list_filings(issuer.sec_cik, since=since):
                documents.append(self.fetch_filing(filing, issuer_id=issuer.id))
        return documents

    def fetch_document(self, url: str) -> RawDocument:
        """Fetch one EDGAR archive URL; connector metadata is derived from the path."""
        match = _ARCHIVE_URL_RE.match(url)
        extra: dict[str, str | list[str] | None] = {}
        if match:
            acc = match.group("accession")
            extra = {
                "cik": match.group("cik").zfill(10),
                "accession_number": f"{acc[:10]}-{acc[10:12]}-{acc[12:]}",
                "primary_document": match.group("document"),
            }
        fetched = self._get(url)
        return build_raw_document(
            fetched,
            source_type="edgar",
            raw_dir=self.raw_dir,
            normalizer=self.normalizer,
            extra=extra,
        )
