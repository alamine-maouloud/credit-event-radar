"""Going concern family (Phase 3.4c): one schema with four statuses, a dedicated prompt, a
validator that reads a doubt stated for now apart from a hypothetical, a description of the
evaluation, a denial or an alleviation, and a flag set by code on doubt alone. The OMV
"not impacted" sentence, the first false P1 of the project, is the permanent control."""

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
    GOING_CONCERN_SCHEMA_VERSION,
    GoingConcernExtraction,
    GoingConcernStatement,
    going_concern_json_schema,
)
from radar.llm.validate import going_concern_status_ok, validate_going_concern_statement
from radar.models import RawDocument
from radar.pipeline import process
from radar.snapshot import sha256_hex
from tests.llm_helpers import ISSUER_NAMES, doc

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "prompts" / "extraction" / "going_concern.v1.yaml"


# ------------------------------------------------------------- schema --- #


def test_going_concern_schema_is_closed_with_four_statuses():
    schema = going_concern_json_schema()
    assert schema["additionalProperties"] is False
    props = schema["$defs"]["GoingConcernStatement"]["properties"]
    assert set(props) == {
        "risk_type",
        "status",
        "period",
        "evidence_quote",
        "start_offset",
        "end_offset",
    }
    assert set(props["status"]["enum"]) == {"doubt", "alleviated", "negated", "mentioned"}
    assert "flag" not in json.dumps(schema).lower()
    assert GOING_CONCERN_SCHEMA_VERSION == "going_concern-1.0"


def test_going_concern_statement_rejects_extra_fields():
    base = dict(
        risk_type="going_concern", status="doubt", evidence_quote="q", start_offset=0, end_offset=1
    )
    GoingConcernStatement(**base)
    with pytest.raises(ValidationError):
        GoingConcernStatement(**base, going_concern_flag=True)
    with pytest.raises(ValidationError):
        GoingConcernExtraction(has_going_concern_statements=True, statements=[], flag=True)


def test_going_concern_prompt_is_dedicated():
    prompt = load_prompt(PROMPT)
    assert prompt.id == "extraction.going_concern" and prompt.version == "1.0.0"
    assert prompt.output_schema == "GoingConcernExtraction"
    low = prompt.system.lower()
    assert "substantial doubt" in low and "alleviat" in low and "hypothetical risk" in low


# ---------------------------------------------------------- validator --- #

DOUBT = "These conditions and events, considered in the aggregate, raise substantial doubt about the Company's ability to continue as a going concern."
DOUBT_EXISTS = "Management has concluded that there is substantial doubt about our ability to continue as a going concern during the next year."
NOT_ALLEVIATED = "Management's plans may not alleviate the substantial doubt about the Company's ability to continue as a going concern."
DO_NOT_ALLEVIATE = "These plans do not alleviate the substantial doubt about the Company's ability to continue as a going concern."
ALLEVIATED = "Management believes its plans alleviate the substantial doubt about the Company's ability to continue as a going concern."
OMV = "From today's perspective, we assume that based on the measures listed above, the Company's ability to continue as a going concern is not impacted."
NO_DOUBT = "The directors have concluded that there is no material uncertainty about the Group's ability to continue as a going concern."
NO_LONGER = "The conditions identified in 2025 no longer raise substantial doubt about our ability to continue as a going concern."
BASIS = (
    "The condensed consolidated financial statements have been prepared on a going concern basis."
)
EVALUATION = "Management is required to evaluate whether there are conditions and events, considered in the aggregate, that raise substantial doubt about the Company's ability to continue as a going concern within one year after the date the financial statements are issued."
HYPO = "If we are unable to raise additional capital, there could be substantial doubt about our ability to continue as a going concern."
GENERIC = "Risks and uncertainties could affect our ability to continue our operations."
TWO_SENTENCES = "These conditions raise substantial doubt about the Company's ability to continue as a going concern. Management believes its plans alleviate that doubt."
TEXT = "\n".join(
    [
        "Issuer Test A AG quarterly report",
        DOUBT, DOUBT_EXISTS, NOT_ALLEVIATED, DO_NOT_ALLEVIATE, ALLEVIATED, OMV, NO_DOUBT,
        NO_LONGER, BASIS, EVALUATION, HYPO, GENERIC, TWO_SENTENCES,
    ]
)  # fmt: skip


