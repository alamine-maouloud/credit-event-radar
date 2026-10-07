"""Committee notes (Phase P3, SPEC 12): deterministic and complete without any model;
optional prose for two sections, every sentence verified against the facts it cites."""

from __future__ import annotations

from radar.notes.build import build_note
from radar.notes.model import CommitteeNote
from radar.notes.render import render_html, render_markdown

__all__ = ["CommitteeNote", "build_note", "render_html", "render_markdown"]
