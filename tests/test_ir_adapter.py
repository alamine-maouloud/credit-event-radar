"""IR adapter without network: discovery kinds, robots.txt, cadence, PDF and HTML documents."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pymupdf
import pytest

from radar.config import IRSource, Issuer
from radar.connectors.edgar import EdgarConfigError
from radar.connectors.ir import (
    IRSourceAdapter,
    discover_page_links,
    discover_rss,
    discover_sitemap,
    ir_user_agent_from_env,
)
from radar.connectors.robots import HostCadence, RobotsGate
from radar.normalize import PDF_NORMALIZER_VERSION, PdfNormalizer, normalizer_for

UA = "Credit Event Radar test suite research@issuer-test-a.invalid"
HOST = "https://ir.issuer-test-a.invalid"

ROBOTS = b"User-agent: *\nDisallow: /private/\nAllow: /\n"
RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Issuer Test A</title>
<item><title>Issuer Test A reports first quarter results</title><link>HOST/press/q1-2026</link>
<pubDate>Thu, 30 Apr 2026 07:30:00 +0000</pubDate><category>Press releases</category><guid>pr-1</guid></item>
<item><title>Issuer Test A places EUR 500 million bond</title><link>HOST/press/bond-2026</link>
<pubDate>Wed, 06 May 2026 09:00:00 +0000</pubDate><category>Press releases</category><guid>pr-2</guid></item>
<item><title>A story about people</title><link>HOST/stories/people</link>
<pubDate>Mon, 01 Jun 2026 09:00:00 +0000</pubDate><category>Stories</category><guid>st-1</guid></item>
<item><title>Old news</title><link>HOST/press/old</link><pubDate>Mon, 05 Jan 2026 09:00:00 +0000</pubDate>
<category>Press releases</category><guid>pr-0</guid></item>
</channel></rss>""".replace(b"HOST", HOST.encode())
SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>HOST/en/investors/news/2026/hybrid-notice</loc><lastmod>2026-08-07T10:00:00.000Z</lastmod></url>
<url><loc>HOST/en/investors/news/2026/q2</loc><lastmod>2026-07-31</lastmod></url>
<url><loc>HOST/en/investors/news/2025/q4</loc><lastmod>2025-11-01</lastmod></url>
<url><loc>HOST/en/media/other</loc><lastmod>2026-09-01</lastmod></url>
<url><loc>HOST/en/investors/news/2026/undated</loc></url>
</urlset>""".replace(b"HOST", HOST.encode())
SITEMAP_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>HOST/sitemap-news.xml</loc></sitemap></sitemapindex>""".replace(
    b"HOST", HOST.encode()
)
RATINGS_PAGE = b"""<html><body><h1>Ratings</h1>
<a href="https://cdn.issuer-test-a.invalid/docs/fitch-release.pdf?1">Fitch Rating Release</a>
<a href="/en/esg-ratings">ESG</a>
<a href="https://cdn.issuer-test-a.invalid/docs/fitch-release.pdf?1#top">Fitch again</a>
<a href="https://cdn.issuer-test-a.invalid/docs/sp-release.pdf">S&amp;P Release</a>
<table><tr><td>Fitch</td><td>Short-Term</td><td>Long-Term</td><td>Outlook</td></tr>
<tr><td>Issuer Test A</td><td>F-1</td><td>A-</td><td>Negative</td></tr></table></body></html>"""
HTML_DOC = b"<html><body><p>Issuer Test A places a EUR 500 million bond due 2031.</p></body></html>"