def _st(quote, status, **extra):
    start = TEXT.index(quote)
    return GoingConcernStatement(
        risk_type="going_concern", status=status, evidence_quote=quote,
        start_offset=start, end_offset=start + len(quote), **extra,
    )  # fmt: skip


def _validate(st, document=None, segments=None, document_date=date(2026, 8, 10)):
    return validate_going_concern_statement(
        st, document or doc(TEXT), ISSUER_NAMES, document_date=document_date, segments=segments
    )


@pytest.mark.parametrize(
    "quote,status",
    [
        (DOUBT, "doubt"),
        (DOUBT_EXISTS, "doubt"),
        (NOT_ALLEVIATED, "doubt"),
        (DO_NOT_ALLEVIATE, "doubt"),
        (ALLEVIATED, "alleviated"),
        (TWO_SENTENCES, "alleviated"),
        (OMV, "negated"),
        (NO_DOUBT, "negated"),
        (NO_LONGER, "negated"),
        (BASIS, "mentioned"),
        (EVALUATION, "mentioned"),
        (HYPO, "mentioned"),
        (DOUBT, "mentioned"),  # under-reading never rejected
    ],
)
def test_statuses_coherent_with_the_passage_are_valid(quote, status):
    result = _validate(_st(quote, status))
    assert result.status == "VALID", result.reasons


@pytest.mark.parametrize(
    "quote,status,reason",
    [
        (OMV, "doubt", "NEGATED_DOUBT"),  # the permanent control: the first false P1
        (NO_DOUBT, "doubt", "NEGATED_DOUBT"),
        (NO_LONGER, "doubt", "NEGATED_DOUBT"),
        (HYPO, "doubt", "HYPOTHETICAL_RISK"),
        (EVALUATION, "doubt", "HYPOTHETICAL_RISK"),
        (ALLEVIATED, "doubt", "alleviate"),
        (TWO_SENTENCES, "doubt", "alleviate"),
        (BASIS, "doubt", "no substantial doubt stated"),
        (NOT_ALLEVIATED, "alleviated", "no alleviation"),
        (DO_NOT_ALLEVIATE, "alleviated", "no alleviation"),
        (DOUBT, "alleviated", "no alleviation"),
        (DOUBT, "negated", "contradicts"),
        (BASIS, "negated", "does not deny"),
    ],
)
def test_statuses_the_words_do_not_support_are_rejected(quote, status, reason):
    result = _validate(_st(quote, status))
    assert result.status == "INVALID" and not result.checks["status_match"]
    assert any(reason in r for r in result.reasons), result.reasons


def test_a_generic_risk_without_the_going_concern_is_rejected_whatever_the_status():
    for status in ("doubt", "mentioned"):
        result = _validate(_st(GENERIC, status))
        assert result.status == "INVALID" and not result.checks["going_concern_named"]


def test_status_reading_is_exposed_for_the_gold_tooling():
    assert going_concern_status_ok(DOUBT, "doubt") == (True, None)
    ok, reason = going_concern_status_ok(OMV, "doubt")
    assert not ok and reason.startswith("NEGATED_DOUBT")
    assert going_concern_status_ok(BASIS, "mentioned") == (True, None)


OLD_DOUBT = "As disclosed in the annual report for 2024, substantial doubt about the Company's ability to continue as a going concern existed as of December 31, 2024."
AHEAD = "Management has concluded that there is substantial doubt about our ability to continue as a going concern through September 30, 2027, when the notes mature."


def test_a_doubt_older_than_a_year_is_a_historical_reference():
    text = "\n".join(["Issuer Test A AG report", OLD_DOUBT])
    start = text.index(OLD_DOUBT)
    st = GoingConcernStatement(
        risk_type="going_concern", status="doubt", evidence_quote=OLD_DOUBT,
        start_offset=start, end_offset=start + len(OLD_DOUBT),
    )  # fmt: skip
    result = _validate(st, doc(text), document_date=date(2026, 8, 10))
    assert result.status == "INVALID" and not result.checks["historical_reference"]
    assert any("HISTORICAL_REFERENCE" in r for r in result.reasons)
    st = st.model_copy(update={"status": "mentioned"})
    assert _validate(st, doc(text), document_date=date(2026, 8, 10)).status == "VALID"


