"""Lot 3.3b: validated LLM statements enrich existing earnings_release events, the engine
replays ERN-02/03/04. Extraction and application are separate steps; nothing deterministic
is ever overwritten; every application is idempotent and audited before/after."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from radar.config import (
    CONFIG_DIR,
    Issuer,
    Universe,
    load_rating_scales,
    load_rules,
)
from radar.db import Database
from radar.enrich.apply import apply_all, apply_event
from radar.enrich.extract import extract_events
from radar.enrich.guidance import build_enrichment
from radar.llm.budget import RunBudget
from radar.llm.cache import LLMCache
from radar.llm.pricing import Pricing
from radar.llm.prompts import load_prompt
from radar.llm.provider import ExtractionRequest, ExtractionResponse, LLMProvider, estimate_tokens
from radar.models import RawDocument
from radar.pipeline import process
from radar.snapshot import sha256_hex

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "prompts" / "extraction" / "guidance.v1.yaml"
ISSUER = Issuer(
    id="ISSUER_TEST_A",
    name="Issuer Test A",
    legal_entity="Issuer Test A AG",
    aliases=["ITA"],
    sector="t",
    country="DE",
    principal_division="Vehicles Division",
    segments=["Financial Services"],
)
UNIVERSE = Universe(
    name="t", disclosure="fictional", retrieved_as_of=date(2026, 1, 1), issuers=[ISSUER]
)

# One results release per scenario. Titles satisfy the deterministic results detector; the
# outlook sentences are written so that the deterministic extractor finds no guidance
# statement (no "confirms its outlook", no "lowers its guidance"), except scenario H.
S_FCF_BIG = "Net cash flow for 2026 is now expected between EUR 2 billion and EUR 3 billion (previously: EUR 4 billion to EUR 5 billion)."
S_FCF_SMALL = "Net cash flow for 2026 is now expected to be around EUR 4.2 billion (previously: around EUR 4.5 billion)."
S_MARGIN_REAFF = "The Group continues to expect an operating return on sales between 7.5 and 8.5 percent in 2026."
S_REV_INLINE = "Sales revenue in 2026 is expected to be in line with the previous year."
S_SEGMENT = "Organic CAPEX for Financial Services is expected to be around EUR 1 billion in 2026."
S_REV_YOY = "For 2026, the Group now expects a range of -10 to 0 percent for sales revenue (previously -5 to +5 percent)."
S_DET_CUT = "Issuer Test A lowers its full-year outlook for 2026 after a weak first quarter."

TEXTS = {
    "A": f"Issuer Test A reports first quarter results\nOutlook 2026\n{S_FCF_BIG}\n",
    "B": f"Issuer Test A reports second quarter results\nOutlook 2026\n{S_FCF_SMALL}\n",
    "C": f"Issuer Test A reports third quarter results\nOutlook 2026\n{S_MARGIN_REAFF}\n",
    "D": f"Issuer Test A reports fourth quarter results\nOutlook 2026\n{S_REV_INLINE}\n",
    "E": f"Issuer Test A reports first half results\nOutlook 2026\n{S_SEGMENT}\n",
    "F": "Issuer Test A reports nine months results\nOutlook 2026\nNothing forward looking here.\n",
    "G": f"Issuer Test A reports annual results\nOutlook 2026\n{S_REV_YOY}\n",
    "H": f"Issuer Test A interim report\n{S_DET_CUT}\nOutlook 2026\n{S_MARGIN_REAFF}\n",
}


def _st(metric, label, basis, unit, quote, text, status, **bounds):
    start = text.index(quote)
    return {
        "metric": metric,
        "metric_label": label,
        "basis": basis,
        "unit": unit,
        "previous_lower": None,
        "previous_upper": None,
        "current_lower": None,
        "current_upper": None,
        "period": "2026",
        "status": status,
        "direction_claimed": None,
        "evidence_quote": quote,
        "start_offset": start,
        "end_offset": start + len(quote),
        **bounds,
    }


def _payloads() -> dict[str, dict]:
    t = TEXTS
    return {
        "A": {
            "has_guidance": True,
            "statements": [
                _st(
                    "fcf",
                    "Net cash flow",
                    "absolute",
                    "EUR_BN",
                    S_FCF_BIG,
                    t["A"],
                    "cut",
                    previous_lower=4,
                    previous_upper=5,
                    current_lower=2,
                    current_upper=3,
                )
            ],
        },
        "B": {
            "has_guidance": True,
            "statements": [
                _st(
                    "fcf",
                    "Net cash flow",
                    "absolute",
                    "EUR_BN",
                    S_FCF_SMALL,
                    t["B"],
                    "cut",
                    previous_lower=4.5,
                    previous_upper=4.5,
                    current_lower=4.2,
                    current_upper=4.2,
                )
            ],
        },
        "C": {
            "has_guidance": True,
            "statements": [
                _st(
                    "margin",
                    "operating return on sales",
                    "margin_pct",
                    "PCT",
                    S_MARGIN_REAFF,
                    t["C"],
                    "reaffirmed",
                    current_lower=7.5,
                    current_upper=8.5,
                )
            ],
        },
        "D": {
            "has_guidance": True,
            "statements": [
                _st(
                    "revenue",
                    "Sales revenue",
                    "yoy_change_pct",
                    "PCT",
                    S_REV_INLINE,
                    t["D"],
                    "mentioned",
                )
            ],
        },
        "E": {
            "has_guidance": True,
            "statements": [
                _st(
                    "capex",
                    "Organic CAPEX for Financial Services",
                    "absolute",
                    "EUR_BN",
                    S_SEGMENT,
                    t["E"],
                    "new",
                    current_lower=1,
                    current_upper=1,
                )
            ],
        },
        "F": {
            "has_guidance": True,
            "statements": [
                {
                    **_st(
                        "ebitda",
                        "EBITDA",
                        "absolute",
                        "EUR_BN",
                        "Nothing forward looking here.",
                        t["F"],
                        "new",
                        current_lower=9,
                        current_upper=9,
                    ),
                    "evidence_quote": "EBITDA is expected to reach EUR 9 billion in 2026.",
                }
            ],
        },
        "G": {
            "has_guidance": True,
            "statements": [
                _st(
                    "revenue",
                    "sales revenue",
                    "yoy_change_pct",
                    "PCT",
                    S_REV_YOY,
                    t["G"],
                    "cut",
                    previous_lower=-5,
                    previous_upper=5,
                    current_lower=-10,
                    current_upper=0,
                )
            ],
        },
        "H": {
            "has_guidance": True,
            "statements": [
                _st(
                    "margin",
                    "operating return on sales",
                    "margin_pct",
                    "PCT",
                    S_MARGIN_REAFF,
                    t["H"],
                    "reaffirmed",
                    current_lower=7.5,
                    current_upper=8.5,
                )
            ],
        },
    }


class ScriptedProvider(LLMProvider):
    name = "openai"

    def __init__(self) -> None:
        self.calls = 0
        self.payloads = _payloads()

    def complete(self, request: ExtractionRequest) -> ExtractionResponse:
        self.calls += 1
        key = next(k for k, text in TEXTS.items() if text.split("\n", 1)[0] in request.user)
        raw = json.dumps(self.payloads[key])
        return ExtractionResponse(
            raw_json=raw, model_id=request.model_id, resolved_model=request.model_id,
            provider=self.name, input_tokens=request.estimated_input_tokens,
            output_tokens=estimate_tokens(raw), latency_ms=7, reasoning_effort=request.reasoning_effort,
        )  # fmt: skip


def _doc(key: str, month: int = 1) -> RawDocument:
    text = TEXTS[key]
    return RawDocument(
        doc_id=sha256_hex(text), source_type="ir_feed", url=f"https://example.invalid/{key}",
        title=text.split("\n", 1)[0], published_at=datetime(2026, month, 15, tzinfo=UTC),  # one month apart: the dedup window is 2 days
        retrieved_at=datetime(2026, 10, 6, tzinfo=UTC), content_hash=sha256_hex(text.encode()),
        raw_size_bytes=len(text), normalizer_version="t", text=text, raw_path="p",
        issuer_hint="ISSUER_TEST_A", extra={"document_type": "press_release", "source_id": "src", "kind": "rss"},
    )  # fmt: skip


@pytest.fixture
def world(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    db.init_schema()
    db.replace_universe(UNIVERSE)
    scales = load_rating_scales(CONFIG_DIR / "rating_scales.yaml")
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    for i, key in enumerate(TEXTS):
        db.insert_document(_doc(key, i + 1))
    process(db, UNIVERSE, scales, rules)
    events = {e.source_doc_ids[0]: e for e in db.list_events()}
    by_key = {key: events[_doc(key).doc_id] for key in TEXTS}
    provider = ScriptedProvider()
    budget = RunBudget(
        limit_usd=10.0,
        pricing=Pricing.model_validate(
            {
                "version": "t",
                "models": {
                    "gpt-5.6-terra": {"input_per_million_usd": 2.0, "output_per_million_usd": 12.0}
                },
            }
        ),
    )
    cache = LLMCache(db)
    prompt = load_prompt(PROMPT)
    yield dict(
        db=db,
        scales=scales,
        rules=rules,
        events=by_key,
        provider=provider,
        budget=budget,
        cache=cache,
        prompt=prompt,
    )
    db.close()


def _extract(w, **kw):
    return extract_events(
        w["db"], UNIVERSE, provider=w["provider"], cache=w["cache"], budget=w["budget"],
        prompt=w["prompt"], model_id="gpt-5.6-terra", reasoning_effort="low", **kw,
    )  # fmt: skip


def test_deterministic_pass_leaves_guidance_fields_empty(world):
    events = world["events"]
    assert len(events) == 8 and all(e.event_type == "earnings_release" for e in events.values())
    for key, e in events.items():
        if key != "H":
            assert e.fields["guidance_status"] is None and e.extraction_method == "structured"
            assert world["db"].priority_of(e.event_id) == (None, "NO_APPLICABLE_RULE")
    assert events["H"].fields["guidance_status"] == "cut"


def test_extract_stores_validated_statements_without_touching_events(world):
    db = world["db"]
    before = {k: (e.fields, e.extraction_method) for k, e in world["events"].items()}
    summary = _extract(world)
    assert summary.documents == 8 and summary.calls == 8 and world["provider"].calls == 8
    assert summary.statements == 8 and summary.valid == 6  # E (scope) and F (span) are invalid
    for key, e in world["events"].items():
        stored = db.get_event(e.event_id)
        assert (stored.fields, stored.extraction_method) == before[key]
        assert stored.enrichment_method is None
    rows = db.statements_for_document(world["events"]["E"].source_doc_ids[0])
    assert len(rows) == 1 and rows[0]["validation_status"] == "INVALID"
    assert "OUT_OF_SCOPE_SEGMENT" in json.dumps(rows[0]["validation_json"])
    row = db.statements_for_document(world["events"]["A"].source_doc_ids[0])[0]
    assert row["llm_call_id"] is not None and row["model_id"] == "gpt-5.6-terra"
    assert (
        row["prompt_version"] == world["prompt"].version
        and row["event_id"] == world["events"]["A"].event_id
    )


def _priority(w, key):
    return w["db"].priority_of(w["events"][key].event_id)


def test_quantitative_cut_on_fcf_gives_ern02_or_ern03_by_threshold(world):
    _extract(world)
    results = apply_all(world["db"], world["rules"], world["scales"])
    assert _priority(world, "A") == ("P1", "DECIDED")
    assert _priority(world, "B") == ("P2", "DECIDED")
    big = world["db"].get_event(world["events"]["A"].event_id)
    assert big.fields["guidance_metric"] == "fcf" and big.fields["guidance_status"] == "cut"
    assert big.fields["guidance_old"] == 4.5 and big.fields["guidance_new"] == 2.5
    assert big.fields["guidance_change_pct"] == pytest.approx(-44.444, abs=0.01)
    assert big.extraction_method == "structured" and big.enrichment_method == "llm_validated"
    source = big.fields["guidance_source"]
    assert source["method"] == "llm_validated" and source["model_id"] == "gpt-5.6-terra"
    assert source["prompt_version"] == world["prompt"].version and len(source["statement_ids"]) == 1
    assert source["llm_call_ids"] and source["schema_version"] == "guidance-1.0"
    llm_spans = [s for s in big.evidence if s.evidence_type == "llm_statement"]
    assert len(llm_spans) == 1 and llm_spans[0].quote == S_FCF_BIG
    assert TEXTS["A"][llm_spans[0].char_start : llm_spans[0].char_end] == S_FCF_BIG
    decision = world["db"].get_decision(big.event_id)
    assert "ERN-02" in decision.triggered_ids()
    applied = [r for r in results if r.event_id == big.event_id][0]
    assert (
        applied.status == "applied"
        and applied.priority_before is None
        and applied.priority_after == "P1"
    )
    entries = world["db"].audit_entries(event_id=big.event_id)
    assert any(
        e["step"] == "llm_apply" and "before None" in e["message"] and "after P1" in e["message"]
        for e in entries
    )


def test_explicit_reaffirmation_gives_ern04(world):
    _extract(world)
    apply_all(world["db"], world["rules"], world["scales"])
    assert _priority(world, "C") == ("P3", "DECIDED")
    e = world["db"].get_event(world["events"]["C"].event_id)
    assert e.fields["guidance_status"] == "reaffirmed" and e.fields["guidance_metric"] == "margin"
    assert e.fields["guidance_change_pct"] is None
    assert "ERN-04" in world["db"].get_decision(e.event_id).triggered_ids()


def test_mentioned_statement_changes_nothing_decisional(world):
    _extract(world)
    apply_all(world["db"], world["rules"], world["scales"])
    assert _priority(world, "D") == (None, "NO_APPLICABLE_RULE")
    e = world["db"].get_event(world["events"]["D"].event_id)
    assert e.fields["guidance_status"] is None and e.fields["guidance_metric"] is None
    assert e.fields["llm_guidance"][0]["status"] == "mentioned"  # recorded for the audit


@pytest.mark.parametrize("key", ["E", "F"])
def test_rejected_statements_never_reach_the_event(world, key):
    _extract(world)
    results = apply_all(world["db"], world["rules"], world["scales"])
    e = world["db"].get_event(world["events"][key].event_id)
    assert _priority(world, key) == (None, "NO_APPLICABLE_RULE")
    assert e.enrichment_method is None and "guidance_source" not in e.fields
    assert "llm_guidance" not in e.fields
    assert [r.status for r in results if r.event_id == e.event_id] == ["no_valid_statement"]


def test_growth_rate_cut_is_ern03_without_a_relative_magnitude(world):
    _extract(world)
    apply_all(world["db"], world["rules"], world["scales"])
    assert _priority(world, "G") == ("P2", "DECIDED")
    e = world["db"].get_event(world["events"]["G"].event_id)
    assert e.fields["guidance_change_pct"] is None and e.fields["guidance_change_points"] == -5.0
    assert e.fields["guidance_old"] == 0.0 and e.fields["guidance_new"] == -5.0


def test_deterministic_guidance_status_is_never_overwritten(world):
    _extract(world)
    apply_all(world["db"], world["rules"], world["scales"])
    e = world["db"].get_event(world["events"]["H"].event_id)
    assert e.fields["guidance_status"] == "cut"  # the deterministic reading stays
    assert e.fields["llm_guidance_conflicts"] == [
        {"field": "guidance_status", "deterministic": "cut", "llm": "reaffirmed"}
    ]
    assert e.fields["guidance_metric"] is None  # no partial application either
    assert _priority(world, "H") == ("P2", "DECIDED")  # rules v1.5: explicit cut without magnitude


def test_replay_from_cache_is_free_and_idempotent(world):
    db = world["db"]
    first = _extract(world)
    apply_all(db, world["rules"], world["scales"])
    decisions = {k: db.priority_of(e.event_id) for k, e in world["events"].items()}
    n_enrich = db.count("event_enrichments")
    n_statements = db.count("llm_statements")
    second = _extract(world)
    assert second.calls == 0 and second.cached == 8 and second.cost_usd == 0.0
    assert world["provider"].calls == first.calls == 8
    assert db.count("llm_statements") == n_statements  # same statement ids, no duplicates
    results = apply_all(db, world["rules"], world["scales"])
    assert {r.status for r in results} <= {"already_applied", "no_valid_statement"}
    assert db.count("event_enrichments") == n_enrich
    assert {k: db.priority_of(e.event_id) for k, e in world["events"].items()} == decisions
    e = db.get_event(world["events"]["A"].event_id)
    assert len([s for s in e.evidence if s.evidence_type == "llm_statement"]) == 1


def test_apply_single_event_reports_same_semantic_enrichment(world):
    db = world["db"]
    _extract(world)
    event = world["events"]["A"]
    first = apply_event(db, event.event_id, world["rules"], world["scales"])
    assert first.status == "applied"
    # a second extraction with new statement ids but identical content is detected
    row = db.statements_for_document(event.source_doc_ids[0])[0]
    db.insert_statements([{**row, "statement_id": "s" * 64, "llm_call_id": row["llm_call_id"]}])
    again = apply_event(db, event.event_id, world["rules"], world["scales"])
    assert again.status == "same_semantic_enrichment" and db.count("event_enrichments") == 1


# ------------------------------------------------- pure enrichment logic --- #


def _cand(metric, status, basis="absolute", unit="EUR_BN", period="2026", sid="x", **bounds):
    st = {"metric": metric, "metric_label": metric, "basis": basis, "unit": unit, "period": period,
          "status": status, "previous_lower": None, "previous_upper": None, "current_lower": None,
          "current_upper": None, "evidence_quote": "q", "start_offset": 0, "end_offset": 1, **bounds}  # fmt: skip
    return {"statement_id": sid, "llm_call_id": 1, "statement_json": st,
            "validation_json": {"status": "VALID", "matched_start": 0, "matched_end": 1},
            "validation_status": "VALID", "doc_id": "d" * 64}  # fmt: skip


def test_governing_metric_is_chosen_by_rule_severity_then_magnitude_then_order():
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    revenue = _cand(
        "revenue",
        "cut",
        sid="r",
        previous_lower=100,
        previous_upper=100,
        current_lower=93,
        current_upper=93,
    )
    fcf = _cand(
        "fcf",
        "cut",
        sid="f",
        previous_lower=10,
        previous_upper=10,
        current_lower=8.8,
        current_upper=8.8,
    )
    enr = build_enrichment([revenue, fcf], rules)
    assert enr.governing.metric == "fcf" and enr.governing.rule_simulated == "ERN-02"
    assert enr.decisional_fields["guidance_metric"] == "fcf"
    # both at or above the threshold: the larger comparable magnitude governs
    fcf2 = _cand(
        "fcf",
        "cut",
        sid="f2",
        previous_lower=10,
        previous_upper=10,
        current_lower=8,
        current_upper=8,
    )
    rev2 = _cand(
        "revenue",
        "cut",
        sid="r2",
        previous_lower=100,
        previous_upper=100,
        current_lower=85,
        current_upper=85,
    )
    assert build_enrichment([fcf2, rev2], rules).governing.metric == "fcf"
    # equal rule and magnitude: revenue before ebitda before fcf
    rev3 = _cand(
        "revenue",
        "cut",
        sid="r3",
        previous_lower=100,
        previous_upper=100,
        current_lower=80,
        current_upper=80,
    )
    assert build_enrichment([fcf2, rev3], rules).governing.metric == "revenue"
    assert len(enr.statements) == 2  # every statement stays in llm_guidance


def test_conflicting_statements_block_only_their_metric():
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    a = _cand(
        "fcf", "cut", sid="a", previous_lower=4, previous_upper=5, current_lower=2, current_upper=3
    )
    b = _cand(
        "fcf", "cut", sid="b", previous_lower=4, previous_upper=5, current_lower=1, current_upper=2
    )
    margin = _cand(
        "margin",
        "reaffirmed",
        basis="margin_pct",
        unit="PCT",
        sid="m",
        current_lower=7,
        current_upper=8,
    )
    enr = build_enrichment([a, b, margin], rules)
    assert enr.conflicts == [("fcf", "2026", ["a", "b"])]
    assert enr.governing is None and enr.guidance_status == "reaffirmed"
    # equivalent statements after unit normalisation are not a conflict
    c = _cand(
        "fcf",
        "cut",
        unit="EUR_MN",
        sid="c",
        previous_lower=4000,
        previous_upper=5000,
        current_lower=2000,
        current_upper=3000,
    )
    enr2 = build_enrichment([a, c], rules)
    assert enr2.conflicts == [] and enr2.governing.metric == "fcf"
    assert sorted(enr2.applied_statement_ids) == ["a", "c"]


def test_raised_alone_sets_no_status_and_reaffirmed_wins_over_raised():
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    raised = _cand(
        "fcf",
        "raised",
        sid="u",
        previous_lower=2,
        previous_upper=3,
        current_lower=3,
        current_upper=4,
    )
    assert build_enrichment([raised], rules).guidance_status is None
    reaff = _cand(
        "revenue",
        "reaffirmed",
        basis="yoy_change_pct",
        unit="PCT",
        sid="v",
        current_lower=0,
        current_upper=3,
    )
    enr = build_enrichment([raised, reaff], rules)
    assert (
        enr.guidance_status == "reaffirmed"
        and enr.decisional_fields["guidance_metric"] == "revenue"
    )
    cut = _cand(
        "margin",
        "cut",
        basis="margin_pct",
        unit="PCT",
        sid="w",
        previous_lower=6,
        previous_upper=7,
        current_lower=4,
        current_upper=5,
    )
    enr3 = build_enrichment([cut, reaff], rules)
    assert enr3.guidance_status == "cut" and enr3.governing is None  # margin is outside ERN-02/03
    assert enr3.decisional_fields["guidance_metric"] == "margin"


def test_the_model_selection_reason_travels_with_the_statements(world):
    """ADR-020: why the model was used is stored with each statement, written in the audit
    log and carried into the event's provenance, not only the model's name."""
    selection = {
        "kind": "guidance", "role": "default", "name": "openai_terra", "model_id": "gpt-5.6-terra",
        "provider": "openai", "reason": "guidance gold V1 (2026-10-06): Terra with guardrails",
    }  # fmt: skip
    _extract(world, selection=selection)
    db = world["db"]
    a = world["events"]["A"]
    rows = db.statements_for_document(a.source_doc_ids[0])
    assert rows and rows[0]["model_selection_json"] == selection
    messages = [
        r["message"]
        for r in db.conn.execute("SELECT message FROM audit_log WHERE step = 'llm_extract_events'")
    ]
    assert messages and all("as default for guidance: guidance gold V1" in m for m in messages)
    apply_all(db, world["rules"], world["scales"])
    assert db.get_event(a.event_id).fields["guidance_source"]["model_selection"] == selection
