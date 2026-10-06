"""The Alert object (Phase P1): the contract between the engine and everything an analyst
sees. Built from a stored decision, its event, the source documents and the issuer; every
fact is a verbatim passage tied to a numbered source; the provenance says who decided
(the rules engine, always) and which model read what, with the benchmark reason."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from radar.audit import stable_hash
from radar.config import AlertsSettings, Issuer, Rules
from radar.enrich.flags import FAMILIES
from radar.materiality.engine import Decision
from radar.materiality.explain import AGENCY_DISPLAY, render_explanation
from radar.models import CreditEvent, Priority, RawDocument

IssuerUniverse = Literal["live_watchlist", "historical_stress_case", "other"]
DISCLAIMER = (
    "Automatically generated draft from public sources, to be validated by the analyst. "
    "No investment recommendation. / Brouillon généré automatiquement à partir de sources "
    "publiques, à valider par l'analyste. Aucune recommandation d'investissement."
)
FLAG_LABELS = {
    "going_concern": "going concern doubt",
    "covenant": "covenant breach",
    "liquidity": "liquidity concern",
    "impairment": "impairment",
}
UNIVERSE_LABELS = {
    "live_watchlist": "Live watchlist",
    "historical_stress_case": "Historical stress case",
    "other": "Other issuer",
}


class AlertFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    text: str  # the document's own passage, never a paraphrase
    field: str | None = None
    source: int  # index of the source, 0 when the document is unknown
    doc_id: str
    char_start: int
    char_end: int
    evidence_type: str
    extractor_version: str


class AlertRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    priority: Priority
    reason: str
    description: str | None = None


class AlertModifier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    effect: str
    applied: bool
    reason: str


class AlertRating(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agency: str
    agency_name: str
    rating: str
    category: str
    outlook: str | None = None
    watch: str = "none"
    as_of: date
    age_days: int
    origin: str
    verification: str


class AlertSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    doc_id: str
    title: str | None = None
    url: str | None = None
    published: str | None = None
    retrieved: str | None = None
    raw_sha256: str | None = None
    normalized_sha256: str | None = None
    normalizer_version: str | None = None
    form: str | None = None
    available: bool = True


class ModelProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    model_id: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    role: str | None = None
    reason: str | None = None
    statements_applied: int = 0
    statements_recorded: int = 0


class DecisionProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority_decided_by: str = "deterministic rules engine"
    rules_version: str
    base_priority: Priority | None = None
    decision_status: str
    agency_ratings_used: bool
    composite_used: bool = False
    llm_used: str
    extraction_method: str
    enrichment_method: str | None = None
    detectors: list[str] = Field(default_factory=list)


class AlertRouteInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[str]
    mode: str
    digest_every_hours: int | None = None


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str
    generated_at: datetime
    priority: Priority | None
    decision_status: str
    issuer_id: str
    issuer_name: str
    universe: IssuerUniverse
    event_id: str
    family: str
    event_type: str
    effective_date: date | None
    title: str
    summary: str
    facts: list[AlertFact] = Field(default_factory=list)
    triggered_rules: list[AlertRule] = Field(default_factory=list)
    modifiers: list[AlertModifier] = Field(default_factory=list)
    ratings_after: list[AlertRating] = Field(default_factory=list)
    sources: list[AlertSource] = Field(default_factory=list)
    model_provenance: list[ModelProvenance] = Field(default_factory=list)
    decision_provenance: DecisionProvenance
    route: AlertRouteInfo | None = None
    explanation: str
    disclaimer: str = DISCLAIMER

    @property
    def key_facts(self) -> list[AlertFact]:
        """The three facts of the card: the first verified passages of the event."""
        return self.facts[:3]

    @property
    def universe_label(self) -> str:
        return UNIVERSE_LABELS[self.universe]


def universe_of(issuer: Issuer | None) -> IssuerUniverse:
    """Live watchlist, historical stress case or other, from the universe tags; the two
    universes are never mixed in what an analyst sees."""
    tags = set(issuer.tags) if issuer else set()
    if "demo_watchlist" in tags:
        return "live_watchlist"
    if "historical_control" in tags:
        return "historical_stress_case"
    return "other"


def _title(event: CreditEvent) -> str:
    f = event.fields if isinstance(event.fields, dict) else {}
    if event.family == "rating":
        agency = AGENCY_DISPLAY.get(f.get("agency") or "", f.get("agency") or "Agency")
        action = event.event_type.replace("_", " ")
        core = ""
        if f.get("old_rating") and f.get("new_rating"):
            core = f"{f['old_rating']} → {f['new_rating']}"
            if f.get("crosses_ig_to_hy"):
                core += ", IG → HY"
            elif (
                f.get("old_category")
                and f.get("new_category")
                and f["old_category"] != f["new_category"]
            ):
                core += f", {f['old_category']} → {f['new_category']}"
        elif f.get("new_rating"):
            core = str(f["new_rating"])
        head = f"{agency} {action}" + (f": {core}" if core else "")
        extras = []
        if f.get("new_outlook"):
            extras.append(f"outlook {f['new_outlook']}")
        if f.get("watch") and f["watch"] != "none":
            extras.append(f"watch {f['watch']}")
        return head + (f" ({', '.join(extras)})" if extras else "")
    if event.family == "earnings":
        form = str(f.get("report_form") or "")
        head = (
            "Annual report"
            if form.startswith("10-K")
            else ("Quarterly report" if form else "Results release")
        )
        base = head + (f" {f['period']}" if f.get("period") else "")
        flags = [x for x in (f.get("flags") or []) if isinstance(x, str)]
        if flags:
            return f"{base}: " + ", ".join(FLAG_LABELS.get(x, x) for x in flags)
        if f.get("guidance_status"):
            detail = f"guidance {f['guidance_status']}"
            if f.get("guidance_metric"):
                detail += f" ({f['guidance_metric']})"
            if f.get("guidance_old") is not None and f.get("guidance_new") is not None:
                detail += f" {f['guidance_old']} → {f['guidance_new']}"
            return f"{base}: {detail}"
        return base
    if event.family == "issuance":
        parts = ["New issue"]
        if f.get("amount") is not None:
            parts.append(f"{f['amount']} {f.get('currency') or ''}".strip())
        if f.get("seniority"):
            parts.append(str(f["seniority"]))
        if f.get("maturity"):
            parts.append(f"due {f['maturity']}")
        return parts[0] + (": " + " ".join(parts[1:]) if len(parts) > 1 else "")
    return event.event_type.replace("_", " ")


def _model_provenance(fields: dict[str, Any]) -> list[ModelProvenance]:
    out: list[ModelProvenance] = []
    for kind in ("guidance", *FAMILIES):
        source = fields.get(f"{kind}_source")
        if not isinstance(source, dict):
            continue
        sel = (
            source.get("model_selection") if isinstance(source.get("model_selection"), dict) else {}
        )
        out.append(
            ModelProvenance(
                kind=kind,
                model_id=source.get("model_id"),
                prompt_version=source.get("prompt_version"),
                schema_version=source.get("schema_version"),
                role=sel.get("role"),
                reason=sel.get("reason"),
                statements_applied=len(source.get("statement_ids") or []),
                statements_recorded=len(fields.get(f"llm_{kind}") or []),
            )
        )
    return out


def build_alert(
    event: CreditEvent,
    decision: Decision,
    documents: dict[str, RawDocument],
    issuer: Issuer | None,
    *,
    rules: Rules,
    alerts: AlertsSettings,
    generated_at: datetime | None = None,
) -> Alert:
    fields = event.fields if isinstance(event.fields, dict) else {}
    sources: list[AlertSource] = []
    index_of: dict[str, int] = {}
    for i, doc_id in enumerate(event.source_doc_ids, 1):
        index_of[doc_id] = i
        doc = documents.get(doc_id)
        if doc is None:
            sources.append(AlertSource(index=i, doc_id=doc_id, available=False))
            continue
        extra = doc.extra if isinstance(doc.extra, dict) else {}
        sources.append(
            AlertSource(
                index=i, doc_id=doc_id, title=doc.title, url=str(doc.url),
                published=doc.published_at.date().isoformat() if doc.published_at else None,
                retrieved=doc.retrieved_at.isoformat(), raw_sha256=doc.content_hash,
                normalized_sha256=doc.doc_id, normalizer_version=doc.normalizer_version,
                form=str(extra["form"]) if extra.get("form") else None,
            )
        )  # fmt: skip
    facts = [
        AlertFact(
            index=i,
            text=span.quote,
            field=span.field,
            source=index_of.get(span.doc_id, 0),
            doc_id=span.doc_id,
            char_start=span.char_start,
            char_end=span.char_end,
            evidence_type=span.evidence_type,
            extractor_version=span.extractor_version,
        )  # fmt: skip
        for i, span in enumerate(event.evidence, 1)
    ]
    triggered = []
    for r in decision.rules:
        if not r.triggered:
            continue
        try:
            description = rules.by_id(r.id).description
        except KeyError:
            description = None
        triggered.append(
            AlertRule(id=r.id, priority=r.priority, reason=r.reason, description=description)
        )
    modifiers = [
        AlertModifier(id=m.id, effect=m.effect, applied=m.applied, reason=m.reason)
        for m in decision.modifiers
    ]
    ratings = []
    if decision.state_after is not None:
        for agency in sorted(decision.state_after.entries):
            e = decision.state_after.entries[agency]
            ratings.append(
                AlertRating(
                    agency=agency,
                    agency_name=AGENCY_DISPLAY.get(agency, agency),
                    rating=e.rating,
                    category=e.category,
                    outlook=e.outlook,
                    watch=e.watch,
                    as_of=e.as_of,
                    age_days=e.age_days,
                    origin=e.origin,
                    verification=e.verification,
                )  # fmt: skip
            )
    priority = decision.final_priority
    ids = [r.id for r in triggered]
    if priority:
        summary = (
            f"{priority} by {', '.join(ids)} (deterministic rules engine, "
            f"rules.yaml {decision.rules_version})"
        )
    else:
        summary = (
            f"No priority ({decision.decision_status}): no applicable rule "
            f"(rules.yaml {decision.rules_version})"
        )
    route = alerts.routing.get(priority) if priority else None
    provenance = DecisionProvenance(
        rules_version=decision.rules_version,
        base_priority=decision.base_priority,
        decision_status=decision.decision_status,
        agency_ratings_used=decision.provenance.agency_ratings_used,
        composite_used=decision.provenance.composite_used,
        llm_used=decision.provenance.llm_used,
        extraction_method=event.extraction_method,
        enrichment_method=event.enrichment_method,
        detectors=sorted(
            {s.extractor_version for s in event.evidence if s.evidence_type != "llm_statement"}
        ),
    )
    alert_id = stable_hash(
        {
            "event_id": event.event_id,
            "priority": priority,
            "rules_version": decision.rules_version,
            "triggered": ids,
            "facts": [(f.doc_id, f.char_start, f.char_end) for f in facts],
        }
    )
    return Alert(
        alert_id=alert_id,
        generated_at=generated_at or datetime.now(UTC),
        priority=priority,
        decision_status=decision.decision_status,
        issuer_id=event.issuer_id,
        issuer_name=issuer.name if issuer else event.issuer_id,
        universe=universe_of(issuer),
        event_id=event.event_id,
        family=event.family,
        event_type=event.event_type,
        effective_date=event.effective_date,
        title=_title(event),
        summary=summary,
        facts=facts,
        triggered_rules=triggered,
        modifiers=modifiers,
        ratings_after=ratings,
        sources=sources,
        model_provenance=_model_provenance(fields),
        decision_provenance=provenance,
        route=AlertRouteInfo(
            channels=list(route.channels),
            mode=route.mode,
            digest_every_hours=route.digest_every_hours,
        )
        if route
        else None,
        explanation=render_explanation(decision, event, documents),
    )
