"""Versioned prompt library (docs/SPEC.md 13.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from radar.llm.prompts import PROMPTS_DIR, load_prompt, render_prompt

GUIDANCE = PROMPTS_DIR / "extraction" / "guidance.v1.yaml"


def test_guidance_prompt_metadata():
    prompt = load_prompt(GUIDANCE)
    assert (
        prompt.id == "extraction.guidance" and prompt.version == "1.1.0" and prompt.language == "en"
    )
    assert prompt.model_role == "extraction" and prompt.temperature == 0
    assert prompt.output_schema == "GuidanceExtraction"
    assert set(prompt.input_variables) == {"issuer_name", "document_date", "source_text"}
    assert prompt.changelog and prompt.changelog[0].startswith("1.1.0")
    assert len(prompt.content_hash) == 64


def test_prompt_forbids_priority_and_demands_quotes():
    prompt = load_prompt(GUIDANCE)
    low = (prompt.system + prompt.user).lower()
    assert "do not" in low and "priority" in low and "quote" in low
    assert "instructions" in low  # the document is data, never instructions


def test_render_is_strict_about_variables():
    prompt = load_prompt(GUIDANCE)
    system, user = render_prompt(
        prompt,
        {"issuer_name": "Issuer Test A", "document_date": "2026-04-30", "source_text": "TEXT"},
    )
    assert "Issuer Test A" in user and "TEXT" in user and "2026-04-30" in user
    with pytest.raises(KeyError):
        render_prompt(prompt, {"issuer_name": "x"})
    with pytest.raises(KeyError):
        render_prompt(
            prompt, {"issuer_name": "x", "document_date": "d", "source_text": "t", "extra": 1}
        )


def test_hash_is_stable_and_version_bump_required_on_change(tmp_path: Path):
    prompt = load_prompt(GUIDANCE)
    copy = tmp_path / "guidance.v1.yaml"
    copy.write_text(
        GUIDANCE.read_text(encoding="utf-8").replace("Extract", "EXTRACT"), encoding="utf-8"
    )
    assert load_prompt(copy).content_hash != prompt.content_hash
