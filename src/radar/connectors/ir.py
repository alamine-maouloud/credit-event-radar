"""Generic investor-relations adapter configured per issuer (docs/SPEC.md section 6).

Four discovery kinds, declared in ``universe.yaml`` under ``ir_sources``:

- ``rss``: feed entries (feedparser), optional title regex and category filter;
- ``sitemap``: URLs of a sitemap (or sitemap index) under ``path_prefix``, with ``lastmod``;
- ``page_links``: links of a page whose href matches ``link_pattern``;
- ``page``: the page itself is the document, re-snapshotted on every run (identical bytes
  are deduplicated by raw hash downstream).

Same discipline as EDGAR: declared User-Agent read from ``.env``, robots.txt honoured,
polite cadence per host, raw bytes hashed before any parsing, normaliser version recorded.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

from radar.config import IRSource, Issuer
from radar.connectors.base import SourceAdapter
from radar.connectors.edgar import SEC_USER_AGENT_ENV, EdgarConfigError, user_agent_from_env
from radar.connectors.robots import HostCadence, RobotsGate
from radar.models import RawDocument
from radar.normalize import normalizer_for
from radar.snapshot import FetchedBytes, build_raw_document

IR_USER_AGENT_ENV = "IR_USER_AGENT"
TIMEOUT_SECONDS = 30.0
MAX_NESTED_SITEMAPS = 20


def ir_user_agent_from_env(env: Mapping[str, str] | None = None) -> str:
    """IR_USER_AGENT, falling back to SEC_USER_AGENT; both must carry a real contact."""
    source = os.environ if env is None else env
    if source.get(IR_USER_AGENT_ENV, "").strip():
        return user_agent_from_env({SEC_USER_AGENT_ENV: source[IR_USER_AGENT_ENV]})
    try:
        return user_agent_from_env(source)
    except EdgarConfigError as exc:
        raise EdgarConfigError(
            f"{IR_USER_AGENT_ENV} (or {SEC_USER_AGENT_ENV}) is required: {exc}"
        ) from exc


@dataclass(frozen=True)
class Discovered:
    url: str
    title: str | None
    published: date | None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Skipped:
    url: str
    reason: str


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "tm_year"):
        return date(value.tm_year, value.tm_mon, value.tm_mday)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError):
        return None


def discover_rss(raw: bytes, source: IRSource, since: date) -> list[Discovered]:
    parsed = feedparser.parse(raw)
    title_re = re.compile(source.include_title) if source.include_title else None
    wanted = {c.casefold() for c in source.include_categories}
    out: list[Discovered] = []
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        if not link:
            continue
        title = (entry.get("title") or "").strip() or None
        published = _to_date(
            entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("published")
        )
        if published is not None and published < since:
            continue
        if title_re and not (title and title_re.search(title)):
            continue
        if wanted:
            tags = {str(t.get("term", "")).casefold() for t in entry.get("tags", [])}
            if not tags & wanted:
                continue
        out.append(Discovered(link, title, published, {"entry_id": entry.get("id") or link}))
        if len(out) >= source.max_items:
            break
    return out


def _sitemap_entries(raw: bytes) -> tuple[list[tuple[str, date | None]], list[str]]:
    """(urls with lastmod, nested sitemap urls) from a urlset or a sitemapindex."""
    root = ET.fromstring(raw)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: list[tuple[str, date | None]] = []
    nested: list[str] = []
    tag = root.tag.split("}")[-1]
    if tag == "sitemapindex":
        for item in root.findall("sm:sitemap", ns):
            loc = item.findtext("sm:loc", default="", namespaces=ns).strip()
            if loc:
                nested.append(loc)
    else:
        for item in root.findall("sm:url", ns):
            loc = item.findtext("sm:loc", default="", namespaces=ns).strip()
            if loc:
                lastmod = _to_date(item.findtext("sm:lastmod", default="", namespaces=ns))
                urls.append((loc, lastmod))
    return urls, nested


def discover_sitemap(
    raw: bytes, source: IRSource, since: date
) -> tuple[list[Discovered], list[str]]:
    urls, nested = _sitemap_entries(raw)
    base = urlsplit(str(source.url))
    prefix = source.path_prefix or "/"
    out: list[Discovered] = []
    for loc, lastmod in urls:
        parts = urlsplit(loc)
        if parts.netloc != base.netloc or not parts.path.startswith(prefix):
            continue
        if lastmod is not None and lastmod < since:
            continue
        extra = {"lastmod": lastmod.isoformat() if lastmod else None}
        out.append(Discovered(loc, None, lastmod, extra))
    out.sort(key=lambda d: (d.published or date.min, d.url), reverse=True)
    return out[: source.max_items], nested


def discover_page_links(raw: bytes, source: IRSource) -> list[Discovered]:
    pattern = re.compile(source.link_pattern or "")
    soup = BeautifulSoup(raw, "html.parser")
    seen: set[str] = set()
    out: list[Discovered] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(str(source.url), str(a["href"]).strip()).split("#")[0]
        if not pattern.search(href) or href in seen:
            continue
        seen.add(href)
        text = " ".join(a.get_text(" ", strip=True).split()) or None
        out.append(Discovered(href, text, None, {"link_text": text}))
        if len(out) >= source.max_items:
            break
    return out


class IRSourceAdapter(SourceAdapter):
    """Fetch documents from the configured investor-relations sources of each issuer."""

    source_type: ClassVar = "ir_feed"

    def __init__(
        self,
        raw_dir: Path,
        *,
        user_agent: str | None = None,
        client: httpx.Client | None = None,
        min_interval_seconds: float = 2.0,
        respect_robots: bool = True,
        cadence: HostCadence | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.raw_dir = raw_dir
        self.user_agent = user_agent or ir_user_agent_from_env()
        self._client = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)
        self.cadence = cadence or HostCadence(min_interval_seconds)
        self.robots = RobotsGate(self._client, self.user_agent, enabled=respect_robots)
        self._now = now
        self.skipped: list[Skipped] = []

    @property
    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}

    def _get(self, url: str) -> FetchedBytes | None:
        if not self.robots.allows(url):
            self.skipped.append(Skipped(url, "disallowed by robots.txt"))
            return None
        self.cadence.wait(url)
        response = self._client.get(url, headers=self.headers)
        response.raise_for_status()
        return FetchedBytes(
            url=str(response.url),
            content=response.content,
            retrieved_at=self._now(),
            content_type=response.headers.get("content-type"),
        )

    def discover(self, source: IRSource, since: date) -> list[Discovered]:
        if source.kind == "page":
            return [Discovered(str(source.url), None, None, {})]
        fetched = self._get(str(source.url))
        if fetched is None:
            return []
        if source.kind == "rss":
            return discover_rss(fetched.content, source, since)
        if source.kind == "page_links":
            return discover_page_links(fetched.content, source)
        found, nested = discover_sitemap(fetched.content, source, since)
        for nested_url in nested[:MAX_NESTED_SITEMAPS]:
            child = self._get(nested_url)
            if child is None:
                continue
            more, _ = discover_sitemap(child.content, source, since)
            found.extend(more)
        found.sort(key=lambda d: (d.published or date.min, d.url), reverse=True)
        return found[: source.max_items]

    def _build(
        self,
        fetched: FetchedBytes,
        issuer: Issuer | None,
        source: IRSource | None,
        found: Discovered | None,
    ) -> RawDocument:
        normalizer = normalizer_for(fetched.content_type, fetched.content)
        extra: dict[str, Any] = {"content_type": fetched.content_type}
        if source is not None:
            extra.update(
                {
                    "source_id": source.id,
                    "kind": source.kind,
                    "document_type": source.document_type,
                    "table_profile": source.table_profile,
                    "expected_content": source.content,
                }
            )
        published_at = None
        if found is not None:
            extra.update(found.extra)
            extra["discovered_title"] = found.title
            extra["published"] = found.published.isoformat() if found.published else None
            if found.published is not None:
                p = found.published
                published_at = datetime(p.year, p.month, p.day, tzinfo=UTC)
        return build_raw_document(
            fetched,
            source_type="ir_feed",
            raw_dir=self.raw_dir,
            normalizer=normalizer,
            title=(found.title if found else None) or (source.id if source else None),
            published_at=published_at,
            issuer_hint=issuer.id if issuer else None,
            extra=extra,
        )

    def fetch(self, since: date, issuers: Sequence[Issuer]) -> list[RawDocument]:
        documents: list[RawDocument] = []
        for issuer in issuers:
            for source in issuer.ir_sources:
                for found in self.discover(source, since):
                    try:
                        fetched = self._get(found.url)
                    except httpx.HTTPError as exc:
                        reason = f"fetch failed ({exc.__class__.__name__})"
                        self.skipped.append(Skipped(found.url, reason))
                        continue
                    if fetched is None:
                        continue
                    documents.append(self._build(fetched, issuer, source, found))
        return documents

    def fetch_document(self, url: str) -> RawDocument:
        fetched = self._get(url)
        if fetched is None:
            raise PermissionError(f"{url} is disallowed by robots.txt")
        return self._build(fetched, None, None, None)
