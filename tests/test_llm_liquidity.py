"""Phase 3.4a: liquidity statements. The model extracts and qualifies (deteriorated, concern,
stable, improved, mentioned); the code checks the polarity against the words of the passage
and alone decides whether fields.flags gains "liquidity" (ERN-01)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from radar.config import CONFIG_DIR, Issuer, Universe, load_rating_scales, load_rules
from radar.db import Database
from radar.enrich.apply import apply_all
from radar.enrich.extract import extract_events
from radar.llm.budget import RunBudget
from radar.llm.cache import LLMCache
from radar.llm.pricing import Pricing
from radar.llm.prompts import load_prompt
from radar.llm.provider import ExtractionRequest, ExtractionResponse, LLMProvider, estimate_tokens
from radar.llm.schemas import (
    LIQUIDITY_SCHEMA_VERSION,
    LiquidityExtraction,
    LiquidityStatement,
    liquidity_json_schema,
)
from radar.llm.validate import validate_liquidity_statement
from radar.models import RawDocument
from radar.pipeline import process
from radar.snapshot import sha256_hex
from tests.llm_helpers import ISSUER_NAMES, doc

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "prompts" / "extraction" / "liquidity.v1.yaml"


# ------------------------------------------------------------- schema --- #


def test_liquidity_schema_is_closed_and_has_no_confidence():
    schema = liquidity_json_schema()
    assert schema["additionalProperties"] is False
    props = schema["$defs"]["LiquidityStatement"]["properties"]
    assert set(props) == {
        "risk_type", "status", "metric_label", "value", "unit", "period",
        "evidence_quote", "start_offset", "end_offset",
    }  # fmt: skip
    assert "confidence" not in json.dumps(schema) and "priority" not in json.dumps(schema)
    assert set(props["status"]["enum"]) == {
        "deteriorated",
        "concern",
        "stable",
        "improved",
        "mentioned",
    }
    assert LIQUIDITY_SCHEMA_VERSION == "liquidity-1.0"


def test_liquidity_statement_rejects_extra_fields_and_bad_offsets():
    base = dict(
        risk_type="liquidity", status="stable", evidence_quote="q", start_offset=0, end_offset=1
    )
    LiquidityStatement(**base)
    with pytest.raises(ValidationError):
        LiquidityStatement(**base, liquidity_flag=True)
    with pytest.raises(ValidationError):
        LiquidityStatement(**{**base, "end_offset": 0})
    with pytest.raises(ValidationError):
        LiquidityExtraction(has_liquidity_statements=True, statements=[], flag=True)


def test_liquidity_prompt_is_dedicated():
    prompt = load_prompt(PROMPT)
    assert prompt.id == "extraction.liquidity" and prompt.version == "1.0.0"
    assert set(prompt.input_variables) == {"issuer_name", "document_date", "source_text"}
    assert prompt.output_schema == "LiquidityExtraction"
    assert "never" in prompt.system.lower() and "flag" not in prompt.system.lower().replace(
        "flagship", ""
    )


# ---------------------------------------------------------- validator --- #

CONSTRAINED = "Liquidity has become constrained in the second quarter."
NO_CONCERNS = "We have no liquidity concerns for the coming twelve months."
STRONG = "We maintain a strong liquidity position with EUR 4.2 billion of cash."
GENERIC = "Liquidity risk management is described in the risk report."
TIGHTENED = "Liquidity tightened significantly and the Group may be unable to meet its obligations."
SEGMENT = "Liquidity at the Financial Services division tightened in the quarter."
OTHER = "Issuer Test B AG reports constrained liquidity after the acquisition."
NET_LIQ = "Net liquidity in the Automotive Division amounted to EUR 34 billion."
TEXT = "\n".join(
    [
        "Issuer Test A AG half-year report",
        CONSTRAINED,
        NO_CONCERNS,
        STRONG,
        GENERIC,
        TIGHTENED,
        SEGMENT,
        OTHER,
        NET_LIQ,
    ]
)


def _st(quote: str, status: str, **extra) -> LiquidityStatement:
    start = TEXT.index(quote)
    return LiquidityStatement(
        risk_type="liquidity",
        status=status,
        evidence_quote=quote,
        start_offset=start,
        end_offset=start + len(quote),
        **extra,
    )


def _validate(st, segments=None):
    return validate_liquidity_statement(
        st, doc(TEXT), ISSUER_NAMES, document_date=date(2026, 7, 25), segments=segments
    )


@pytest.mark.parametrize(
    "quote,status",
    [
        (CONSTRAINED, "concern"),
        (CONSTRAINED, "deteriorated"),
        (TIGHTENED, "deteriorated"),
        (NO_CONCERNS, "stable"),
        (NO_CONCERNS, "mentioned"),
        (STRONG, "stable"),
        (STRONG, "improved"),
        (GENERIC, "mentioned"),
    ],
)
def test_statuses_compatible_with_the_passage_are_valid(quote, status):
    result = _validate(_st(quote, status))
    assert result.status == "VALID", result.reasons
    assert result.checks["polarity_match"] is True


@pytest.mark.parametrize(
    "quote,status",
    [
        (NO_CONCERNS, "concern"),  # negation: the negative status contradicts the passage
        (STRONG, "deteriorated"),
        (STRONG, "concern"),
        (GENERIC, "concern"),  # a generic mention carries no deterioration
    ],
)
def test_negative_status_without_a_negative_passage_is_a_polarity_mismatch(quote, status):
    result = _validate(_st(quote, status))
    assert result.status == "INVALID" and result.checks["polarity_match"] is False
    assert any(r.startswith("POLARITY_MISMATCH") for r in result.reasons)


def test_segment_and_other_entity_are_rejected_as_for_guidance():
    seg = _validate(_st(SEGMENT, "deteriorated"), segments=["Financial Services"])
    assert seg.status == "INVALID" and seg.checks["scope_match"] is False
    other = _validate(_st(OTHER, "concern"))
    assert other.status == "INVALID" and other.checks["entity_match"] is False


def test_value_and_unit_are_checked_only_when_a_value_is_given():
    ok = _validate(_st(NET_LIQ, "stable", metric_label="Net liquidity", value=34, unit="EUR_BN"))
    assert ok.status == "VALID"
    wrong = _validate(_st(NET_LIQ, "stable", metric_label="Net liquidity", value=35, unit="EUR_BN"))
    assert wrong.status == "INVALID" and wrong.checks["numbers_match"] is False
    no_value = _validate(_st(NET_LIQ, "stable"))
    assert no_value.status == "VALID"


def test_quote_outside_the_document_is_rejected():
    st = LiquidityStatement(
        risk_type="liquidity",
        status="concern",
        evidence_quote="Liquidity is constrained.",
        start_offset=0,
        end_offset=25,
    )
    result = validate_liquidity_statement(
        st, doc(TEXT), ISSUER_NAMES, document_date=date(2026, 7, 25)
    )
    assert result.status == "INVALID" and result.checks["span_match"] is False


# ------------------------------------------------- end to end, the flag --- #

ISSUER = Issuer(
    id="ISSUER_TEST_A", name="Issuer Test A", legal_entity="Issuer Test A AG", aliases=["ITA"],
    sector="t", country="DE", principal_division="Vehicles Division", segments=["Financial Services"],
)  # fmt: skip
UNIVERSE = Universe(
    name="t", disclosure="fictional", retrieved_as_of=date(2026, 1, 1), issuers=[ISSUER]
)

L_CONSTRAINED = "Liquidity headroom narrowed markedly and the Group may be unable to meet its obligations without new financing."
L_STRONG = "We maintain a strong liquidity position of EUR 4.2 billion."
L_NO_CONCERNS = "We have no liquidity concerns for the coming twelve months."
L_GENERIC = "Liquidity risk management is described in the risk report."
L_DET = "Management notes liquidity constraints in the second half."

DOCS = {
    "A": f"Issuer Test A reports first quarter results\nFinancing\n{L_CONSTRAINED}\n",
    "B": f"Issuer Test A reports second quarter results\nFinancing\n{L_STRONG}\n",
    "C": f"Issuer Test A reports third quarter results\nFinancing\n{L_NO_CONCERNS}\n",
    "D": f"Issuer Test A reports fourth quarter results\nFinancing\n{L_GENERIC}\n",
    "E": f"Issuer Test A reports first half results\nFinancing\n{L_DET}\n",
}


SCRIPT = {
    "A": (L_CONSTRAINED, "concern"),
    "B": (L_STRONG, "stable"),
    "C": (L_NO_CONCERNS, "concern"),  # wrong: the passage negates the worry
    "D": (L_GENERIC, "mentioned"),
    "E": (L_DET, "stable"),  # wrong but harmless: the deterministic flag stays
}


def _payload(key: str) -> dict:
    text = DOCS[key]
    quote, status = SCRIPT[key]
    start = text.index(quote)
    return {
        "has_liquidity_statements": True,
        "statements": [
            {
                "risk_type": "liquidity",
                "status": status,
                "metric_label": None,
                "value": None,
                "unit": None,
                "period": "2026",
                "evidence_quote": quote,
                "start_offset": start,
                "end_offset": start + len(quote),
            }
        ],
    }


class ScriptedProvider(LLMProvider):
    name = "openai"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: ExtractionRequest) -> ExtractionResponse:
        self.calls += 1
        key = next(k for k, t in DOCS.items() if t.split("\n", 1)[0] in request.user)
        raw = json.dumps(_payload(key))
        return ExtractionResponse(
            raw_json=raw, model_id=request.model_id, resolved_model=request.model_id, provider=self.name,
            input_tokens=request.estimated_input_tokens, output_tokens=estimate_tokens(raw), latency_ms=3,
            reasoning_effort=request.reasoning_effort,
        )  # fmt: skip


def _doc(key: str, month: int = 1) -> RawDocument:
    text = DOCS[key]
    return RawDocument(
        doc_id=sha256_hex(text), source_type="ir_feed", url=f"https://example.invalid/{key}",
        title=text.split("\n", 1)[0], published_at=datetime(2026, month, 15, tzinfo=UTC),
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
    for i, key in enumerate(DOCS):
        db.insert_document(_doc(key, i + 1))
    process(db, UNIVERSE, scales, rules)
    events = {e.source_doc_ids[0]: e for e in db.list_events()}
    by_key = {key: events[_doc(key).doc_id] for key in DOCS}
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
    yield dict(
        db=db,
        scales=scales,
        rules=rules,
        events=by_key,
        provider=provider,
        budget=budget,
        cache=LLMCache(db),
        prompt=load_prompt(PROMPT),
    )
    db.close()


def _extract(w):
    return extract_events(
        w["db"], UNIVERSE, provider=w["provider"], cache=w["cache"], budget=w["budget"],
        prompt=w["prompt"], model_id="gpt-5.6-terra", reasoning_effort="low", kind="liquidity",
    )  # fmt: skip


def _priority(w, key):
    return w["db"].priority_of(w["events"][key].event_id)


def test_deterministic_pass_flags_only_the_explicit_constraint(world):
    flags = {k: e.fields["flags"] for k, e in world["events"].items()}
    assert flags == {"A": [], "B": [], "C": [], "D": [], "E": ["liquidity"]}
    assert _priority(world, "E") == ("P1", "DECIDED") and _priority(world, "C") == (
        None,
        "NO_APPLICABLE_RULE",
    )


def test_only_code_sets_the_liquidity_flag(world):
    summary = _extract(world)
    assert summary.documents == 5 and summary.statements == 5 and summary.valid == 4
    rows = world["db"].statements_for_document(world["events"]["A"].source_doc_ids[0])
    assert rows[0]["statement_kind"] == "liquidity" and rows[0]["schema_version"] == "liquidity-1.0"
    results = apply_all(world["db"], world["rules"], world["scales"])
    db = world["db"]
    a = db.get_event(world["events"]["A"].event_id)
    assert a.fields["flags"] == ["liquidity"] and _priority(world, "A") == ("P1", "DECIDED")
    assert "ERN-01" in db.get_decision(a.event_id).triggered_ids()
    assert a.enrichment_method == "llm_validated"
    assert (
        a.fields["liquidity_source"]["statement_ids"]
        and a.fields["liquidity_source"]["model_id"] == "gpt-5.6-terra"
    )
    span = [s for s in a.evidence if s.evidence_type == "llm_statement"][0]
    assert span.field == "flag:liquidity" and span.quote == L_CONSTRAINED
    # stable: valid statement, recorded, no flag, no rule
    b = db.get_event(world["events"]["B"].event_id)
    assert b.fields["flags"] == [] and _priority(world, "B") == (None, "NO_APPLICABLE_RULE")
    assert b.fields["llm_liquidity"][0]["status"] == "stable"
    # "no liquidity concerns" read as concern by the model: polarity mismatch, nothing applied
    c = db.get_event(world["events"]["C"].event_id)
    assert c.fields["flags"] == [] and _priority(world, "C") == (None, "NO_APPLICABLE_RULE")
    assert c.enrichment_method is None
    # generic mention: recorded as mentioned, never a flag
    d = db.get_event(world["events"]["D"].event_id)
    assert d.fields["flags"] == [] and d.fields["llm_liquidity"][0]["status"] == "mentioned"
    assert _priority(world, "D") == (None, "NO_APPLICABLE_RULE")
    # deterministic flag already there, model says stable: the deterministic flag stays
    e = db.get_event(world["events"]["E"].event_id)
    assert e.fields["flags"] == ["liquidity"] and _priority(world, "E") == ("P1", "DECIDED")
    assert e.fields["llm_liquidity_conflicts"] == [
        {"field": "flags:liquidity", "deterministic": True, "llm": "stable"}
    ]
    assert {r.status for r in results} == {"applied", "no_valid_statement"}


def test_liquidity_replay_from_cache_is_free_and_idempotent(world):
    first = _extract(world)
    apply_all(world["db"], world["rules"], world["scales"])
    before = {k: _priority(world, k) for k in DOCS}
    n = world["db"].count("event_enrichments")
    second = _extract(world)
    assert second.calls == 0 and second.cached == 5 and second.cost_usd == 0.0 and first.calls == 5
    results = apply_all(world["db"], world["rules"], world["scales"])
    assert {r.status for r in results} <= {"already_applied", "no_valid_statement"}
    assert world["db"].count("event_enrichments") == n
    assert {k: _priority(world, k) for k in DOCS} == before
