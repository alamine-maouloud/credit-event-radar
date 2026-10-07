"""Credit Event Radar viewer (Streamlit, Phase P2): four screens over the stored decisions.
Dashboard, Watchlist (the live watchlist strictly apart from the historical stress cases),
Alerts, Event detail with "Why this priority?", the evidence and the audit trail. Reads
the database named by RADAR_DB or settings.paths.db; never writes."""

from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from radar.alerts.render import COLORS, render_card, render_html  # noqa: E402
from radar.config import (  # noqa: E402
    CONFIG_DIR,
    load_rating_scales,
    load_rules,
    load_settings,
    load_universe,
)
from radar.db import Database  # noqa: E402
from radar.viewer.data import (  # noqa: E402
    alert_rows,
    audit_rows,
    event_alert,
    passage_context,
    routing_rows,
    split_watchlist,
    summary_counts,
    watchlist_rows,
)

SCREENS = ["Dashboard", "Watchlist", "Alerts", "Event detail"]
DISCLAIMER = (
    "Automatically generated from public sources, to be validated by the analyst. "
    "No investment recommendation."
)


@st.cache_resource
def _config():
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    return (
        load_universe(CONFIG_DIR / "universe.yaml"),
        load_rules(CONFIG_DIR / "rules.yaml"),
        load_rating_scales(CONFIG_DIR / "rating_scales.yaml"),
        settings,
    )


def _badge(priority: str | None) -> str:
    color = COLORS.get(priority, COLORS[None])
    return (
        f"<span style='display:inline-block;padding:4px 12px;border-radius:6px;color:#fff;"
        f"font-weight:700;background:{color}'>{html.escape(priority or 'NONE')}</span>"
    )


def _dashboard(db: Database, universe, scales, settings) -> None:
    st.title("Credit Event Radar")
    st.markdown(
        "Monitors public information for credit-relevant events, prioritises them with "
        "deterministic rules (P1, P2, P3), explains and sources every alert. "
        "The LLM reads and quotes; the rules decide."
    )
    alerts = alert_rows(db, universe)
    counts = summary_counts(alerts)
    groups = split_watchlist(watchlist_rows(db, universe, scales, settings.demo.featured_issuers))
    cols = st.columns(5)
    cols[0].metric("P1", counts["P1"])
    cols[1].metric("P2", counts["P2"])
    cols[2].metric("P3", counts["P3"])
    cols[3].metric("Live watchlist", len(groups["live"]))
    cols[4].metric("Historical stress cases", len(groups["featured"]) + len(groups["historical"]))
    st.markdown("#### Featured demo cases")
    featured_ids = set(settings.demo.featured_issuers)
    st.dataframe(
        [
            {
                k: r[k]
                for k in (
                    "priority",
                    "issuer_name",
                    "universe_label",
                    "title",
                    "effective_date",
                    "rules",
                )
            }
            for r in alerts
            if r["issuer_id"] in featured_ids and r["priority"]
        ],
        width="stretch",
        hide_index=True,
    )
    st.markdown("#### Latest alerts")
    st.dataframe(
        [
            {
                k: r[k]
                for k in (
                    "priority",
                    "issuer_name",
                    "universe_label",
                    "title",
                    "effective_date",
                    "rules",
                )
            }
            for r in alerts[:10]
        ],
        width="stretch",
        hide_index=True,
    )
    st.markdown(
        "#### Historical stress cases\nPublic filings of issuers outside the demo watchlist, "
        "used as controls for the detectors. They are not positions of any portfolio; the "
        "featured cases are a presentation choice, every result is shown as decided."
    )
    st.markdown("#### Model routing, one benchmark per family")
    st.dataframe(routing_rows(settings), width="stretch", hide_index=True)
    st.caption(DISCLAIMER)


