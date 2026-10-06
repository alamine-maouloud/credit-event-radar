"""Alerts (Phase P1): the Alert object and its local renders. The local channel is always
on (SPEC 11.4); Teams and e-mail are optional and explicit."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from radar.alerts.model import Alert, build_alert
from radar.alerts.render import render_card, render_html

__all__ = ["Alert", "build_alert", "write_alert"]


def write_alert(
    alert: Alert,
    out_dir: Path,
    *,
    day: str | None = None,
    viewer_base_url: str = "http://localhost:8501",
) -> list[Path]:
    """outputs/alerts/<date>/<event_id>.json, .html and .card.json (SPEC 11.4)."""
    folder = out_dir / (day or datetime.now(UTC).date().isoformat())
    folder.mkdir(parents=True, exist_ok=True)
    paths = [
        folder / f"{alert.event_id}.json",
        folder / f"{alert.event_id}.html",
        folder / f"{alert.event_id}.card.json",
    ]
    paths[0].write_text(
        json.dumps(alert.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    paths[1].write_text(render_html(alert), encoding="utf-8")
    paths[2].write_text(
        json.dumps(render_card(alert, viewer_base_url), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return paths
