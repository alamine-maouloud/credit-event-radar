"""Versioned prompt library (docs/SPEC.md 13.2). Variables use ${name} placeholders."""

from __future__ import annotations

import hashlib
from pathlib import Path
from string import Template

from pydantic import BaseModel, ConfigDict, Field

from radar.config import ROOT, load_yaml

PROMPTS_DIR = ROOT / "prompts"


class Prompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    language: str
    purpose: str
    model_role: str
    temperature: float
    input_variables: list[str]
    output_schema: str
    system: str
    user: str
    changelog: list[str]
    content_hash: str


def load_prompt(path: Path) -> Prompt:
    data = load_yaml(path)
    data["content_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return Prompt.model_validate(data)


def render_prompt(prompt: Prompt, variables: dict[str, object]) -> tuple[str, str]:
    expected = set(prompt.input_variables)
    given = set(variables)
    if given != expected:
        raise KeyError(f"prompt {prompt.id} expects {sorted(expected)}, got {sorted(given)}")
    values = {k: str(v) for k, v in variables.items()}
    return Template(prompt.system).substitute(values), Template(prompt.user).substitute(values)