def _watchlist(db: Database, universe, scales, settings) -> None:
    st.title("Watchlist")
    groups = split_watchlist(watchlist_rows(db, universe, scales, settings.demo.featured_issuers))
    columns = (
        "name", "sector", "country", "composite", "documents", "events", "last_event",
        "highest_priority",
    )  # fmt: skip

    def table(rows):
        st.dataframe([{k: r[k] for k in columns} for r in rows], width="stretch", hide_index=True)

    st.markdown("### Live watchlist")
    table(groups["live"])
    with st.expander(f"Universe, not yet ingested ({len(groups['not_ingested'])} issuers)"):
        table(groups["not_ingested"])
    st.markdown("### Featured demo cases")
    st.caption(
        "Public filings of issuers outside the demo watchlist, chosen to show one behaviour "
        "each (fallen angel, going concern, covenant breach, denied doubt). "
        "They are not positions of any portfolio."
    )
    table(groups["featured"])
    st.markdown("### Historical stress cases")
    st.caption(
        "The other control filings, with their real result as decided; nothing is altered "
        "for the presentation."
    )
    table(groups["historical"])
    if groups["other"]:
        st.markdown("### Other issuers with events")
        table(groups["other"])
    st.caption(
        "Composite: hand-verified seed ratings, analytical metadata only, never a rule input."
    )


def _alerts(db: Database, universe) -> None:
    st.title("Alerts and events")
    rows = alert_rows(db, universe)
    c1, c2, c3 = st.columns(3)
    priorities = c1.multiselect("Priority", ["P1", "P2", "P3", "NONE"], default=["P1", "P2", "P3"])
    universes = c2.multiselect(
        "Universe",
        ["live_watchlist", "historical_stress_case", "other"],
        default=["live_watchlist", "historical_stress_case"],
    )
    families = c3.multiselect("Family", ["rating", "earnings", "issuance", "other"], default=[])
    shown = [
        r
        for r in rows
        if (r["priority"] or "NONE") in priorities
        and r["universe"] in universes
        and (not families or r["family"] in families)
    ]
    st.dataframe(
        [
            {
                k: r[k]
                for k in (
                    "priority",
                    "issuer_name",
                    "universe_label",
                    "title",
                    "effective_date",
                    "rules",
                    "enrichment",
                )
            }
            for r in shown
        ],
        width="stretch",
        hide_index=True,
    )
    st.caption("Open an event in the Event detail screen to read why it has its priority.")


