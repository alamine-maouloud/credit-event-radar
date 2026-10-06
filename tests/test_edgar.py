"""EDGAR adapter tests without network: mocked transport, fictional CIK 0000000001."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from radar.config import Issuer, load_dotenv
from radar.connectors.edgar import (
    DEFAULT_FORMS,
    EdgarAdapter,
    EdgarConfigError,
    Filing,
    RateLimiter,
    parse_submissions,
    select_filings,
    user_agent_from_env,
)

UA = "Credit Event Radar test suite research@issuer-test-a.invalid"
CIK = "0000000001"
SUBMISSIONS = {
    "cik": "1",
    "name": "ISSUER TEST A, INC.",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000000001-26-000003",
                "0000000001-26-000002",
                "0000000001-26-000001",
            ],
            "filingDate": ["2026-08-05", "2026-07-23", "2026-02-10"],
            "reportDate": ["2026-06-30", "2026-07-23", ""],
            "form": ["10-Q", "8-K", "SC 13G"],
            "primaryDocument": ["test-a-20260630.htm", "a8k.htm", "sc13g.htm"],
            "items": ["", "2.02,9.01", ""],
            "primaryDocDescription": ["10-Q", "8-K", ""],
        },
        "files": [],
    },
}
DOC_10Q = b"<html><body><p>S&amp;P lowered the rating from BBB- to BB+.</p></body></html>"
DOC_8K = b"<html><body><p>Results.</p></body></html>"


def transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if url == f"https://data.sec.gov/submissions/CIK{CIK}.json":
            return httpx.Response(200, json=SUBMISSIONS)
        if url.endswith("/000000000126000003/test-a-20260630.htm"):
            return httpx.Response(200, content=DOC_10Q, headers={"content-type": "text/html"})
        if url.endswith("/000000000126000002/a8k.htm"):
            return httpx.Response(200, content=DOC_8K, headers={"content-type": "text/html"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def adapter(tmp_path: Path, seen: list[httpx.Request], **kw) -> EdgarAdapter:
    return EdgarAdapter(
        tmp_path,
        user_agent=UA,
        client=httpx.Client(transport=transport(seen)),
        rate_limiter=RateLimiter(1000, clock=lambda: 0.0, sleep=lambda s: None),
        now=lambda: datetime(2026, 10, 6, 12, 0, tzinfo=UTC),
        **kw,
    )


# ------------------------------------------------------------ user agent --- #


@pytest.mark.parametrize(
    "value",
    [None, "", "Credit Event Radar", "Credit Event Radar contact@example.com", "x <you@x.com>"],
)
def test_user_agent_rejected(value):
    env = {} if value is None else {"SEC_USER_AGENT": value}
    with pytest.raises(EdgarConfigError):
        user_agent_from_env(env)


def test_user_agent_accepted():
    assert user_agent_from_env({"SEC_USER_AGENT": UA}) == UA


def test_adapter_requires_user_agent(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(EdgarConfigError):
        EdgarAdapter(tmp_path)


def test_adapter_refuses_ten_requests_per_second(tmp_path: Path):
    with pytest.raises(EdgarConfigError):
        EdgarAdapter(tmp_path, user_agent=UA, max_requests_per_second=10)


def test_env_example_user_agent_is_a_placeholder():
    env: dict[str, str] = {}
    load_dotenv(Path(__file__).resolve().parents[1] / ".env.example", env)
    with pytest.raises(EdgarConfigError):
        user_agent_from_env(env)


# ----------------------------------------------------------- submissions --- #


def test_parse_submissions():
    filings = parse_submissions(SUBMISSIONS)
    assert [f.form for f in filings] == ["10-Q", "8-K", "SC 13G"]
    f = filings[1]
    assert f.cik == CIK
    assert f.items == ("2.02", "9.01")
    assert f.filing_date == date(2026, 7, 23)
    assert f.report_date == date(2026, 7, 23)
    assert filings[2].report_date is None


def test_document_url_format():
    f = Filing(
        cik="0000793952",
        accession_number="0000793952-26-000061",
        form="10-Q",
        filing_date=date(2026, 8, 5),
        primary_document="hog-20260630.htm",
    )
    assert f.document_url == (
        "https://www.sec.gov/Archives/edgar/data/793952/000079395226000061/hog-20260630.htm"
    )


def test_select_filings_by_form_and_date():
    filings = parse_submissions(SUBMISSIONS)
    assert [f.form for f in select_filings(filings, since=date(2026, 1, 1))] == ["10-Q", "8-K"]
    assert [f.form for f in select_filings(filings, since=date(2026, 8, 1))] == ["10-Q"]
    assert select_filings(filings, since=date(2026, 1, 1), forms=["SC 13G"])[0].form == "SC 13G"
    assert "SC 13G" not in DEFAULT_FORMS


# ---------------------------------------------------------------- fetch --- #


def issuer_a() -> Issuer:
    return Issuer(
        id="ISSUER_TEST_A", name="Issuer Test A", sector="test", country="US", sec_cik=CIK
    )


def test_fetch_downloads_selected_filings(tmp_path: Path):
    seen: list[httpx.Request] = []
    docs = adapter(tmp_path, seen).fetch(date(2026, 1, 1), [issuer_a()])
    assert [d.extra["form"] for d in docs] == ["10-Q", "8-K"]
    doc = docs[0]
    assert doc.issuer_hint == "ISSUER_TEST_A"
    assert doc.extra["accession_number"] == "0000000001-26-000003"
    assert doc.extra["primary_document"] == "test-a-20260630.htm"
    assert doc.extra["filing_date"] == "2026-08-05"
    assert doc.published_at == datetime(2026, 8, 5, tzinfo=UTC)
    assert doc.retrieved_at == datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    assert "from BBB- to BB+" in doc.text
    assert Path(doc.raw_path).read_bytes() == DOC_10Q
    assert docs[1].extra["items"] == ["2.02", "9.01"]
    assert len(seen) == 3
    assert all(r.headers["User-Agent"] == UA for r in seen)


def test_fetch_skips_issuers_without_cik(tmp_path: Path):
    seen: list[httpx.Request] = []
    issuer = Issuer(id="ISSUER_TEST_B", name="Issuer Test B", sector="test", country="DE")
    assert adapter(tmp_path, seen).fetch(date(2026, 1, 1), [issuer]) == []
    assert seen == []


def test_fetch_document_derives_metadata_from_url(tmp_path: Path):
    seen: list[httpx.Request] = []
    url = "https://www.sec.gov/Archives/edgar/data/1/000000000126000003/test-a-20260630.htm"
    doc = adapter(tmp_path, seen).fetch_document(url)
    assert doc.extra == {
        "cik": CIK,
        "accession_number": "0000000001-26-000003",
        "primary_document": "test-a-20260630.htm",
    }
    assert doc.source_type == "edgar"


def test_http_error_is_raised(tmp_path: Path):
    seen: list[httpx.Request] = []
    with pytest.raises(httpx.HTTPStatusError):
        adapter(tmp_path, seen).fetch_document(
            "https://www.sec.gov/Archives/edgar/data/1/missing.htm"
        )


# ---------------------------------------------------------- rate limiter --- #


def test_rate_limiter_spaces_calls():
    clock = {"t": 0.0}
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        clock["t"] += s

    limiter = RateLimiter(8, clock=lambda: clock["t"], sleep=sleep)
    limiter.wait()
    limiter.wait()
    clock["t"] += 1.0
    limiter.wait()
    assert slept == [pytest.approx(0.125)]


def test_fetch_uses_rate_limiter(tmp_path: Path):
    calls: list[int] = []

    class Counting(RateLimiter):
        def wait(self) -> None:
            calls.append(1)

    seen: list[httpx.Request] = []
    ad = adapter(tmp_path, seen)
    ad.rate_limiter = Counting(8)
    ad.fetch(date(2026, 1, 1), [issuer_a()])
    assert len(calls) == 3


def test_submissions_payload_roundtrip():
    assert json.loads(json.dumps(SUBMISSIONS))["cik"] == "1"
