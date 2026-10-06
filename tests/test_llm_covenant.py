"""Phase 3.4b: covenant statements. The model extracts and qualifies (status compliant,
risk_of_breach, breached, mentioned; resolution none, waived, cured, amended); the code
checks the wording and alone sets fields.flags "covenant" on a breach actually stated,
whatever its resolution. An event of default is a covenant breach only when the passage
ties it to a covenant or a non-compliance."""

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
    COVENANT_SCHEMA_VERSION,
    CovenantExtraction,
    CovenantStatement,
    covenant_json_schema,
)
from radar.llm.validate import validate_covenant_statement
from radar.models import RawDocument
from radar.pipeline import process
from radar.snapshot import sha256_hex
from tests.llm_helpers import ISSUER_NAMES, doc

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "prompts" / "extraction" / "covenant.v1.yaml"


# ------------------------------------------------------------- schema --- #


def test_covenant_schema_is_closed_with_status_and_resolution():
    schema = covenant_json_schema()
    assert schema["additionalProperties"] is False
    props = schema["$defs"]["CovenantStatement"]["properties"]
    assert set(props) == {
        "risk_type", "status", "resolution", "covenant_label", "agreement", "period",
        "evidence_quote", "start_offset", "end_offset",
    }  # fmt: skip
    assert set(props["status"]["enum"]) == {"compliant", "risk_of_breach", "breached", "mentioned"}
    assert set(props["resolution"]["enum"]) == {"none", "waived", "cured", "amended"}
    assert "flag" not in json.dumps(schema).lower() and COVENANT_SCHEMA_VERSION == "covenant-1.0"


def test_covenant_statement_rejects_extra_fields():
    base = dict(
        risk_type="covenant",
        status="breached",
        resolution="waived",
        evidence_quote="q",
        start_offset=0,
        end_offset=1,
    )
    CovenantStatement(**base)
    with pytest.raises(ValidationError):
        CovenantStatement(**base, covenant_flag=True)
    with pytest.raises(ValidationError):
        CovenantExtraction(has_covenant_statements=True, statements=[], flag=True)


def test_covenant_prompt_is_dedicated():
    prompt = load_prompt(PROMPT)
    assert prompt.id == "extraction.covenant" and prompt.version == "1.0.0"
    assert prompt.output_schema == "CovenantExtraction"
    assert "resolution" in prompt.system and "event of default" in prompt.system.lower()


# ---------------------------------------------------------- validator --- #

BREACH_WAIVED = "As of March 31, 2026, the Company was not in compliance with the minimum net worth covenant, and the lender waived this covenant violation on May 6, 2026."
BREACH_CURED = "The Company was not in compliance with the asset coverage ratio covenant for the quarter ended December 31, 2025 and subsequently cured the non-compliance by entering into an amendment."
COMPLIANT = "As of June 30, 2026, we were in compliance with all material terms and covenants under our loan agreements, credit agreements, and bonds."
RISK = "The Company anticipates that it will not remain in compliance with the financial covenants of its Credit Agreement for the next twelve months."
AMENDED = "We obtained an amendment to the Credit Agreement to provide additional covenant headroom for the remaining quarters of 2026."
DEFAULT_PAYMENT = "An event of default occurred under the indenture following the missed interest payment on the 2029 Notes."
DEFAULT_COVENANT = "The outstanding balances were classified as current due to the existing events of default and non-compliance with covenant requirements under the Term Loan."
HYPO = "Future non-compliance with financial covenants may limit our access to existing credit facilities or result in an acceleration of debt obligations."
DEFINITION = "The 2021 Credit Agreement contains customary representations, warranties, affirmative and negative covenants, and events of default."
TEXT = "\n".join(
    [
        "Issuer Test A AG quarterly report",
        BREACH_WAIVED,
        BREACH_CURED,
        COMPLIANT,
        RISK,
        AMENDED,
        DEFAULT_PAYMENT,
        DEFAULT_COVENANT,
        HYPO,
        DEFINITION,
    ]
)


def _st(quote, status, resolution="none", **extra):
    start = TEXT.index(quote)
    return CovenantStatement(
        risk_type="covenant", status=status, resolution=resolution, evidence_quote=quote,
        start_offset=start, end_offset=start + len(quote), **extra,
    )  # fmt: skip


def _validate(st, document=None, segments=None):
    return validate_covenant_statement(
        st, document or doc(TEXT), ISSUER_NAMES, document_date=date(2026, 8, 10), segments=segments
    )


