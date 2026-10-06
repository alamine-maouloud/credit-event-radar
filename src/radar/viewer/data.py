"""Data layer of the viewer: plain functions over the database, no Streamlit, so the
screens and the static export share one reading of the stored decisions."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from radar.alerts.model import UNIVERSE_LABELS, Alert, _title, build_alert, universe_of
from radar.config import AlertsSettings, Rules, Settings, Universe
from radar.db import Database
from radar.models import PRIORITY_ORDER, RawDocument
from radar.ratings import RatingScales, composite_rating

UNIVERSE_ORDER = {"live_watchlist": 0, "historical_stress_case": 1, "other": 2}


def _priority_rank(priority: str | None) -> int:
    return PRIORITY_ORDER.get(priority or "", -1)


def watchlist_rows(db: Database, universe: Universe, scales: RatingScales) -> list[dict[str, Any]]:
    """One row per issuer: universe, composite from the hand-verified seed (analytical
    metadata only), event count, last event, highest priority. Issuers tagged neither live
    nor historical appear only when they carry events."""
    events = defaultdict(list)
    for event in db.list_events():
        events[event.issuer_id].append(event)
    rows = []
    for issuer in universe.issuers:
        kind = universe_of(issuer)
        own = events.get(issuer.id, [])
        if kind == "other" and not own:
            continue
        # the official composite needs GOLDEN seed rows (ADR-001); the structural one,
        # computed from any sourced row, is shown with its basis and never feeds a rule
        seed = db.ratings_for(issuer.id)
        try:
            golden = composite_rating(seed, scales)
            composite = golden or composite_rating(seed, scales, min_verification="SOURCE_VERIFIED")
        except ValueError:
            golden = composite = None
        priorities = [db.priority_of(e.event_id) for e in own]
        best = max((p[0] for p in priorities if p and p[0]), key=_priority_rank, default=None)
        dates = [e.effective_date for e in own if e.effective_date]
        rows.append(
            {
                "issuer_id": issuer.id,
                "name": issuer.name,
                "universe": kind,
                "universe_label": UNIVERSE_LABELS[kind],
                "sector": issuer.sector,
                "country": issuer.country,
                "composite": composite.label if composite else "n/a",
                "composite_category": composite.category if composite else "",
                "composite_basis": (
                    "golden seed" if golden else ("seed, not signed off" if composite else "")
                ),
                "rating_status": issuer.rating_status,
                "events": len(own),
                "last_event": max(dates).isoformat() if dates else "",
                "highest_priority": best,
            }
        )
    rows.sort(key=lambda r: (UNIVERSE_ORDER[r["universe"]], r["name"]))
    return rows


def alert_rows(db: Database, universe: Universe) -> list[dict[str, Any]]:
    """Every decided event, the priority first, with the title the alert would carry."""
    rows = []
    for event in db.list_events():
        decision = db.get_decision(event.event_id)
        if decision is None:
            continue
        try:
            issuer = universe.by_id(event.issuer_id)
        except KeyError:
            issuer = None
        kind = universe_of(issuer)
        rows.append(
            {
                "event_id": event.event_id,
                "priority": decision.final_priority,
                "decision_status": decision.decision_status,
                "issuer_id": event.issuer_id,
                "issuer_name": issuer.name if issuer else event.issuer_id,
                "universe": kind,
                "universe_label": UNIVERSE_LABELS[kind],
                "family": event.family,
                "event_type": event.event_type,
                "effective_date": event.effective_date.isoformat() if event.effective_date else "",
                "title": _title(event),
                "rules": ", ".join(r.id for r in decision.rules if r.triggered),
                "enrichment": event.enrichment_method or "none",
            }
        )
    # P1 first, then the most recent within a priority
    rows.sort(
        key=lambda r: (_priority_rank(r["priority"]), r["effective_date"] or ""), reverse=True
    )
    return rows


def event_alert(
    db: Database, event_id: str, universe: Universe, rules: Rules, alerts: AlertsSettings
) -> tuple[Alert, dict[str, RawDocument]]:
    event = db.get_event(event_id)
    if event is None:
        raise KeyError(event_id)
    decision = db.get_decision(event_id)
    if decision is None:
        raise KeyError(f"no decision for {event_id}")
    documents = {d: doc for d in event.source_doc_ids if (doc := db.get_document(d))}
    try:
        issuer = universe.by_id(event.issuer_id)
    except KeyError:
        issuer = None
    return build_alert(event, decision, documents, issuer, rules=rules, alerts=alerts), documents


def passage_context(
    doc: RawDocument, start: int, end: int, window: int = 400
) -> tuple[str, str, str]:
    """The passage with its surroundings, for the highlighted view of the source."""
    text = doc.text
    return text[max(0, start - window) : start], text[start:end], text[end : end + window]


def audit_rows(db: Database, event_id: str) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": e["timestamp"],
            "step": e["step"],
            "status": e["status"],
            "message": e["message"],
            "model_id": e.get("model_id"),
            "rules_version": e.get("rules_version"),
        }
        for e in db.audit_entries(event_id=event_id)
    ]


def routing_rows(settings: Settings) -> list[dict[str, Any]]:
    """The routed model per family with its benchmark reason (ADR-020)."""
    llm = settings.llm
    rows = []
    for family, route in llm.routing.items():

        def model_of(name: str | None) -> str:
            if not name or name == "TO_BENCHMARK":
                return "none" if not name else name
            return llm.benchmark_alternatives[name]["extraction"].model

        rows.append(
            {
                "family": family,
                "default": model_of(route.default),
                "challenger": model_of(route.challenger),
                "reason": route.reason,
            }
        )
    return rows


def summary_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"P1": 0, "P2": 0, "P3": 0, "NONE": 0}
    for r in rows:
        counts[r["priority"] or "NONE"] += 1
    return counts
