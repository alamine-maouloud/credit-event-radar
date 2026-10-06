"""Anthropic backend: Messages API with a forced tool call carrying the JSON schema.

The model id stays TO_CONFIRM in config/settings.yaml until the official catalogue is
checked on the day of the first real benchmark; the provider refuses the placeholder.
"""

from __future__ import annotations

import json
import os
from typing import Any

from radar.llm.provider import (
    ExtractionRequest,
    ExtractionResponse,
    LLMProvider,
    refuse_placeholder_model,
    timed,
)

API_KEY_ENV = "ANTHROPIC_API_KEY"


def build_payload(request: ExtractionRequest) -> dict[str, Any]:
    return {
        "model": request.model_id,
        "max_tokens": request.max_output_tokens,
        "temperature": request.temperature,
        "system": request.system,
        "messages": [{"role": "user", "content": request.user}],
        "tools": [
            {
                "name": request.schema_name,
                "description": f"Return the {request.schema_name} JSON object and nothing else.",
                "input_schema": request.json_schema,
            }
        ],
        "tool_choice": {"type": "tool", "name": request.schema_name},
    }


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def _sdk(self) -> Any:
        if self._client is None:
            if not os.environ.get(API_KEY_ENV):
                raise RuntimeError(f"{API_KEY_ENV} is not set")
            from anthropic import Anthropic

            self._client = Anthropic()
        return self._client

    def complete(self, request: ExtractionRequest) -> ExtractionResponse:
        refuse_placeholder_model(request.model_id)
        payload = build_payload(request)
        client = self._sdk()
        message, elapsed = timed(lambda: client.messages.create(**payload))
        raw = ""
        for block in getattr(message, "content", []) or []:
            if (
                getattr(block, "type", "") == "tool_use"
                and getattr(block, "name", "") == request.schema_name
            ):
                raw = json.dumps(block.input, ensure_ascii=False)
                break
        usage = getattr(message, "usage", None)
        return ExtractionResponse(
            raw_json=raw,
            model_id=request.model_id,
            resolved_model=getattr(message, "model", None),
            provider=self.name,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            latency_ms=elapsed,
            reasoning_effort=request.reasoning_effort,
        )
