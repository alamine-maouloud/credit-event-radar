"""Phase P1: the Alert object is the contract of the product layer. Built from a stored
decision, an event, its documents and the issuer; rendered as JSON, HTML and an Adaptive
Card; sending is optional and never implied."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from radar.alerts import build_alert, write_alert
from radar.alerts.model import Alert
from radar.alerts.render import render_card, render_html, render_teams_message
from radar.alerts.send import send_teams
from radar.config import CONFIG_DIR, Issuer, load_rules, load_settings
from radar.models import EvidenceSpan, RawDocument
from tests.materiality_helpers import candidate, decide, ev, rating_event, state

HEX = "7" * 64
QUOTE = "S&P Global Ratings lowered the Company's long-term credit rating from BBB- to BB+ <b>and</b> kept the outlook stable."


@pytest.fixture(scope="module")
def rules():
    return load_rules(CONFIG_DIR / "rules.yaml")


@pytest.fixture(scope="module")
def alerts_settings():
    return load_settings(CONFIG_DIR / "settings.yaml").alerts


def document() -> RawDocument:
    return RawDocument(
        doc_id=HEX, source_type="edgar", url="https://www.sec.gov/Archives/edgar/data/1/x.htm",
        title="10-Q 0000000001-26-000003", published_at=datetime(2026, 8, 5, tzinfo=UTC),
        retrieved_at=datetime(2026, 10, 6, tzinfo=UTC), content_hash=HEX, raw_size_bytes=10,
        normalizer_version="sec-html-1.0", text="x" * 10, raw_path="p", extra={"form": "10-Q"},
    )  # fmt: skip


def issuer(tags: list[str]) -> Issuer:
    return Issuer(id="ISSUER_TEST_A", name="Issuer Test A", legal_entity="Issuer Test A Inc.", sector="t", country="US", tags=tags)  # fmt: skip


def _span(field: str | None, quote: str = QUOTE) -> EvidenceSpan:
    return EvidenceSpan(doc_id="doc_test", char_start=10, char_end=10 + len(quote), quote=quote,
                        evidence_type="sentence", extractor_version="structured-rating-1.0", match_score=100.0, field=field)  # fmt: skip


def fallen_angel(rules, scales, alerts_settings) -> Alert:
    st = state(rules, scales, candidate("MOODYS", "Baa3"), candidate("FITCH", "BBB"))
    event = rating_event("SP", "BBB-", "BB+", new_outlook="stable", crosses_ig_to_hy=True)
    event = event.model_copy(update={"evidence": [_span(None), _span("new_rating", "BB+")]})
    decision = decide(rules, scales, event, st)
    return build_alert(
        event, decision, {"doc_test": document()}, issuer(["historical_control", "sec_filer"]),
        rules=rules, alerts=alerts_settings,
    )  # fmt: skip


def test_alert_carries_priority_title_rules_ratings_and_provenance(rules, scales, alerts_settings):
    alert = fallen_angel(rules, scales, alerts_settings)
    assert alert.priority == "P1" and alert.decision_status == "DECIDED"
    assert alert.title == "S&P Global Ratings downgrade: BBB- → BB+, IG → HY (outlook stable)"
    assert alert.universe == "historical_stress_case" and alert.issuer_name == "Issuer Test A"
    assert [r.id for r in alert.triggered_rules] == ["RAT-01", "RAT-02"]
    rat02 = alert.triggered_rules[1]
    assert "fallen-angel risk" in (rat02.description or "") and rat02.reason
    ratings = {r.agency: r for r in alert.ratings_after}
    assert (
        ratings["MOODYS"].rating,
        ratings["MOODYS"].category,
        ratings["MOODYS"].agency_name,
    ) == ("Baa3", "IG", "Moody's")
    assert (ratings["FITCH"].rating, ratings["SP"].rating, ratings["SP"].category) == (
        "BBB",
        "BB+",
        "HY",
    )
    prov = alert.decision_provenance
    assert prov.priority_decided_by == "deterministic rules engine" and prov.rules_version == "1.5"
    assert (
        prov.composite_used is False
        and prov.llm_used == "none"
        and prov.agency_ratings_used is True
    )
    assert alert.model_provenance == []
    assert alert.summary == "P1 by RAT-01, RAT-02 (deterministic rules engine, rules.yaml 1.5)"
    assert "FINAL PRIORITY: P1" in alert.explanation


def test_facts_are_verbatim_quotes_tied_to_numbered_sources(rules, scales, alerts_settings):
    alert = fallen_angel(rules, scales, alerts_settings)
    assert [f.index for f in alert.facts] == [1, 2]
    assert (
        alert.facts[0].text == QUOTE and alert.facts[0].source == 1 and alert.facts[0].field is None
    )
    assert alert.facts[1].field == "new_rating" and alert.facts[1].text == "BB+"
    assert len(alert.sources) == 1
    src = alert.sources[0]
    assert (src.index, src.raw_sha256, src.normalized_sha256, src.form) == (1, HEX, HEX, "10-Q")
    assert str(src.url).startswith("https://www.sec.gov/") and src.published == "2026-08-05"
    assert alert.key_facts == alert.facts[:3]


def test_route_follows_the_settings_and_the_alert_id_is_deterministic(
    rules, scales, alerts_settings
):
    a = fallen_angel(rules, scales, alerts_settings)
    b = fallen_angel(rules, scales, alerts_settings)
    assert a.route is not None and a.route.mode == "immediate"
    assert a.route.channels == ["teams", "email", "local"]
    assert a.alert_id == b.alert_id and len(a.alert_id) == 64
    assert "Brouillon" in a.disclaimer or "draft" in a.disclaimer.lower()
    assert "recommendation" in a.disclaimer.lower()


def _enriched_earnings_alert(rules, scales, alerts_settings) -> Alert:
    source = {
        "method": "llm_validated", "statement_ids": ["s" * 64], "model_id": "gpt-5.6-terra",
        "prompt_version": "1.0.0", "schema_version": "going_concern-1.0",
        "model_selection": {"kind": "going_concern", "role": "default", "name": "openai_terra",
                            "model_id": "gpt-5.6-terra", "provider": "openai",
                            "reason": "Gold V1 DEV: 7/7 stress cases detected, 0 false flags"},
    }  # fmt: skip
    event = ev(
        "earnings", "earnings_release", period="Q2 2026", flags=["going_concern"],
        going_concern_source=source, llm_going_concern=[{"statement_id": "s" * 64, "status": "doubt"}],
    )  # fmt: skip
    quote = "These conditions raise substantial doubt about the Company's ability to continue as a going concern."
    span = EvidenceSpan(doc_id="doc_test", char_start=5, char_end=5 + len(quote), quote=quote,
                        evidence_type="llm_statement", extractor_version="gpt-5.6-terra/1.0.0", match_score=100.0, field="flag:going_concern")  # fmt: skip
    event = event.model_copy(update={"evidence": [span], "enrichment_method": "llm_validated"})
    decision = decide(rules, scales, event, None)
    return build_alert(
        event,
        decision,
        {"doc_test": document()},
        issuer(["demo_watchlist"]),
        rules=rules,
        alerts=alerts_settings,
    )


def test_alert_for_an_llm_enriched_flag_names_the_model_and_its_reason(
    rules, scales, alerts_settings
):
    alert = _enriched_earnings_alert(rules, scales, alerts_settings)
    assert alert.priority == "P1" and [r.id for r in alert.triggered_rules] == ["ERN-01"]
    assert alert.title == "Results release Q2 2026: going concern doubt"
    assert alert.universe == "live_watchlist"
    assert len(alert.model_provenance) == 1
    mp = alert.model_provenance[0]
    assert (mp.kind, mp.model_id, mp.role) == ("going_concern", "gpt-5.6-terra", "default")
    assert "7/7" in mp.reason and mp.statements_applied == 1
    assert alert.decision_provenance.llm_used != "none"
    assert alert.decision_provenance.priority_decided_by == "deterministic rules engine"
    assert (
        alert.facts[0].field == "flag:going_concern"
        and alert.facts[0].evidence_type == "llm_statement"
    )


def test_an_event_without_priority_has_no_route_and_a_plain_title(rules, scales, alerts_settings):
    event = ev("other", "management_change", edgar_item="5.02")
    alert = build_alert(
        event,
        decide(rules, scales, event, None),
        {},
        issuer([]),
        rules=rules,
        alerts=alerts_settings,
    )
    assert (
        alert.priority is None
        and alert.route is None
        and alert.decision_status == "NO_APPLICABLE_RULE"
    )
    assert alert.title == "management change" and alert.universe == "other"
    assert alert.summary.startswith("No priority")


def test_html_is_self_contained_escaped_and_complete(rules, scales, alerts_settings):
    alert = fallen_angel(rules, scales, alerts_settings)
    html = render_html(alert)
    assert (
        html.startswith("<!DOCTYPE html>")
        and "<script" not in html
        and "http://" not in html.replace("http://www.w3.org", "")
    )
    assert "&lt;b&gt;and&lt;/b&gt;" in html and "<b>and</b>" not in html
    assert ">P1<" in html and alert.title in html.replace("&amp;", "&")
    assert "Why this priority?" in html and "RAT-01" in html and "RAT-02" in html
    assert "Composite rating used: NO" in html and "LLM used for the decision: NO" in html
    assert "https://www.sec.gov/Archives/edgar/data/1/x.htm" in html and HEX[:16] in html
    assert "LIVE WATCHLIST" not in html and "Historical stress case" in html
    assert "No investment recommendation" in html or "Aucune recommandation" in html


def test_adaptive_card_has_badge_facts_rules_and_source_actions(rules, scales, alerts_settings):
    alert = fallen_angel(rules, scales, alerts_settings)
    card = render_card(alert)
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.4"
    texts = json.dumps(card)
    assert "P1" in texts and "Issuer Test A" in texts and "RAT-01" in texts and "RAT-02" in texts
    factsets = [b for b in card["body"] if b.get("type") == "FactSet"]
    assert factsets and len(factsets[0]["facts"]) <= 3
    assert factsets[0]["facts"][0]["value"].startswith("S&P Global Ratings lowered")
    actions = {a["title"]: a["url"] for a in card["actions"] if a["type"] == "Action.OpenUrl"}
    assert actions["Source [1]"] == "https://www.sec.gov/Archives/edgar/data/1/x.htm"
    assert "event=" in actions["Note FR"] and actions["Note EN"].endswith("lang=en")
    message = render_teams_message(alert)
    assert message["type"] == "message"
    assert message["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert message["attachments"][0]["content"] == card


def test_write_alert_produces_json_html_and_card_files(
    rules, scales, alerts_settings, tmp_path: Path
):
    alert = fallen_angel(rules, scales, alerts_settings)
    paths = write_alert(alert, tmp_path, day="2026-10-07")
    assert {p.suffix for p in paths} == {".json", ".html"} and len(paths) == 3
    assert all(p.parent == tmp_path / "2026-10-07" for p in paths)
    data = json.loads((tmp_path / "2026-10-07" / f"{alert.event_id}.json").read_text())
    assert Alert.model_validate(data).alert_id == alert.alert_id
    card = json.loads((tmp_path / "2026-10-07" / f"{alert.event_id}.card.json").read_text())
    assert card["type"] == "AdaptiveCard"


def test_sending_is_skipped_without_a_configured_webhook(rules, scales, alerts_settings):
    alert = fallen_angel(rules, scales, alerts_settings)
    outcome = send_teams(render_teams_message(alert), webhook_url=None)
    assert outcome == {
        "channel": "teams",
        "sent": False,
        "detail": "TEAMS_WEBHOOK_URL not configured",
    }


def test_validated_statements_without_a_flag_are_recorded_on_the_alert(
    rules, scales, alerts_settings
):
    """The OMV case: the model reads "not impacted", the validator accepts it as negated, the
    alert shows it and sets nothing."""
    event = ev("earnings", "earnings_release", period="Q4 2024", flags=[])
    decision = decide(rules, scales, event, None)
    rows = [
        {
            "statement_kind": "going_concern", "validation_status": "VALID", "doc_id": "doc_test",
            "statement_json": {"status": "negated", "evidence_quote": "From today's perspective, we assume that the Company's ability to continue as a going concern is not impacted."},
            "validation_json": {"matched_start": 5, "matched_end": 120},
        },
        {
            "statement_kind": "liquidity", "validation_status": "INVALID", "doc_id": "doc_test",
            "statement_json": {"status": "concern", "evidence_quote": "rejected"}, "validation_json": {},
        },
        {
            "statement_kind": "covenant", "validation_status": "VALID", "doc_id": "doc_test",
            "statement_json": {"status": "breached", "resolution": "waived", "evidence_quote": "The Company was not in compliance with the leverage covenant and obtained a waiver."},
            "validation_json": {"matched_start": 200, "matched_end": 280},
        },
    ]  # fmt: skip
    alert = build_alert(
        event,
        decision,
        {"doc_test": document()},
        issuer(["demo_watchlist"]),
        rules=rules,
        alerts=alerts_settings,
        statements=rows,
    )
    assert alert.priority is None
    kinds = [(r.kind, r.status, r.negative) for r in alert.recorded_statements]
    assert kinds == [("going_concern", "negated", False), ("covenant", "breached", True)]
    assert alert.recorded_statements[1].resolution == "waived"
    html = render_html(alert)
    assert "Statements read by the model" in html and "not impacted" in html and "no flag" in html
