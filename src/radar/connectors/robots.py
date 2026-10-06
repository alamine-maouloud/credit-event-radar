"""robots.txt gate and per-host cadence for issuer sites.

Decisions use the standard library parser on each host's robots.txt, fetched once per
adapter instance. A missing robots.txt (4xx) allows everything; a transport error is
recorded in ``warnings`` and treated as allowed, since every site configured here was
checked by hand first (docs/ARCHITECTURE.md, ADR-010).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from radar.connectors.edgar import RateLimiter


class RobotsGate:
    def __init__(self, client: httpx.Client, user_agent: str, *, enabled: bool = True) -> None:
        self._client = client
        self._user_agent = user_agent
        self.enabled = enabled
        self._parsers: dict[str, RobotFileParser | None] = {}
        self.warnings: list[str] = []

    def _parser_for(self, host: str, scheme: str) -> RobotFileParser | None:
        if host in self._parsers:
            return self._parsers[host]
        parser: RobotFileParser | None = RobotFileParser()
        try:
            response = self._client.get(
                f"{scheme}://{host}/robots.txt", headers={"User-Agent": self._user_agent}
            )
            if response.status_code >= 400:
                parser = None
            else:
                assert parser is not None
                parser.parse(response.text.splitlines())
        except httpx.HTTPError as exc:
            self.warnings.append(f"{host}: robots.txt unavailable ({exc.__class__.__name__})")
            parser = None
        self._parsers[host] = parser
        return parser

    def allows(self, url: str) -> bool:
        if not self.enabled:
            return True
        parts = urlsplit(url)
        parser = self._parser_for(parts.netloc, parts.scheme or "https")
        if parser is None:
            return True
        return parser.can_fetch(self._user_agent, url)


class HostCadence:
    """One rate limiter per host, so a slow issuer site never blocks another."""

    def __init__(
        self,
        min_interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._per_second = 1.0 / min_interval_seconds
        self._clock = clock
        self._sleep = sleep
        self._limiters: dict[str, RateLimiter] = {}

    def wait(self, url: str) -> None:
        host = urlsplit(url).netloc
        limiter = self._limiters.get(host)
        if limiter is None:
            limiter = RateLimiter(self._per_second, clock=self._clock, sleep=self._sleep)
            self._limiters[host] = limiter
        limiter.wait()