def test_a_future_date_in_a_prospective_doubt_is_legitimate():
    text = "\n".join(["Issuer Test A AG report", AHEAD])
    start = text.index(AHEAD)
    st = GoingConcernStatement(
        risk_type="going_concern", status="doubt", evidence_quote=AHEAD,
        start_offset=start, end_offset=start + len(AHEAD),
    )  # fmt: skip
    result = _validate(st, doc(text), document_date=date(2026, 8, 10))
    assert result.status == "VALID", result.reasons


def test_agency_report_and_segment_are_rejected_as_for_the_other_families():
    agency = doc(TEXT).model_copy(update={"extra": {"document_type": "rating_report"}})
    result = _validate(_st(DOUBT, "doubt"), agency)
    assert result.status == "INVALID" and any("THIRD_PARTY_DOCUMENT" in r for r in result.reasons)
    seg = "Financial Services' ability to continue as a going concern is subject to substantial doubt."
    text = "\n".join(["Issuer Test A AG report", seg])
    start = text.index(seg)
    st = GoingConcernStatement(
        risk_type="going_concern", status="doubt", evidence_quote=seg,
        start_offset=start, end_offset=start + len(seg),
    )  # fmt: skip
    result = _validate(st, doc(text), segments=["Financial Services"])
    assert result.status == "INVALID" and not result.checks["scope_match"]


# ------------------------------------------------- end to end, offline --- #

ISSUER = Issuer(
    id="ISSUER_TEST_A", name="Issuer Test A", legal_entity="Issuer Test A AG", aliases=["ITA"],
    sector="t", country="DE", principal_division="Vehicles Division", segments=["Financial Services"],
)  # fmt: skip
UNIVERSE = Universe(
    name="t", disclosure="fictional", retrieved_as_of=date(2026, 1, 1), issuers=[ISSUER]
)

G_DOUBT = "These conditions and events raise substantial doubt about the Company's ability to continue as a going concern."
G_ALLEVIATED = "Management believes its plans alleviate the substantial doubt about the Company's ability to continue as a going concern."
G_NEGATED = "From today's perspective, we assume that based on the measures listed above, the Company's ability to continue as a going concern is not impacted."
G_BASIS = "The consolidated financial statements have been prepared on a going concern basis."
G_HYPO = "If we cannot raise additional capital, there could be substantial doubt about our ability to continue as a going concern."
G_DET = "Management concluded that substantial doubt exists about the Company's ability to continue as a going concern."

DOCS = {
    "A": f"Issuer Test A reports first quarter results\nGoing concern\n{G_DOUBT}\n",
    "B": f"Issuer Test A reports second quarter results\nGoing concern\n{G_ALLEVIATED}\n",
    "C": f"Issuer Test A reports third quarter results\nGoing concern\n{G_NEGATED}\n",
    "D": f"Issuer Test A reports fourth quarter results\nBasis of preparation\n{G_BASIS}\n",
    "E": f"Issuer Test A reports first half results\nRisk factors\n{G_HYPO}\n",
    "F": f"Issuer Test A reports nine months results\nGoing concern\n{G_DET}\n",
}
SCRIPT = {
    "A": (G_DOUBT, "doubt"),
    "B": (G_ALLEVIATED, "alleviated"),
    "C": (G_NEGATED, "negated"),
    "D": (G_BASIS, "mentioned"),
    "E": (G_HYPO, "doubt"),  # wrong: a hypothetical read as a doubt, rejected
    "F": (G_DET, "negated"),  # wrong: a stated doubt read as a denial, rejected
}


def _payload(key):
    text = DOCS[key]
    quote, status = SCRIPT[key]
    start = text.index(quote)
    return {"has_going_concern_statements": True, "statements": [{
        "risk_type": "going_concern", "status": status, "period": "2026", "evidence_quote": quote,
        "start_offset": start, "end_offset": start + len(quote)}]}  # fmt: skip


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
        retrieved_at=datetime(2026, 10, 7, tzinfo=UTC), content_hash=sha256_hex(text.encode()),
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
        db=db, scales=scales, rules=rules, events=by_key, provider=ScriptedProvider(),
        budget=budget, cache=LLMCache(db), prompt=load_prompt(PROMPT),
    )  # fmt: skip
    db.close()