@pytest.mark.parametrize(
    "quote,status,resolution",
    [
        (BREACH_WAIVED, "breached", "waived"),
        (BREACH_CURED, "breached", "cured"),
        (COMPLIANT, "compliant", "none"),
        (RISK, "risk_of_breach", "none"),
        (AMENDED, "mentioned", "amended"),
        (DEFAULT_COVENANT, "breached", "none"),
        (HYPO, "mentioned", "none"),
        (HYPO, "risk_of_breach", "none"),
        (DEFINITION, "mentioned", "none"),
    ],
)
def test_coherent_status_and_resolution_are_valid(quote, status, resolution):
    result = _validate(_st(quote, status, resolution))
    assert result.status == "VALID", result.reasons
    assert result.checks["status_match"] is True and result.checks["resolution_match"] is True


@pytest.mark.parametrize(
    "quote,status,resolution,reason",
    [
        (COMPLIANT, "breached", "none", "STATUS_MISMATCH"),  # no non-compliance stated
        (
            BREACH_WAIVED,
            "compliant",
            "none",
            "STATUS_MISMATCH",
        ),  # a stated breach contradicts compliant
        (AMENDED, "breached", "amended", "STATUS_MISMATCH"),  # preventive amendment, no breach
        (
            DEFAULT_PAYMENT,
            "breached",
            "none",
            "STATUS_MISMATCH",
        ),  # a payment default is not a covenant breach
        (HYPO, "breached", "none", "STATUS_MISMATCH"),  # hypothetical non-compliance
        (DEFINITION, "risk_of_breach", "none", "STATUS_MISMATCH"),  # no anticipation wording
        (BREACH_CURED, "breached", "waived", "RESOLUTION_MISMATCH"),  # cured, not waived
        (COMPLIANT, "compliant", "amended", "RESOLUTION_MISMATCH"),
    ],
)
def test_incoherent_status_or_resolution_is_rejected(quote, status, resolution, reason):
    result = _validate(_st(quote, status, resolution))
    assert result.status == "INVALID"
    assert any(reason in r for r in result.reasons), result.reasons


def test_payment_default_is_outside_the_covenant_topic():
    result = _validate(_st(DEFAULT_PAYMENT, "mentioned"))
    assert result.checks["covenant_named"] is False and result.status == "INVALID"


def test_agency_report_and_segment_are_rejected_as_for_liquidity():
    report = doc(TEXT).model_copy(
        update={"extra": {"document_type": "rating_report", "source_id": "x"}}
    )
    assert _validate(_st(COMPLIANT, "compliant"), document=report).checks["issuer_voice"] is False
    seg = "Cat Financial was not in compliance with its leverage covenant at June 30, 2026."
    text = TEXT + "\n" + seg
    start = text.index(seg)
    st = CovenantStatement(
        risk_type="covenant",
        status="breached",
        evidence_quote=seg,
        start_offset=start,
        end_offset=start + len(seg),
    )
    result = validate_covenant_statement(
        st, doc(text), ISSUER_NAMES, document_date=date(2026, 8, 10), segments=["Cat Financial"]
    )
    assert result.checks["scope_match"] is False


# ------------------------------------------------- end to end, the flag --- #

ISSUER = Issuer(
    id="ISSUER_TEST_A", name="Issuer Test A", legal_entity="Issuer Test A AG", aliases=["ITA"],
    sector="t", country="DE", principal_division="Vehicles Division", segments=["Financial Services"],
)  # fmt: skip
UNIVERSE = Universe(
    name="t", disclosure="fictional", retrieved_as_of=date(2026, 1, 1), issuers=[ISSUER]
)

C_BREACH = "As of March 31, 2026, the Company was not in compliance with the minimum net worth covenant, and the lender waived this covenant violation on May 6, 2026."
C_COMPLIANT = "As of June 30, 2026, we were in compliance with all material terms and covenants under our loan agreements."
C_RISK = "The Company anticipates that it will not remain in compliance with the financial covenants of its Credit Agreement for the next twelve months."
C_AMENDED = "We obtained an amendment to the Credit Agreement to provide additional covenant headroom for the remaining quarters of 2026."
C_PAYMENT = "An event of default occurred under the indenture following the missed interest payment on the 2029 Notes."
C_DET = (
    "The company obtained a covenant waiver from its lenders after breaching the leverage covenant."
)

