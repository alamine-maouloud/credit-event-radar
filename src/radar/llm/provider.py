"""Provider abstraction (CLAUDE.md stack): one interface, two backends, one fake for tests."""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

CHARS_PER_TOKEN = 3.0  # conservative estimate for budget reservation


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: str
    user: str
    schema_name: str
    json_schema: dict[str, Any]
    model_id: str
    temperature: float = 0.0
    reasoning_effort: str | None = None
    max_output_tokens: int = Field(default=1024, ge=1)

    @property
    def estimated_input_tokens(self) -> int:
        return (
            estimate_tokens(self.system)
            + estimate_tokens(self.user)
            + estimate_tokens(str(self.json_schema))
        )


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_json: str
    model_id: str
    resolved_model: str | None = None
    provider: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    reasoning_effort: str | None = None
    # set when the API stopped before the end (max_output_tokens, content filter)
    incomplete_reason: str | None = None


class LLMProvider(ABC):
    name: ClassVar[str] = "abstract"

    @abstractmethod
    def complete(self, request: ExtractionRequest) -> ExtractionResponse:
        """Run one structured extraction and report usage. Never swallow errors."""


def refuse_placeholder_model(model_id: str) -> None:
    if not model_id or model_id.upper() == "TO_CONFIRM":
        raise ValueError(
            "model id is TO_CONFIRM: verify the provider's official catalogue and set the exact "
            "model id in config/settings.yaml before any real call"
        )


class FakeProvider(LLMProvider):
    """Deterministic stand-in for tests: returns a canned JSON string and counts calls."""

    name = "fake"

    def __init__(
        self,
        *,
        name: str = "fake",
        raw_json: str,
        resolved_model: str | None = None,
        latency_ms: int = 5,
        incomplete_reason: str | None = None,
    ) -> None:
        self.name = name
        self.raw_json = raw_json
        self.resolved_model = resolved_model
        self.latency_ms = latency_ms
        self.incomplete_reason = incomplete_reason
        self.calls = 0
        self.requests: list[ExtractionRequest] = []

    def complete(self, request: ExtractionRequest) -> ExtractionResponse:
        refuse_placeholder_model(request.model_id)
        self.calls += 1
        self.requests.append(request)
        return ExtractionResponse(
            raw_json=self.raw_json,
            model_id=request.model_id,
            resolved_model=self.resolved_model or request.model_id,
            provider=self.name,
            input_tokens=request.estimated_input_tokens,
            output_tokens=estimate_tokens(self.raw_json),
            latency_ms=self.latency_ms,
            reasoning_effort=request.reasoning_effort,
            incomplete_reason=self.incomplete_reason,
        )


def timed(fn):
    """Run fn() and return (result, elapsed_ms)."""
    t0 = time.perf_counter()
    result = fn()
    return result, int((time.perf_counter() - t0) * 1000)


class RefusingProvider(LLMProvider):
    """A provider that never calls anything: cache-only replays (zero cost). Reaching it is
    an error recorded on the document, never a silent call."""

    def __init__(self, *, name: str = "openai") -> None:
        self.name = name

    def complete(self, request: ExtractionRequest) -> ExtractionResponse:
        raise RuntimeError("cache-only run: no provider call is allowed")
