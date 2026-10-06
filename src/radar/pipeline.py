"""Orchestration of the deterministic pipeline (docs/SPEC.md section 8, steps 1 to 5).

``ingest`` stores documents with their provenance; ``process`` resolves the issuer,
runs the structured extractors, deduplicates and records every step in ``audit_log``.
No LLM is involved anywhere in this module.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from radar.audit import AuditEntry, stable_hash
from radar.config import Issuer, Rules, Universe
from radar.connectors.base import SourceAdapter
from radar.db import Database
from radar.dedup import find_duplicate
from radar.extract.earnings import extract_earnings_events, extract_periodic_report
from radar.extract.issuance import extract_issuance_events
from radar.extract.ratings_table import extract_rating_observations
from radar.extract.spans import verify_span
from radar.extract.structured import (
    Extraction,
    Skipped,
    document_agency,
    extract_edgar_items,
    extract_rating_actions,
)
from radar.materiality.engine import Decision, MaterialityEngine, PriorContext, PriorEvent
from radar.materiality.state import RatingCandidate, RatingState, build_rating_state
from radar.models import CreditEvent, RawDocument
from radar.ratings import RatingScales
from radar.resolve import resolve_document


@dataclass
class IngestSummary:
    fetched: int = 0
    stored: int = 0
    duplicates: int = 0
    doc_ids: list[str] = field(default_factory=list)


@dataclass
class ProcessSummary:
    documents: int = 0
    resolved: int = 0
    unresolved: int = 0
    events_new: int = 0
    events_merged: int = 0
    candidates_skipped: int = 0
    observations_new: int = 0
    no_event: int = 0
    decisions: dict[str, int] = field(default_factory=dict)
    event_ids: list[str] = field(default_factory=list)


OUTCOME_EVENTS = "EVENTS"
OUTCOME_OBSERVATIONS_ONLY = "OBSERVATIONS_ONLY"
OUTCOME_NO_EVENT = "NO_EVENT"
OUTCOME_UNRESOLVED = "UNRESOLVED"
OUTCOME_UNREADABLE = "UNREADABLE_TEXT"

_COMMON_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "has", "have", "been",
    "will", "its", "our", "not", "which", "their", "than", "also",
}  # fmt: skip
_WORD_RE = re.compile(r"[A-Za-z]+")
READABLE_MIN_COMMON_PER_1000 = 6.0


def text_is_readable(text: str) -> bool:
    """Deterministic guard against garbled extraction (e.g. a PDF font without Unicode map).

    Texts shorter than 500 characters are always considered readable; longer texts need at
    least READABLE_MIN_COMMON_PER_1000 common English words (three letters or more) per
    1000 characters. Calibrated on the Phase 2b documents: readable pages score 10 to 30,
    a PDF with a font lacking a Unicode map scores under 2.
    """
    if len(text) < 500:
        return True
    hits = sum(1 for w in _WORD_RE.findall(text) if w.lower() in _COMMON_WORDS)
    return hits * 1000 / len(text) >= READABLE_MIN_COMMON_PER_1000


def document_kind(doc: RawDocument) -> str:
    """edgar | press_release | ratings_page | rating_report | other, from provenance."""
    if doc.source_type == "edgar":
        return "edgar"
    return str(doc.extra.get("document_type") or "other")


def issuer_names(issuer: Issuer) -> list[str]:
    names = [issuer.name, *issuer.aliases]
    if issuer.legal_entity:
        names.append(issuer.legal_entity)
    return names


def universe_entity_resolver(universe: Universe):
    """Exact, case-insensitive match of a named legal entity against the universe."""
    from radar.extract.structured import _entity_key

    table: dict[str, str] = {}
    for item in universe.issuers:
        for n in issuer_names(item):
            table.setdefault(_entity_key(n), item.id)

    def resolve(name: str) -> str | None:
        return table.get(_entity_key(name))

    return resolve


def run_extractors(
    doc: RawDocument, issuer: Issuer, scales: RatingScales, universe: Universe | None = None
) -> tuple[Extraction, Extraction, object, str | None]:
    """Deterministic extractors for a document kind.

    Returns (rating events, other events, observations or None, table profile or None).
    """
    kind = document_kind(doc)
    empty = Extraction([], [])
    names = issuer_names(issuer)
    resolver = universe_entity_resolver(universe) if universe else None
    if kind == "edgar":
        others = extract_edgar_items(doc, issuer.id)
        periodic = extract_periodic_report(doc, issuer.id)  # 10-Q, 10-K: a results publication
        others.events.extend(periodic.events)
        others.skipped.extend(periodic.skipped)
        return (
            extract_rating_actions(
                doc, issuer.id, scales, issuer_names=names, resolve_entity=resolver
            ),
            others,
            extract_rating_observations(doc, issuer.id, scales, profile="sec_as_of"),
            "sec_as_of",
        )
    if kind == "ratings_page":
        profile = str(doc.extra.get("table_profile") or "current_by_agency")
        aliases = [issuer.name, *issuer.aliases]
        if issuer.legal_entity:
            aliases.append(issuer.legal_entity)
        observations = extract_rating_observations(
            doc, issuer.id, scales, profile=profile, entity_aliases=aliases
        )
        return empty, empty, observations, profile
    if kind == "rating_report":
        agency = document_agency(doc, scales)
        ratings = extract_rating_actions(
            doc,
            issuer.id,
            scales,
            default_agency=agency,
            report_mode=True,
            issuer_names=names,
            resolve_entity=resolver,
        )
        return ratings, empty, None, None
    ratings = extract_rating_actions(
        doc, issuer.id, scales, issuer_names=names, resolve_entity=resolver
    )
    others = Extraction([], [])
    issuance = extract_issuance_events(doc, issuer.id)
    earnings = extract_earnings_events(doc, issuer.id)
    others.events.extend(earnings.events)
    others.skipped.extend(earnings.skipped)
    if earnings.events:
        # A results release recaps past transactions: issuance sentences are not new events.
        for ev in issuance.events:
            span = ev.evidence[0]
            others.skipped.append(
                Skipped(
                    "issuance_mentioned_in_results_release",
                    span.char_start,
                    span.char_end,
                    ev.event_type,
                )
            )
    else:
        others.events.extend(issuance.events)
    others.skipped.extend(issuance.skipped)
    return ratings, others, None, None


def _store(db: Database, doc: RawDocument, summary: IngestSummary) -> None:
    summary.fetched += 1
    stored = db.insert_document(doc)
    inputs = stable_hash({"url": str(doc.url), "content_hash": doc.content_hash})
    if stored:
        summary.stored += 1
        summary.doc_ids.append(doc.doc_id)
        message = f"{doc.source_type} {doc.title or doc.url} ({doc.raw_size_bytes} bytes)"
        db.audit(
            AuditEntry(
                step="ingest",
                doc_id=doc.doc_id,
                inputs_hash=inputs,
                outputs_hash=doc.doc_id,
                message=message,
            )
        )
    else:
        summary.duplicates += 1
        db.audit(
            AuditEntry(
                step="ingest",
                doc_id=doc.doc_id,
                inputs_hash=inputs,
                status="skipped",
                message="duplicate raw content_hash, already stored",
            )
        )


def ingest(
    db: Database, adapter: SourceAdapter, *, since: date, issuers: Sequence[Issuer]
) -> IngestSummary:
    summary = IngestSummary()
    for doc in adapter.fetch(since, issuers):
        _store(db, doc, summary)
    return summary


def ingest_url(db: Database, adapter: SourceAdapter, url: str) -> IngestSummary:
    summary = IngestSummary()
    _store(db, adapter.fetch_document(url), summary)
    return summary


def _record_event(
    db: Database, doc: RawDocument, event: CreditEvent, summary: ProcessSummary
) -> None:
    bad = [s for s in event.evidence if not verify_span(doc, s)]
    if bad:
        db.audit(
            AuditEntry(
                step="extract",
                doc_id=doc.doc_id,
                event_id=event.event_id,
                status="error",
                message=f"{len(bad)} evidence span(s) do not match the document text, dropped",
            )
        )
        return
    duplicate = find_duplicate(db, event)
    outputs = stable_hash(event.model_dump(mode="json"))
    if duplicate:
        db.merge_event_sources(duplicate.event_id, event.source_doc_ids)
        summary.events_merged += 1
        db.audit(
            AuditEntry(
                step="dedup",
                doc_id=doc.doc_id,
                event_id=duplicate.event_id,
                outputs_hash=outputs,
                status="skipped",
                message=f"duplicate of {duplicate.event_id}, sources merged",
            )
        )
        return
    db.insert_event(event)
    summary.events_new += 1
    summary.event_ids.append(event.event_id)
    label = f"{event.family}/{event.event_type}"
    if event.family == "rating":
        f = event.fields
        label += f" {f.get('agency')} {f.get('old_rating')} to {f.get('new_rating')}"
    db.audit(
        AuditEntry(
            step="extract",
            doc_id=doc.doc_id,
            event_id=event.event_id,
            inputs_hash=doc.doc_id,
            outputs_hash=outputs,
            message=f"{label}, method {event.extraction_method}, {len(event.evidence)} spans",
        )
    )


def rating_candidates(
    db: Database, issuer_id: str, *, exclude_event_id: str | None = None
) -> list[RatingCandidate]:
    """Every dated rating known for the issuer: seed rows, document observations, rating events."""
    candidates: list[RatingCandidate] = []
    for r in db.ratings_for(issuer_id):
        candidates.append(
            RatingCandidate(
                agency=r.agency,
                rating=r.rating,
                outlook=r.outlook,
                watch=r.watch,
                rating_type=r.rating_type,
                scope=r.scope,
                as_of=r.as_of,
                as_of_raw=r.as_of_raw,
                origin="seed",
                source=str(r.source_url),
                verification=f"seed:{r.verification_status}",
            )
        )
    for o in db.observations_for(issuer_id):
        candidates.append(
            RatingCandidate(
                agency=o.agency,
                rating=o.rating,
                outlook=o.outlook,
                watch=o.watch,
                rating_type=o.rating_type,
                scope=o.scope,
                as_of=o.as_of,
                as_of_basis=o.as_of_basis,
                origin="observation",
                source=o.doc_id,
                verification=o.verification_method,
            )
        )
    for ev in db.list_events(issuer_id):
        if ev.family != "rating" or ev.event_id == exclude_event_id or ev.effective_date is None:
            continue
        agency, new = ev.fields.get("agency"), ev.fields.get("new_rating")
        if not agency or not new:
            continue
        candidates.append(
            RatingCandidate(
                agency=str(agency),
                rating=str(new),
                outlook=ev.fields.get("new_outlook"),
                watch=ev.fields.get("watch") or "none",
                rating_type="long_term_issuer",
                scope="issuer",
                as_of=ev.effective_date,
                origin="event",
                source=ev.event_id,
                verification=f"event:{ev.extraction_method}",
            )
        )
    return candidates


def decide_event(db: Database, event: CreditEvent, rules: Rules, scales: RatingScales) -> Decision:
    """Materiality decision for one stored event, with the rating state at its date."""
    effective = event.effective_date
    if effective is None:
        docs = [db.get_document(d) for d in event.source_doc_ids]
        published = [d.published_at.date() for d in docs if d and d.published_at]
        effective = min(published) if published else None
    state: RatingState | None = None
    if effective is not None:
        candidates = rating_candidates(db, event.issuer_id, exclude_event_id=event.event_id)
        state = build_rating_state(candidates, effective, rules.rating_state, scales)
    prior = PriorContext(
        prior_events=[
            PriorEvent(
                event_id=d.event_id,
                effective_date=d.effective_date,
                base_priority=d.base_priority,
                negative=d.negative,
            )
            for d in db.decisions_for(event.issuer_id)
            if d.effective_date is not None and d.event_id != event.event_id
        ]
    )
    decision = MaterialityEngine(rules, scales).evaluate(event, state, prior)
    db.upsert_decision(decision)
    db.audit(
        AuditEntry(
            step="materiality",
            event_id=event.event_id,
            inputs_hash=stable_hash(
                {
                    "event": event.model_dump(mode="json"),
                    "state": state.model_dump(mode="json") if state else None,
                }
            ),
            outputs_hash=stable_hash(decision.model_dump(mode="json")),
            rules_version=rules.version,
            message=(
                f"{decision.final_priority or 'NONE'} ({decision.decision_status}) via "
                f"{', '.join(decision.triggered_ids()) or 'no rule'}"
            ),
        )
    )
    return decision


def decide_all(
    db: Database, rules: Rules, scales: RatingScales, issuer_id: str | None = None
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in db.list_events(issuer_id):
        decision = decide_event(db, event, rules, scales)
        key = decision.final_priority or "NONE"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _store_observations(db: Database, doc: RawDocument, extraction, summary: ProcessSummary) -> int:
    stored = 0
    for skipped in extraction.skipped:
        db.audit(
            AuditEntry(
                step="observe",
                doc_id=doc.doc_id,
                status="skipped",
                message=f"table row rejected ({skipped.reason}): {skipped.detail}",
            )
        )
    for obs in extraction.observations:
        if db.insert_observation(obs):
            stored += 1
            summary.observations_new += 1
            db.audit(
                AuditEntry(
                    step="observe",
                    doc_id=doc.doc_id,
                    inputs_hash=doc.doc_id,
                    outputs_hash=obs.observation_id,
                    message=(
                        f"{obs.agency} {obs.rating} as_of {obs.as_of.isoformat()} "
                        f"({obs.as_of_basis}, {obs.verification_method}, {obs.extractor_version})"
                    ),
                )
            )
    return stored


def process(
    db: Database, universe: Universe, scales: RatingScales, rules: Rules | None = None
) -> ProcessSummary:
    summary = ProcessSummary()
    for doc in db.unprocessed_documents():
        summary.documents += 1
        resolution = resolve_document(doc, universe)
        db.mark_resolved(doc.doc_id, resolution.issuer_id, resolution.method)
        if not resolution.resolved:
            summary.unresolved += 1
            db.audit(
                AuditEntry(
                    step="resolve",
                    doc_id=doc.doc_id,
                    status="skipped",
                    message=f"unresolved: {resolution.detail}",
                )
            )
            db.mark_processed(doc.doc_id, OUTCOME_UNRESOLVED)
            db.audit(
                AuditEntry(
                    step="process",
                    doc_id=doc.doc_id,
                    status="skipped",
                    message="UNRESOLVED: no extraction on unresolved document",
                )
            )
            continue
        summary.resolved += 1
        issuer_id = resolution.issuer_id
        assert issuer_id is not None
        db.audit(
            AuditEntry(
                step="resolve",
                doc_id=doc.doc_id,
                message=f"{issuer_id} via {resolution.method} ({resolution.detail})",
            )
        )

        issuer = universe.by_id(issuer_id)
        if not text_is_readable(doc.text):
            db.mark_processed(doc.doc_id, OUTCOME_UNREADABLE)
            db.audit(
                AuditEntry(
                    step="process",
                    doc_id=doc.doc_id,
                    status="skipped",
                    message=(
                        "UNREADABLE_TEXT: normalised text does not look like readable English "
                        f"({doc.normalizer_version}); no extraction, nothing inferred"
                    ),
                )
            )
            continue
        ratings, items, observations, profile = run_extractors(doc, issuer, scales, universe)
        stored_observations = (
            _store_observations(db, doc, observations, summary) if observations else 0
        )
        all_skipped = [*ratings.skipped, *items.skipped]
        for skipped in all_skipped:
            summary.candidates_skipped += 1
            db.audit(
                AuditEntry(
                    step="extract",
                    doc_id=doc.doc_id,
                    status="skipped",
                    message=(
                        f"candidate rejected ({skipped.reason}) at "
                        f"[{skipped.char_start}, {skipped.char_end}): {skipped.detail}"
                    ),
                )
            )
        before = len(summary.event_ids)
        for event in [*ratings.events, *items.events]:
            _record_event(db, doc, event, summary)
        if rules is not None:
            for event_id in summary.event_ids[before:]:
                stored = db.get_event(event_id)
                assert stored is not None
                decision = decide_event(db, stored, rules, scales)
                key = decision.final_priority or "NONE"
                summary.decisions[key] = summary.decisions.get(key, 0) + 1
        n_events = len(ratings.events) + len(items.events)
        if n_events:
            outcome = OUTCOME_EVENTS
        elif stored_observations or (observations and observations.observations):
            outcome = OUTCOME_OBSERVATIONS_ONLY
        else:
            outcome = OUTCOME_NO_EVENT
            summary.no_event += 1
            db.audit(
                AuditEntry(
                    step="no_event",
                    doc_id=doc.doc_id,
                    message=(
                        f"{document_kind(doc)} document: no deterministic extractor matched "
                        f"({len(all_skipped)} rejected candidate(s)); "
                        "stored for audit, nothing inferred"
                    ),
                )
            )
        db.mark_processed(doc.doc_id, outcome)
        db.audit(
            AuditEntry(
                step="process",
                doc_id=doc.doc_id,
                message=(
                    f"{outcome}: {n_events} event(s), {stored_observations} new observation(s), "
                    f"{len(all_skipped)} rejected candidate(s), kind {document_kind(doc)}"
                    + (f", table profile {profile}" if profile else "")
                ),
            )
        )
    return summary
