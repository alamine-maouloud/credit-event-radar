"""Static HTML export (the portable fallback of the viewer): an index with the two
universes, the alert list and the routing, and one page per decided event with a priority,
rendered by the alert renderer. No script, no external resource."""

from __future__ import annotations

import html
from pathlib import Path

from radar.alerts.render import COLORS, render_html
from radar.config import CONFIG_DIR, Rules, Settings, Universe, load_rating_scales
from radar.db import Database
from radar.viewer.data import (
    alert_rows,
    event_alert,
    routing_rows,
    split_watchlist,
    summary_counts,
    watchlist_rows,
)

STYLE = """
body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; margin: 0;
  padding: 24px; color: #111827; background: #ffffff; max-width: 1100px; }
h1 { font-size: 1.6em; margin: 0 0 4px; } h2 { font-size: 1.1em; margin: 28px 0 8px; }
.tag { color: #6b7280; } table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid #e5e7eb; padding: 5px 8px; text-align: left; font-size: 0.9em;
  vertical-align: top; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 5px; color: #fff;
  font-weight: 700; font-size: 0.85em; }
.note { background: #f9fafb; border-left: 3px solid #e5e7eb; padding: 8px 12px;
  color: #374151; font-size: 0.9em; }
footer { margin-top: 32px; color: #6b7280; font-size: 0.85em; }
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _badge(priority: str | None) -> str:
    color = COLORS.get(priority, COLORS[None])
    return f"<span class='badge' style='background:{color}'>{_e(priority or 'NONE')}</span>"


def _watchlist_table(rows: list[dict]) -> str:
    head = (
        "<tr><th>Issuer</th><th>Sector</th><th>Country</th><th>Composite (seed)</th>"
        "<th>Events</th><th>Last event</th><th>Highest priority</th></tr>"
    )
    body = "".join(
        f"<tr><td>{_e(r['name'])}</td><td>{_e(r['sector'])}</td><td>{_e(r['country'])}</td>"
        f"<td>{_e(r['composite'])}</td><td>{r['events']}</td><td>{_e(r['last_event'])}</td>"
        f"<td>{_badge(r['highest_priority']) if r['highest_priority'] else ''}</td></tr>"
        for r in rows
    )
    return f"<table>{head}{body}</table>" if rows else "<p class='tag'>No issuer.</p>"


def export_site(
    db: Database, universe: Universe, rules: Rules, settings: Settings, out_dir: Path
) -> list[Path]:
    scales = load_rating_scales(CONFIG_DIR / "rating_scales.yaml")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "alerts").mkdir(exist_ok=True)
    written: list[Path] = []
    groups = split_watchlist(watchlist_rows(db, universe, scales, settings.demo.featured_issuers))
    alerts = alert_rows(db, universe)
    counts = summary_counts(alerts)
    alert_html = []
    for r in alerts:
        link = ""
        if r["priority"]:
            alert, _ = event_alert(db, r["event_id"], universe, rules, settings.alerts)
            page = out_dir / "alerts" / f"{r['event_id']}.html"
            page.write_text(render_html(alert), encoding="utf-8")
            written.append(page)
            link = f' <a href="alerts/{_e(r["event_id"])}.html">Why this priority?</a>'
        alert_html.append(
            f"<tr><td>{_badge(r['priority'])}</td><td>{_e(r['issuer_name'])}<br>"
            f"<span class='tag'>{_e(r['universe_label'])}</span></td>"
            f"<td>{_e(r['title'])}{link}</td><td>{_e(r['effective_date'])}</td>"
            f"<td>{_e(r['rules'])}</td><td>{_e(r['enrichment'])}</td></tr>"
        )
    routing = "".join(
        f"<tr><td>{_e(r['family'])}</td><td>{_e(r['default'])}</td><td>{_e(r['challenger'])}</td>"
        f"<td>{_e(r['reason'])}</td></tr>"
        for r in routing_rows(settings)
    )
    live, hist, other = groups["live"], groups["historical"], groups["other"]
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Credit Event Radar</title>",
        f"<style>{STYLE}</style></head><body>",
        "<h1>Credit Event Radar</h1>",
        "<p class='tag'>Monitors public information for credit-relevant events, prioritises "
        "them with deterministic rules, explains and sources every alert. "
        "No investment recommendation.</p>",
        f"<p>P1 {counts['P1']} · P2 {counts['P2']} · P3 {counts['P3']} · no priority "
        f"{counts['NONE']}</p>",
        "<h2>Live watchlist</h2>",
        _watchlist_table(live),
        f"<p class='tag'>Universe, not yet ingested: {len(groups['not_ingested'])} issuers "
        "(kept in the universe, no document yet).</p>",
        "<h2>Featured demo cases</h2>",
        "<p class='note'>Public filings of issuers outside the demo watchlist, chosen to show "
        "one behaviour each (fallen angel, going concern, covenant breach, denied doubt). "
        "They are not positions of any portfolio.</p>",
        _watchlist_table(groups["featured"]),
        "<h2>Historical stress cases</h2>",
        "<p class='note'>The other control filings, with their real result as decided; "
        "nothing is altered for the presentation.</p>",
        _watchlist_table(hist),
        ("<h2>Other issuers with events</h2>" + _watchlist_table(other)) if other else "",
        "<h2>Alerts and events</h2>",
        "<table><tr><th>Priority</th><th>Issuer</th><th>Event</th><th>Effective</th>"
        "<th>Rules</th><th>Enrichment</th></tr>" + "".join(alert_html) + "</table>",
        "<h2>Model routing (benchmarked per family)</h2>",
        "<table><tr><th>Family</th><th>Default</th><th>Challenger</th><th>Why</th></tr>"
        + routing
        + "</table>",
        "<footer>Automatically generated from public sources, to be validated by the "
        "analyst. No investment recommendation. Demo universe constructed exclusively from "
        "publicly disclosed information.</footer>",
        "</body></html>",
    ]
    index = out_dir / "index.html"
    index.write_text("\n".join(p for p in parts if p) + "\n", encoding="utf-8")
    written.insert(0, index)
    return written
