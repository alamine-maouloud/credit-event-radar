"""Versioned API prices (config/llm_pricing.yaml). Unknown models are an error, never free."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from radar.config import CONFIG_DIR, load_yaml


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_per_million_usd: float = Field(ge=0)
    output_per_million_usd: float = Field(ge=0)
    valid_from: date | None = None
    valid_until: date | None = None
    note: str | None = None


class Pricing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    source: str | None = None
    models: dict[str, ModelPrice]

    def cost(self, model_id: str, *, input_tokens: int, output_tokens: int) -> float:
        price = self.models[model_id]  # KeyError on purpose for unknown models
        return (
            input_tokens * price.input_per_million_usd
            + output_tokens * price.output_per_million_usd
        ) / 1_000_000


def load_pricing(path: Path = CONFIG_DIR / "llm_pricing.yaml") -> Pricing:
    return Pricing.model_validate(load_yaml(path))