DOCS = {
    "A": f"Issuer Test A reports first quarter results\nFinancing\n{C_BREACH}\n",
    "B": f"Issuer Test A reports second quarter results\nFinancing\n{C_COMPLIANT}\n",
    "C": f"Issuer Test A reports third quarter results\nFinancing\n{C_RISK}\n",
    "D": f"Issuer Test A reports fourth quarter results\nFinancing\n{C_AMENDED}\n",
    "E": f"Issuer Test A reports first half results\nFinancing\n{C_PAYMENT}\n",
    "F": f"Issuer Test A reports nine months results\nFinancing\n{C_DET}\n",
}
SCRIPT = {
    "A": (C_BREACH, "breached", "waived"),
    "B": (C_COMPLIANT, "compliant", "none"),
    "C": (C_RISK, "risk_of_breach", "none"),
    "D": (C_AMENDED, "mentioned", "amended"),
    "E": (C_PAYMENT, "breached", "none"),  # wrong: a payment default, rejected
    "F": (C_DET, "compliant", "none"),  # wrong: compliant on a stated breach is rejected
}


def _payload(key):
    text = DOCS[key]
    quote, status, resolution = SCRIPT[key]
    start = text.index(quote)
    return {"has_covenant_statements": True, "statements": [{
        "risk_type": "covenant", "status": status, "resolution": resolution, "covenant_label": None,
        "agreement": None, "period": "2026", "evidence_quote": quote, "start_offset": start,
        "end_offset": start + len(quote)}]}  # fmt: skip


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
        db.insert_document(_doc(key, 7 + i))  # after the dates quoted in the passages
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
        db=db,
        scales=scales,
        rules=rules,
        events=by_key,
        provider=ScriptedProvider(),
        budget=budget,
        cache=LLMCache(db),
        prompt=load_prompt(PROMPT),
    )
    db.close()


def _extract(w):
    return extract_events(
        w["db"], UNIVERSE, provider=w["provider"], cache=w["cache"], budget=w["budget"],
        prompt=w["prompt"], model_id="gpt-5.6-terra", reasoning_effort="low", kind="covenant",
    )  # fmt: skip


def _priority(w, key):
    return w["db"].priority_of(w["events"][key].event_id)


def test_deterministic_pass_flags_only_the_explicit_breach(world):
    flags = {k: e.fields["flags"] for k, e in world["events"].items()}
    assert flags == {"A": ["covenant"], "B": [], "C": [], "D": [], "E": [], "F": ["covenant"]}


def test_only_code_sets_the_covenant_flag(world):
    db = world["db"]
    # A carries the deterministic flag already; remove it to show the LLM path alone
    a = world["events"]["A"]
    db.update_event_enrichment(a.event_id, {**a.fields, "flags": []}, [], None)
    from radar.pipeline import decide_event

    db.upsert_decision(decide_event(db, db.get_event(a.event_id), world["rules"], world["scales"]))
    assert _priority(world, "A") == (None, "NO_APPLICABLE_RULE")
    summary = _extract(world)
    assert summary.documents == 6 and summary.statements == 6 and summary.valid == 4
    rows = db.statements_for_document(a.source_doc_ids[0])
    assert rows[0]["statement_kind"] == "covenant" and rows[0]["schema_version"] == "covenant-1.0"
    results = apply_all(db, world["rules"], world["scales"])
    a = db.get_event(a.event_id)
    assert a.fields["flags"] == ["covenant"] and _priority(world, "A") == ("P1", "DECIDED")
    assert "ERN-01" in db.get_decision(a.event_id).triggered_ids()
    assert (
        a.fields["covenant_source"]["statement_ids"]
        and a.fields["llm_covenant"][0]["resolution"] == "waived"
    )
    span = [s for s in a.evidence if s.evidence_type == "llm_statement"][0]
    assert span.field == "flag:covenant (resolution waived)" and span.quote == C_BREACH
    for key in ("B", "C", "D"):
        e = db.get_event(world["events"][key].event_id)
        assert e.fields["flags"] == [] and _priority(world, key) == (None, "NO_APPLICABLE_RULE")
        assert e.fields["llm_covenant"][0]["status"] == SCRIPT[key][1]
    e = db.get_event(world["events"]["E"].event_id)
    assert e.enrichment_method is None and "llm_covenant" not in e.fields  # rejected statement
    # F: the model read a stated breach as compliant; the validator rejects it, the
    # deterministic flag and its P1 stay, nothing is recorded as a conflict
    f = db.get_event(world["events"]["F"].event_id)
    assert f.fields["flags"] == ["covenant"] and _priority(world, "F") == ("P1", "DECIDED")
    assert f.enrichment_method is None and "llm_covenant" not in f.fields
    rejected = db.statements_for_document(f.source_doc_ids[0])[0]
    assert rejected["validation_status"] == "INVALID"
    assert any("STATUS_MISMATCH" in r for r in rejected["validation_json"]["reasons"])
    assert {r.status for r in results} == {"applied", "no_valid_statement"}