def pdf_bytes(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


PDF_DOC = pdf_bytes(
    "Fitch Ratings has revised the Outlook on Issuer Test A to Negative from Stable."
)


def transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if url.endswith("/robots.txt"):
            if request.url.host.startswith("cdn."):
                return httpx.Response(404)
            return httpx.Response(200, content=ROBOTS)
        routes = {
            f"{HOST}/feed": (RSS, "application/rss+xml"),
            f"{HOST}/sitemap.xml": (SITEMAP, "application/xml"),
            f"{HOST}/sitemap-index.xml": (SITEMAP_INDEX, "application/xml"),
            f"{HOST}/sitemap-news.xml": (SITEMAP, "application/xml"),
            f"{HOST}/ratings": (RATINGS_PAGE, "text/html; charset=utf-8"),
            f"{HOST}/press/q1-2026": (HTML_DOC, "text/html"),
            f"{HOST}/press/bond-2026": (HTML_DOC, "text/html"),
            f"{HOST}/en/investors/news/2026/hybrid-notice": (HTML_DOC, "text/html"),
            f"{HOST}/en/investors/news/2026/q2": (HTML_DOC, "text/html"),
            f"{HOST}/en/investors/news/2026/undated": (HTML_DOC, "text/html"),
            f"{HOST}/private/secret": (HTML_DOC, "text/html"),
            "https://cdn.issuer-test-a.invalid/docs/fitch-release.pdf?1": (
                PDF_DOC,
                "application/pdf",
            ),
            "https://cdn.issuer-test-a.invalid/docs/sp-release.pdf": (
                PDF_DOC,
                "application/octet-stream",
            ),
        }
        if url in routes:
            body, ctype = routes[url]
            return httpx.Response(200, content=body, headers={"content-type": ctype})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def adapter(tmp_path: Path, seen: list[httpx.Request], **kw) -> IRSourceAdapter:
    return IRSourceAdapter(
        tmp_path,
        user_agent=UA,
        client=httpx.Client(transport=transport(seen)),
        cadence=HostCadence(0.5, clock=lambda: 0.0, sleep=lambda s: None),
        now=lambda: datetime(2026, 10, 6, 12, 0, tzinfo=UTC),
        **kw,
    )


def src(**fields) -> IRSource:
    base = dict(id="src_a", kind="rss", url=f"{HOST}/feed", document_type="press_release")
    base.update(fields)
    return IRSource(**base)


def issuer(*sources: IRSource) -> Issuer:
    return Issuer(
        id="ISSUER_TEST_A", name="Issuer Test A", sector="t", country="DE", ir_sources=list(sources)
    )


# ------------------------------------------------------------ user agent --- #


def test_ir_user_agent_fallback_and_validation():
    assert ir_user_agent_from_env({"IR_USER_AGENT": UA}) == UA
    assert ir_user_agent_from_env({"SEC_USER_AGENT": UA}) == UA
    with pytest.raises(EdgarConfigError):
        ir_user_agent_from_env({})
    with pytest.raises(EdgarConfigError):
        ir_user_agent_from_env({"IR_USER_AGENT": "Radar contact@example.com"})


# ------------------------------------------------------------- discovery --- #


def test_discover_rss_filters_since_and_categories():
    found = discover_rss(RSS, src(include_categories=["Press releases"]), date(2026, 3, 1))
    assert [d.url for d in found] == [f"{HOST}/press/q1-2026", f"{HOST}/press/bond-2026"]
    assert found[0].published == date(2026, 4, 30) and found[0].title.startswith(
        "Issuer Test A reports"
    )
    assert found[0].extra["entry_id"] == "pr-1"


def test_discover_rss_title_filter_and_max_items():
    found = discover_rss(RSS, src(include_title="(?i)bond"), date(2026, 1, 1))
    assert [d.url for d in found] == [f"{HOST}/press/bond-2026"]
    assert len(discover_rss(RSS, src(max_items=1), date(2026, 1, 1))) == 1


def test_discover_sitemap_prefix_lastmod_and_order():
    source = src(kind="sitemap", url=f"{HOST}/sitemap.xml", path_prefix="/en/investors/news/")
    found, nested = discover_sitemap(SITEMAP, source, date(2026, 7, 1))
    assert nested == []
    assert [d.url.rsplit("/", 1)[1] for d in found] == ["hybrid-notice", "q2", "undated"]
    assert found[0].published == date(2026, 8, 7) and found[2].published is None


def test_discover_sitemap_index_is_followed(tmp_path: Path):
    seen: list[httpx.Request] = []
    source = src(kind="sitemap", url=f"{HOST}/sitemap-index.xml", path_prefix="/en/investors/news/")
    found = adapter(tmp_path, seen).discover(source, date(2026, 7, 1))
    assert len(found) == 3


def test_discover_page_links_resolves_and_dedupes():
    source = src(
        kind="page_links",
        url=f"{HOST}/ratings",
        link_pattern=r"cdn\.issuer-test-a\.invalid/.*\.pdf",
    )
    found = discover_page_links(RATINGS_PAGE, source)
    assert [d.url for d in found] == [
        "https://cdn.issuer-test-a.invalid/docs/fitch-release.pdf?1",
        "https://cdn.issuer-test-a.invalid/docs/sp-release.pdf",
    ]
    assert found[0].title == "Fitch Rating Release"


# ----------------------------------------------------------------- fetch --- #


def test_fetch_rss_documents_carry_provenance(tmp_path: Path):
    seen: list[httpx.Request] = []
    docs = adapter(tmp_path, seen).fetch(
        date(2026, 3, 1), [issuer(src(include_categories=["Press releases"]))]
    )
    assert [str(d.url) for d in docs] == [f"{HOST}/press/q1-2026", f"{HOST}/press/bond-2026"]
    d = docs[0]
    assert d.source_type == "ir_feed" and d.issuer_hint == "ISSUER_TEST_A"
    assert (
        d.extra["source_id"] == "src_a"
        and d.extra["kind"] == "rss"
        and d.extra["document_type"] == "press_release"
    )
    assert d.extra["published"] == "2026-04-30" and d.published_at == datetime(
        2026, 4, 30, tzinfo=UTC
    )
    assert d.title.startswith("Issuer Test A reports") and d.normalizer_version == "sec-html-1.0"
    assert "EUR 500 million bond" in d.text
    assert Path(d.raw_path).read_bytes() == HTML_DOC
    assert all(r.headers["User-Agent"] == UA for r in seen)
    assert seen[0].url.path == "/robots.txt"


def test_fetch_page_source_is_the_document_itself(tmp_path: Path):
    seen: list[httpx.Request] = []
    source = src(
        kind="page",
        url=f"{HOST}/ratings",
        document_type="ratings_page",
        table_profile="current_by_agency",
    )
    docs = adapter(tmp_path, seen).fetch(date(2026, 1, 1), [issuer(source)])
    assert len(docs) == 1
    d = docs[0]
    assert d.extra["table_profile"] == "current_by_agency" and d.published_at is None
    assert "Issuer Test A | F-1 | A- | Negative" in d.text
    assert d.title == "src_a"


def test_fetch_pdf_links_use_the_pdf_normaliser(tmp_path: Path):
    seen: list[httpx.Request] = []
    source = src(
        kind="page_links",
        url=f"{HOST}/ratings",
        link_pattern=r"\.pdf",
        document_type="rating_report",
        content="pdf",
    )
    docs = adapter(tmp_path, seen).fetch(date(2026, 1, 1), [issuer(source)])
    assert len(docs) == 2
    for d in docs:
        assert d.normalizer_version == PDF_NORMALIZER_VERSION
        assert "revised the Outlook on Issuer Test A to Negative from Stable" in d.text
        assert Path(d.raw_path).suffix in {".pdf", ".bin"}
    assert docs[0].extra["link_text"] == "Fitch Rating Release"


def test_robots_disallow_is_honoured(tmp_path: Path):
    seen: list[httpx.Request] = []
    ad = adapter(tmp_path, seen)
    with pytest.raises(PermissionError):
        ad.fetch_document(f"{HOST}/private/secret")
    assert ad.skipped[0].reason == "disallowed by robots.txt"
    assert not any(r.url.path == "/private/secret" for r in seen)
    doc = ad.fetch_document(f"{HOST}/press/q1-2026")
    assert doc.issuer_hint is None and doc.extra.get("source_id") is None


def test_robots_can_be_disabled_explicitly(tmp_path: Path):
    seen: list[httpx.Request] = []
    doc = adapter(tmp_path, seen, respect_robots=False).fetch_document(f"{HOST}/private/secret")
    assert doc.text


def test_robots_missing_allows_everything():
    seen: list[httpx.Request] = []
    gate = RobotsGate(httpx.Client(transport=transport(seen)), UA)
    assert gate.allows("https://cdn.issuer-test-a.invalid/docs/x.pdf")
    assert gate.allows(f"{HOST}/press/x") and not gate.allows(f"{HOST}/private/x")
    assert len([r for r in seen if r.url.path == "/robots.txt"]) == 2  # cached per host


def test_fetch_errors_are_skipped_not_raised(tmp_path: Path):
    seen: list[httpx.Request] = []
    source = src(kind="page_links", url=f"{HOST}/ratings", link_pattern=r"esg")
    ad = adapter(tmp_path, seen)
    assert ad.fetch(date(2026, 1, 1), [issuer(source)]) == []
    assert ad.skipped and "fetch failed" in ad.skipped[0].reason


def test_cadence_is_per_host():
    clock = {"t": 0.0}
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        clock["t"] += s

    cadence = HostCadence(2.0, clock=lambda: clock["t"], sleep=sleep)
    cadence.wait(f"{HOST}/a")
    cadence.wait("https://other.invalid/a")
    cadence.wait(f"{HOST}/b")
    assert slept == [pytest.approx(2.0)]


# ------------------------------------------------------------ normaliser --- #


def test_pdf_normaliser_is_deterministic():
    text1 = PdfNormalizer().normalize(PDF_DOC)
    text2 = PdfNormalizer().normalize(PDF_DOC)
    assert text1 == text2 and "to Negative from Stable" in text1


def test_normalizer_selection_by_content_type_and_sniffing():
    assert normalizer_for("application/pdf", b"x").version == PDF_NORMALIZER_VERSION
    assert normalizer_for("application/octet-stream", PDF_DOC).version == PDF_NORMALIZER_VERSION
    assert normalizer_for("text/html", b"<p>x</p>").version == "sec-html-1.0"
    assert normalizer_for(None, b"<p>x</p>").version == "sec-html-1.0"
