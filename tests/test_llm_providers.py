"""Provider request builders and simulated SDK responses. No network, no API key needed."""

from __future__ import annotations

import pytest

from radar.llm.provider import ExtractionRequest, LLMProvider, estimate_tokens


def request(**overrides) -> ExtractionRequest:
    base = dict(
        system="SYS",
        user="USER",
        schema_name="GuidanceExtraction",
        json_schema={"type": "object", "additionalProperties": False, "properties": {}},
        model_id="gpt-5.6-terra",
        temperature=0.0,
        reasoning_effort="low",
        max_output_tokens=800,
    )
    base.update(overrides)
    return ExtractionRequest(**base)


def test_token_estimate_is_conservative():
    assert estimate_tokens("a" * 400) >= 100
    assert estimate_tokens("") == 0


# ----------------------------------------------------------------- openai --- #


def test_openai_request_payload_uses_structured_outputs_and_effort():
    from radar.llm.openai_client import build_payload

    payload = build_payload(request())
    assert payload["model"] == "gpt-5.6-terra"
    assert payload["reasoning"] == {"effort": "low"}
    fmt = payload["text"]["format"]
    assert (
        fmt["type"] == "json_schema"
        and fmt["strict"] is True
        and fmt["name"] == "GuidanceExtraction"
    )
    assert fmt["schema"]["additionalProperties"] is False
    assert "temperature" not in payload and payload["max_output_tokens"] == 800
    # a model outside the reasoning family still receives the sampling parameter
    assert build_payload(request(model_id="gpt-4.1-test"))["temperature"] == 0.0
    roles = [m["role"] for m in payload["input"]]
    assert roles == ["system", "user"]


def test_openai_provider_records_resolved_model_and_usage():
    from radar.llm.openai_client import OpenAIProvider

    class FakeResponses:
        def create(self, **payload):
            class Usage:
                input_tokens = 1234
                output_tokens = 56

            class Response:
                model = "gpt-5.6-terra-2026-09-01"
                output_text = '{"has_guidance": false, "statements": []}'
                usage = Usage()

            return Response()

    class FakeClient:
        responses = FakeResponses()

    provider = OpenAIProvider(client=FakeClient())
    response = provider.complete(request())
    assert response.provider == "openai" and response.model_id == "gpt-5.6-terra"
    assert response.resolved_model == "gpt-5.6-terra-2026-09-01"
    assert (response.input_tokens, response.output_tokens) == (1234, 56)
    assert response.raw_json.startswith("{") and response.reasoning_effort == "low"


# -------------------------------------------------------------- anthropic --- #


def test_anthropic_request_payload_forces_a_tool_with_the_schema():
    from radar.llm.anthropic_client import build_payload

    payload = build_payload(request(model_id="claude-test"))
    assert payload["model"] == "claude-test" and payload["temperature"] == 0.0
    assert payload["system"] == "SYS" and payload["messages"][0]["role"] == "user"
    tool = payload["tools"][0]
    assert (
        tool["name"] == "GuidanceExtraction"
        and tool["input_schema"]["additionalProperties"] is False
    )
    assert payload["tool_choice"] == {"type": "tool", "name": "GuidanceExtraction"}
    assert payload["max_tokens"] == 800


def test_anthropic_provider_reads_tool_input_and_usage():
    from radar.llm.anthropic_client import AnthropicProvider

    class FakeMessages:
        def create(self, **payload):
            class Block:
                type = "tool_use"
                name = "GuidanceExtraction"
                input = {"has_guidance": False, "statements": []}

            class Usage:
                input_tokens = 900
                output_tokens = 30

            class Message:
                model = "claude-test-20260101"
                content = [Block()]
                usage = Usage()

            return Message()

    class FakeClient:
        messages = FakeMessages()

    provider = AnthropicProvider(client=FakeClient())
    response = provider.complete(request(model_id="claude-test"))
    assert response.provider == "anthropic" and response.resolved_model == "claude-test-20260101"
    assert response.raw_json == '{"has_guidance": false, "statements": []}'
    assert (response.input_tokens, response.output_tokens) == (900, 30)


def test_to_confirm_model_is_refused_before_any_call():
    from radar.llm.anthropic_client import AnthropicProvider

    provider = AnthropicProvider(client=object())
    with pytest.raises(ValueError, match="TO_CONFIRM"):
        provider.complete(request(model_id="TO_CONFIRM"))


def test_base_class_is_abstract():
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]


@pytest.mark.parametrize("model_id", ["gpt-5.6-terra", "gpt-5.6-sol"])
def test_openai_reasoning_models_get_no_temperature_field(model_id):
    """The Responses API rejects temperature for these models (400 on the first real run,
    2026-10-06): the key must be absent, not null, while effort and strict outputs stay."""
    from radar.llm.openai_client import build_payload

    payload = build_payload(request(model_id=model_id, temperature=0.0, reasoning_effort="low"))
    assert "temperature" not in payload
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["text"]["format"]["strict"] is True
    assert payload["model"] == model_id