def test_covenant_replay_from_cache_is_free_and_idempotent(world):
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


def test_compliance_stated_without_with_is_compliant():
    quote = "We have reviewed our covenants in effect as of June 30, 2026 and determined we are in compliance and expect to remain in compliance in the future."
    text = "Issuer Test A AG report\n" + quote
    st = CovenantStatement(
        risk_type="covenant",
        status="compliant",
        evidence_quote=quote,
        start_offset=text.index(quote),
        end_offset=text.index(quote) + len(quote),
    )
    result = validate_covenant_statement(
        st, doc(text), ISSUER_NAMES, document_date=date(2026, 7, 21)
    )
    assert result.status == "VALID", result.reasons


def test_a_month_named_may_is_not_a_hypothetical_modal():
    quote = "On May 8, 2026, the Company received a waiver from the Lender under the 2025 Credit Agreement waiving the asset coverage ratio non-compliance as of March 31, 2026."
    text = "Issuer Test A AG report\n" + quote
    st = CovenantStatement(
        risk_type="covenant",
        status="breached",
        resolution="waived",
        evidence_quote=quote,
        start_offset=text.index(quote),
        end_offset=text.index(quote) + len(quote),
    )
    result = validate_covenant_statement(
        st, doc(text), ISSUER_NAMES, document_date=date(2026, 5, 11)
    )
    assert result.status == "VALID", result.reasons


# A breach cited as history must not create a covenant event today (same protection as the
# historical_reference guard of the rating extractor).
OLD_BREACH = "During the second quarter of 2025, the Company was not in compliance with the leverage covenant under its Credit Agreement and obtained a waiver from the lenders."
OLD_DATED = "As of September 30, 2025, the Company was not in compliance with the minimum net worth covenant, and the lender waived this covenant violation on November 6, 2025."
RECENT = "As of March 31, 2026, the Company was not in compliance with the minimum net worth covenant, and the lender waived this covenant violation on May 6, 2026."
HIST_TEXT = "Issuer Test A AG report\n" + OLD_BREACH + "\n" + OLD_DATED + "\n" + RECENT


def _hist(quote, status, document_date, resolution="waived"):
    start = HIST_TEXT.index(quote)
    st = CovenantStatement(
        risk_type="covenant",
        status=status,
        resolution=resolution,
        evidence_quote=quote,
        start_offset=start,
        end_offset=start + len(quote),
    )
    return validate_covenant_statement(
        st, doc(HIST_TEXT), ISSUER_NAMES, document_date=document_date
    )


@pytest.mark.parametrize("quote", [OLD_BREACH, OLD_DATED])
def test_a_breach_older_than_a_year_is_a_historical_reference(quote):
    result = _hist(quote, "breached", date(2026, 11, 10))
    assert result.status == "INVALID" and result.checks["historical_reference"] is False
    assert any("HISTORICAL_REFERENCE" in r for r in result.reasons)
    kept = _hist(quote, "mentioned", date(2026, 11, 10))
    assert kept.status == "VALID"  # the history stays a mention


@pytest.mark.parametrize(
    "quote,document_date",
    [(RECENT, date(2026, 5, 11)), (OLD_DATED, date(2025, 11, 14)), (OLD_BREACH, date(2025, 8, 10))],
)
def test_a_breach_of_the_reporting_period_is_current(quote, document_date):
    result = _hist(quote, "breached", document_date)
    assert result.status == "VALID", result.reasons
    assert result.checks["historical_reference"] is True


def test_a_breach_without_a_date_is_not_assumed_historical():
    quote = "The Company was not in compliance with the leverage covenant and obtained a waiver."
    text = "Issuer Test A AG report\n" + quote
    st = CovenantStatement(
        risk_type="covenant",
        status="breached",
        resolution="waived",
        evidence_quote=quote,
        start_offset=text.index(quote),
        end_offset=text.index(quote) + len(quote),
    )
    result = validate_covenant_statement(
        st, doc(text), ISSUER_NAMES, document_date=date(2026, 11, 10)
    )
    assert result.status == "VALID" and result.checks["historical_reference"] is True
