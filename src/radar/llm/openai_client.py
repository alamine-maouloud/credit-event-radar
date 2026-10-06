"""OpenAI backend: Responses API with strict Structured Outputs and reasoning effort.

The payload builder is pure and tested; the SDK call is the only line that touches the
network. The id actually used and the model name the API resolves to are both recorded
(``model_id`` and ``resolved_model``), so a benchmark never silently drifts to an alias.
"""

from __future__ import annotations

import os
from typing import Any

from radar.llm.provider import (
    ExtractionRequest,
    ExtractionResponse,
    LLMProvider,
    refuse_placeholder_model,
    timed,
)

API_KEY_ENV = "OPENAI_API_KEY"
# Reasoning models of the Responses API reject the temperature parameter outright (HTTP 400
# "Unsupported parameter: 'temperature'", observed on gpt-5.6-terra on 2026-10-06). For them
# the key is left out of the payload entirely; determinism rests on the cache and the
# recorded resolved model, not on a sampling parameter the API does not expose.
NO_TEMPERATURE_MODEL_PREFIXES = ("gpt-5",)


def supports_temperature(model_id: str) -> bool:
    return not model_id.startswith(NO_TEMPERATURE_MODEL_PREFIXES)


def build_payload(request: ExtractionRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model_id,
        "input": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": request.schema_name,
                "schema": request.json_schema,
                "strict": True,
            }
        },
        "max_output_tokens": request.max_output_tokens,
    }
    if supports_temperature(request.model_id):
        payload["temperature"] = request.temperature
    if request.reasoning_effort:
        payload["reasoning"] = {"effort": request.reasoning_effort}
    return payload


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def _sdk(self) -> Any:
        if self._client is None:
            if not os.environ.get(API_KEY_ENV):
                raise RuntimeError(f"{API_KEY_ENV} is not set")
            from openai import OpenAI

            self._client = OpenAI()
        return self._client

    def complete(self, request: ExtractionRequest) -> ExtractionResponse:
        refuse_placeholder_model(request.model_id)
        payload = build_payload(request)
        client = self._sdk()
        response, elapsed = timed(lambda: client.responses.create(**payload))
        usage = getattr(response, "usage", None)
        incomplete = None
        if getattr(response, "status", None) == "incomplete":
            details = getattr(response, "incomplete_details", None)
            incomplete = getattr(details, "reason", None) or "incomplete"
        return ExtractionResponse(
            raw_json=getattr(response, "output_text", "") or "",
            model_id=request.model_id,
            resolved_model=getattr(response, "model", None),
            provider=self.name,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            latency_ms=elapsed,
            reasoning_effort=request.reasoning_effort,
            incomplete_reason=incomplete,
        )
