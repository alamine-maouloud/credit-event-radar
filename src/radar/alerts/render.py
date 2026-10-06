"""Renders of an Alert: a self-contained HTML page (the local channel, always on) and an
Adaptive Card for Teams (sent only when a webhook is configured). No external resource,
every value escaped, the facts are the documents' own passages."""

from __future__ import annotations

import html
from typing import Any

from radar.alerts.model import Alert, AlertFact, AlertRating, AlertRule, AlertSource

COLORS = {"P1": "#b00020", "P2": "#c2410c", "P3": "#1d4ed8", None: "#6b7280"}
CARD_COLORS = {"P1": "Attention", "P2": "Warning", "P3": "Accent", None: "Default"}
STYLE = """
body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; margin: 0;
  padding: 24px; color: #111827; background: #ffffff; max-width: 960px; }
.badge { display: inline-block; padding: 4px 12px; border-radius: 6px; color: #fff;
  font-weight: 700; background: COLOR; }
.universe { display: inline-block; margin-left: 8px; padding: 3px 10px; border-radius: 6px;
  background: #e5e7eb; color: #374151; font-size: 0.85em; }
h1 { font-size: 1.4em; margin: 12px 0 4px; }
h2 { font-size: 1.05em; margin: 24px 0 8px; border-bottom: 1px solid #e5e7eb;
  padding-bottom: 4px; }
.summary { color: #374151; }
.rule { font-weight: 700; } .prio { color: #6b7280; } .ref { color: COLOR; font-weight: 700; }
.field { font-family: monospace; background: #f3f4f6; padding: 1px 4px; border-radius: 3px; }
table { border-collapse: collapse; }
td, th { border: 1px solid #e5e7eb; padding: 4px 8px; text-align: left; font-size: 0.9em; }
pre { background: #f9fafb; padding: 12px; overflow-x: auto; font-size: 0.8em; }
small { color: #6b7280; } footer { margin-top: 32px; color: #6b7280; font-size: 0.85em; }
ul { padding-left: 20px; } li { margin-bottom: 6px; }
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _rule_item(r: AlertRule) -> str:
    return (
        f"<li><span class='rule'>{_e(r.id)}</span> <span class='prio'>[{_e(r.priority)}]</span> "
        f"{_e(r.description or '')}<br><small>{_e(r.reason)}</small></li>"
    )


def _rating_row(r: AlertRating) -> str:
    return (
        f"<tr><td>{_e(r.agency_name)}</td><td>{_e(r.rating)}</td><td>{_e(r.category)}</td>"
        f"<td>{_e(r.outlook or '')}</td><td>{_e(r.as_of)}</td>"
        f"<td>{_e(r.origin)}, {_e(r.verification)}</td></tr>"
    )


def _fact_item(f: AlertFact) -> str:
    field = f"<span class='field'>{_e(f.field)}</span> " if f.field else ""
    return (
        f"<li><span class='ref'>[{f.source}]</span> {field}<q>{_e(f.text)}</q> "
        f"<small>offsets {f.char_start}-{f.char_end}, {_e(f.extractor_version)}</small></li>"
    )


def _source_item(s: AlertSource) -> str:
    if not s.available:
        return (
            f"<li><span class='ref'>[{s.index}]</span> {_e(s.doc_id)} (document not available)</li>"
        )
    link = f" <a href='{_e(s.url)}'>{_e(s.url)}</a>" if s.url else ""
    return (
        f"<li><span class='ref'>[{s.index}]</span> {_e(s.title or s.doc_id)}{link}<br>"
        f"<small>form {_e(s.form or 'n/a')}, published {_e(s.published or 'unknown')}, "
        f"retrieved {_e(s.retrieved or 'unknown')}<br>raw sha256 {_e(s.raw_sha256)}<br>"
        f"normalised sha256 {_e(s.normalized_sha256)} ({_e(s.normalizer_version)})</small></li>"
    )


def _route_text(alert: Alert) -> str:
    if alert.route is None:
        return "none"
    every = f", every {alert.route.digest_every_hours} h" if alert.route.digest_every_hours else ""
    return f"{', '.join(alert.route.channels)} ({alert.route.mode}{every})"


def _provenance_text(alert: Alert) -> str:
    p = alert.decision_provenance
    llm = "no" if p.llm_used == "none" else p.llm_used
    return (
        f"Priority decided by: {p.priority_decided_by} (rules.yaml {p.rules_version}). "
        f"Composite rating used: {'YES' if p.composite_used else 'NO'}. "
        f"Agency ratings used: {'YES' if p.agency_ratings_used else 'NO'}. "
        f"LLM used for the decision: NO. LLM used for extraction: {llm}."
    )


def render_html(alert: Alert) -> str:
    badge = alert.priority or "NONE"
    color = COLORS.get(alert.priority, COLORS[None])
    rules = "".join(_rule_item(r) for r in alert.triggered_rules) or "<li>No rule triggered.</li>"
    modifiers = "".join(
        f"<li>{_e(m.id)} APPLIED ({_e(m.effect)}): {_e(m.reason)}</li>"
        for m in alert.modifiers
        if m.applied
    )
    ratings = "".join(_rating_row(r) for r in alert.ratings_after)
    facts = "".join(_fact_item(f) for f in alert.facts) or "<li>No verified passage recorded.</li>"
    sources = "".join(_source_item(s) for s in alert.sources)
    models = (
        "".join(
            f"<li>{_e(m.kind)}: {_e(m.model_id)} as {_e(m.role or 'n/a')}, prompt "
            f"{_e(m.prompt_version)}, schema {_e(m.schema_version)}, {m.statements_applied} "
            f"applied, {m.statements_recorded} recorded"
            + (f"<br><small>{_e(m.reason)}</small>" if m.reason else "")
            + "</li>"
            for m in alert.model_provenance
        )
        or "<li>None: deterministic fields only.</li>"
    )
    p = alert.decision_provenance
    detectors = f", {', '.join(p.detectors)}" if p.detectors else ""
    head = (
        f"<span class='badge'>{_e(badge)}</span>"
        f"<span class='universe'>{_e(alert.universe_label)}</span>"
        f"<h1>{_e(alert.issuer_name)}</h1><div>{_e(alert.title)}</div>"
        f"<div class='summary'>{_e(alert.summary)}<br><small>{_e(alert.family)}/"
        f"{_e(alert.event_type)}, effective {_e(alert.effective_date or 'unknown')}, "
        f"event {_e(alert.event_id)}</small></div>"
    )
    table_head = (
        "<tr><th>Agency</th><th>Rating</th><th>Category</th><th>Outlook</th><th>As of</th>"
        "<th>Basis</th></tr>"
    )
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_e(badge)} {_e(alert.issuer_name)}: {_e(alert.title)}</title>",
        f"<style>{STYLE.replace('COLOR', color)}</style></head><body>",
        f"<header>{head}</header>",
        f"<h2>Why this priority?</h2><ul>{rules}</ul>",
        f"<ul>{modifiers}</ul>" if modifiers else "",
        f"<table>{table_head}{ratings}</table>" if ratings else "",
        f"<p><small>{_e(_provenance_text(alert))}</small></p>",
        f"<h2>Key facts (verbatim passages)</h2><ul>{facts}</ul>",
        f"<h2>Sources</h2><ul>{sources}</ul>",
        f"<h2>Model provenance</h2><ul>{models}</ul>",
        "<h2>Audit</h2>",
        f"<p><small>Alert {_e(alert.alert_id)}, generated {_e(alert.generated_at.isoformat())}, "
        f"route {_e(_route_text(alert))}, detection {_e(p.extraction_method)}{_e(detectors)}, "
        f"enrichment {_e(p.enrichment_method or 'none')}.</small></p>",
        f"<pre>{_e(alert.explanation)}</pre>",
        f"<footer>{_e(alert.disclaimer)}</footer>",
        "</body></html>",
    ]
    return "\n".join(part for part in parts if part) + "\n"


def _text(text: str, **extra: Any) -> dict[str, Any]:
    return {"type": "TextBlock", "text": text, "wrap": True, **extra}


def render_card(alert: Alert, viewer_base_url: str = "http://localhost:8501") -> dict[str, Any]:
    """An Adaptive Card 1.4 for Teams (Workflows app): badge, issuer, title, three sourced
    facts, the triggered rules, the provenance, links to the sources and to the notes."""
    badge = alert.priority or "NONE"
    header = {
        "type": "ColumnSet",
        "columns": [
            {
                "type": "Column",
                "width": "auto",
                "items": [
                    _text(
                        badge,
                        size="Large",
                        weight="Bolder",
                        color=CARD_COLORS.get(alert.priority, "Default"),
                    )
                ],
            },
            {
                "type": "Column",
                "width": "stretch",
                "items": [
                    _text(alert.issuer_name, size="Medium", weight="Bolder"),
                    _text(alert.universe_label, isSubtle=True, spacing="None"),
                ],
            },
        ],
    }
    body: list[dict[str, Any]] = [
        header,
        _text(alert.title, weight="Bolder"),
        _text(alert.summary, isSubtle=True),
        {
            "type": "FactSet",
            "facts": [
                {
                    "title": f"[{f.source}]",
                    "value": f.text if len(f.text) <= 300 else f.text[:297] + "...",
                }
                for f in alert.key_facts
            ],
        },
        _text("Why this priority?", weight="Bolder", spacing="Medium"),
    ]
    for r in alert.triggered_rules:
        body.append(_text(f"✓ {r.id} {r.description or ''}: {r.reason}"))
    if not alert.triggered_rules:
        body.append(_text("No rule triggered."))
    body.append(_text(_provenance_text(alert), isSubtle=True, size="Small"))
    body.append(_text(alert.disclaimer, isSubtle=True, size="Small"))
    base = viewer_base_url.rstrip("/")
    actions: list[dict[str, Any]] = [
        {"type": "Action.OpenUrl", "title": f"Source [{s.index}]", "url": s.url}
        for s in alert.sources
        if s.url
    ]
    for lang in ("fr", "en"):
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": f"Note {lang.upper()}",
                "url": f"{base}/?event={alert.event_id}&lang={lang}",
            }
        )
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
        "actions": actions,
    }


def render_teams_message(
    alert: Alert, viewer_base_url: str = "http://localhost:8501"
) -> dict[str, Any]:
    """The payload of the Teams Workflows webhook: one adaptive card attachment."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": render_card(alert, viewer_base_url),
            }
        ],
    }