def _event_detail(db: Database, universe, rules, settings) -> None:
    rows = alert_rows(db, universe)
    if not rows:
        st.info("No decided event in this database.")
        return
    wanted = st.query_params.get("event")
    ids = [r["event_id"] for r in rows]
    labels = {
        r["event_id"]: f"{r['priority'] or 'NONE'} · {r['issuer_name']} · {r['title']}"
        for r in rows
    }
    index = ids.index(wanted) if wanted in ids else 0
    event_id = st.selectbox("Event", ids, index=index, format_func=lambda i: labels[i])
    alert, documents = event_alert(db, event_id, universe, rules, settings.alerts)
    st.markdown(
        f"{_badge(alert.priority)} <span style='color:#6b7280'>{html.escape(alert.universe_label)}"
        f"</span>",
        unsafe_allow_html=True,
    )
    st.markdown(f"## {alert.issuer_name}")
    st.markdown(f"**{alert.title}**  \n{alert.summary}")
    st.markdown("### Why this priority?")
    for r in alert.triggered_rules:
        st.markdown(f"✓ **{r.id}** [{r.priority}] {r.description or ''}  \n{r.reason}")
    if not alert.triggered_rules:
        st.markdown("No rule triggered.")
    for m in alert.modifiers:
        if m.applied:
            st.markdown(f"✓ **{m.id}** applied ({m.effect}): {m.reason}")
    if alert.ratings_after:
        st.dataframe(
            [
                {
                    k: getattr(r, k)
                    for k in (
                        "agency_name",
                        "rating",
                        "category",
                        "outlook",
                        "as_of",
                        "origin",
                        "verification",
                    )
                }
                for r in alert.ratings_after
            ],
            width="stretch",
            hide_index=True,
        )
    p = alert.decision_provenance
    st.markdown(
        f"Priority decided by: **{p.priority_decided_by}** (rules.yaml {p.rules_version}). "
        f"Composite rating used: **{'YES' if p.composite_used else 'NO'}**. "
        f"Agency ratings used: {'YES' if p.agency_ratings_used else 'NO'}. "
        f"LLM used for the decision: **NO**. LLM used for extraction: "
        f"{'no' if p.llm_used == 'none' else p.llm_used}."
    )
    st.markdown("### Evidence (verbatim passages)")
    for f in alert.facts:
        label = f"[{f.source}] " + (f"{f.field}: " if f.field else "") + f.text[:90]
        with st.expander(label):
            doc = documents.get(f.doc_id)
            if doc is None:
                st.markdown("Document not available.")
                continue
            before, quote, after = passage_context(doc, f.char_start, f.char_end)
            st.markdown(
                f"<div style='font-size:0.9em;color:#374151'>{html.escape(before)}"
                f"<mark>{html.escape(quote)}</mark>{html.escape(after)}</div>",
                unsafe_allow_html=True,
            )
            st.caption(
                f"offsets {f.char_start}-{f.char_end}, {f.evidence_type}, {f.extractor_version}"
            )
    st.markdown("### Sources")
    for s in alert.sources:
        if s.available:
            st.markdown(
                f"[{s.index}] [{s.title or s.doc_id}]({s.url})  \n"
                f"form {s.form or 'n/a'}, published {s.published or 'unknown'}, retrieved "
                f"{s.retrieved}  \nraw sha256 `{s.raw_sha256}`  \nnormalised sha256 "
                f"`{s.normalized_sha256}` ({s.normalizer_version})"
            )
        else:
            st.markdown(f"[{s.index}] {s.doc_id} (document not available)")
    if alert.model_provenance:
        st.markdown("### Model provenance")
        for m in alert.model_provenance:
            st.markdown(
                f"**{m.kind}**: {m.model_id} as {m.role or 'n/a'}, prompt {m.prompt_version}, "
                f"schema {m.schema_version}, {m.statements_applied} applied, "
                f"{m.statements_recorded} recorded  \n{m.reason or ''}"
            )
    lang = st.query_params.get("lang")
    if lang in ("fr", "en"):
        from radar.notes import build_note, render_markdown

        note = build_note(alert, lang)
        st.markdown("### Committee note" if lang == "en" else "### Note de comité")
        st.caption(
            "Deterministic note, complete without any model; the optional prose sections "
            "are generated by `radar note` under the demo budget."
        )
        st.markdown(render_markdown(note))
        st.download_button(
            f"Note {lang.upper()} (Markdown)", render_markdown(note),
            file_name=f"{event_id}.{lang}.md", mime="text/markdown",
        )  # fmt: skip
    st.markdown("### Audit")
    st.code(alert.explanation)
    st.dataframe(audit_rows(db, event_id), width="stretch", hide_index=True)
    c1, c2, c3 = st.columns(3)
    c1.download_button(
        "Alert JSON",
        json.dumps(alert.model_dump(mode="json"), indent=2, ensure_ascii=False),
        file_name=f"{event_id}.json",
        mime="application/json",
    )
    c2.download_button(
        "Alert HTML", render_html(alert), file_name=f"{event_id}.html", mime="text/html"
    )
    c3.download_button(
        "Teams card JSON",
        json.dumps(
            render_card(alert, settings.alerts.viewer_base_url), indent=2, ensure_ascii=False
        ),
        file_name=f"{event_id}.card.json",
        mime="application/json",
    )
    st.caption(alert.disclaimer)


def main() -> None:
    st.set_page_config(page_title="Credit Event Radar", layout="wide")
    universe, rules, scales, settings = _config()
    db_path = os.environ.get("RADAR_DB") or str(ROOT / settings.paths.db)
    default = 3 if st.query_params.get("event") else 0
    screen = st.sidebar.radio("Screen", SCREENS, index=default)
    st.sidebar.caption(f"Database: {db_path}")
    db = Database(Path(db_path))
    try:
        if screen == "Dashboard":
            _dashboard(db, universe, scales, settings)
        elif screen == "Watchlist":
            _watchlist(db, universe, scales, settings)
        elif screen == "Alerts":
            _alerts(db, universe)
        else:
            _event_detail(db, universe, rules, settings)
    finally:
        db.close()


main()