def _extract(w):
    return extract_events(
        w["db"], UNIVERSE, provider=w["provider"], cache=w["cache"], budget=w["budget"],
        prompt=w["prompt"], model_id="gpt-5.6-terra", reasoning_effort="low", kind="going_concern",
    )  # fmt: skip


def _priority(w, key):
    return w["db"].priority_of(w["events"][key].event_id)


def test_deterministic_pass_flags_the_stated_doubts_only(world):
    """structured-earnings-1.2: an alleviated doubt is a reassurance, the OMV denial too."""
    flags = {k: e.fields["flags"] for k, e in world["events"].items()}
    assert flags == {
        "A": ["going_concern"],
        "B": [],
        "C": [],
        "D": [],
        "E": [],
        "F": ["going_concern"],
    }


def test_only_code_sets_the_going_concern_flag(world):
    db = world["db"]
    a = world["events"]["A"]
    db.update_event_enrichment(a.event_id, {**a.fields, "flags": []}, [], None)
    from radar.pipeline import decide_event

    db.upsert_decision(decide_event(db, db.get_event(a.event_id), world["rules"], world["scales"]))
    assert _priority(world, "A") == (None, "NO_APPLICABLE_RULE")
    summary = _extract(world)
    assert summary.documents == 6 and summary.statements == 6 and summary.valid == 4
    rows = db.statements_for_document(a.source_doc_ids[0])
    assert rows[0]["statement_kind"] == "going_concern"
    assert rows[0]["schema_version"] == "going_concern-1.0"
    results = apply_all(db, world["rules"], world["scales"])
    a = db.get_event(a.event_id)
    assert a.fields["flags"] == ["going_concern"] and _priority(world, "A") == ("P1", "DECIDED")
    assert "ERN-01" in db.get_decision(a.event_id).triggered_ids()
    span = [s for s in a.evidence if s.evidence_type == "llm_statement"][0]
    assert span.field == "flag:going_concern" and span.quote == G_DOUBT
    # B: the doubt is alleviated, no flag, the history of the doubt is kept on the event
    b = db.get_event(world["events"]["B"].event_id)
    assert b.fields["flags"] == [] and _priority(world, "B") == (None, "NO_APPLICABLE_RULE")
    assert b.fields["llm_going_concern"][0]["status"] == "alleviated"
    assert (
        b.enrichment_method == "llm_validated"
        and b.fields["going_concern_source"]["statement_ids"] == []
    )
    for key in ("C", "D"):
        e = db.get_event(world["events"][key].event_id)
        assert (
            e.fields["flags"] == [] and e.fields["llm_going_concern"][0]["status"] == SCRIPT[key][1]
        )
    e = db.get_event(world["events"]["E"].event_id)
    assert e.enrichment_method is None and "llm_going_concern" not in e.fields
    rejected = db.statements_for_document(e.source_doc_ids[0])[0]
    assert any("HYPOTHETICAL_RISK" in r for r in rejected["validation_json"]["reasons"])
    f = db.get_event(world["events"]["F"].event_id)
    assert f.fields["flags"] == ["going_concern"] and _priority(world, "F") == ("P1", "DECIDED")
    assert f.enrichment_method is None
    assert {r.status for r in results} == {"applied", "no_valid_statement"}


def test_going_concern_replay_from_cache_is_free_and_idempotent(world):
    first = _extract(world)
    apply_all(world["db"], world["rules"], world["scales"])
    before = {k: _priority(world, k) for k in DOCS}
    n = world["db"].count("event_enrichments")
    second = _extract(world)
    assert second.calls == 0 and second.cached == 6 and second.cost_usd == 0.0 and first.calls == 6
    results = apply_all(world["db"], world["rules"], world["scales"])
    assert {r.status for r in results} <= {"already_applied", "no_valid_statement"}
    assert world["db"].count("event_enrichments") == n
    assert {k: _priority(world, k) for k in DOCS} == before
