"""Strict output schemas for LLM extraction (docs/SPEC.md section 7.2 and 13).

The model reads and quotes; it never computes. Hence bounds and units are raw fields and
every derived figure (midpoint, delta, direction) is computed in :mod:`radar.llm.compute`.
No self-declared confidence field exists anywhere (CLAUDE.md rule 4).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GUIDANCE_SCHEMA_VERSION = "guidance-1.0"

Metric = Literal["revenue", "ebitda", "ebit", "fcf", "margin", "capex", "other"]
Basis = Literal["absolute", "yoy_change_pct", "margin_pct"]
Unit = Literal["EUR_BN", "EUR_MN", "USD_BN", "USD_MN", "PCT"]
GuidanceStatus = Literal["raised", "cut", "reaffirmed", "new", "withdrawn", "mentioned"]
Direction = Literal["up", "down", "unchanged"]


class GuidanceStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Metric
    metric_label: str = Field(description="The metric exactly as the text names it")
    basis: Basis
    unit: Unit
    previous_lower: float | None = None
    previous_upper: float | None = None
    current_lower: float | None = None
    current_upper: float | None = None
    period: str | None = Field(default=None, description="Fiscal period as written, e.g. 2026")
    status: GuidanceStatus
    direction_claimed: Direction | None = Field(
        default=None, description="Direction as stated by the text, never decisional"
    )
    evidence_quote: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)

    @model_validator(mode="after")
    def _offsets(self) -> GuidanceStatement:
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        return self


class GuidanceExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_guidance: bool
    statements: list[GuidanceStatement] = Field(default_factory=list)


def _close(schema: dict[str, Any]) -> None:
    """OpenAI strict mode needs every object closed and every property required."""
    if schema.get("type") == "object" or "properties" in schema:
        schema["additionalProperties"] = False
        if "properties" in schema:
            schema["required"] = list(schema["properties"].keys())
    for key in ("properties", "$defs"):
        for value in schema.get(key, {}).values():
            if isinstance(value, dict):
                _close(value)
    for key in ("items", "anyOf", "oneOf", "allOf"):
        value = schema.get(key)
        if isinstance(value, dict):
            _close(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _close(item)


def guidance_json_schema() -> dict[str, Any]:
    schema = GuidanceExtraction.model_json_schema()
    _close(schema)
    return schema
