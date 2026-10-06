"""Orchestration of the deterministic pipeline (docs/SPEC.md section 8, steps 1 to 5).

``ingest`` stores documents with their provenance; ``process`` resolves the issuer,
runs the structured extractors, deduplicates and records every step in ``audit_log``.
No LLM is involved anywhere in this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from radar.audit import AuditEntry, stable_hash
from radar.config import Issuer, Universe
from radar.connectors.base import SourceAdapter
from radar.db import Database
from radar.dedup import find_duplicate
from radar.extract.spans import verify_span
from radar.extract.structured import extract_edgar_items, extract_rating_actions
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
    event_ids: list[str] = field(default_factory=list)


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


def process(db: Database, universe: Universe, scales: RatingScales) -> ProcessSummary:
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
            db.mark_processed(doc.doc_id)
            db.audit(
                AuditEntry(
                    step="process",
                    doc_id=doc.doc_id,
                    status="skipped",
                    message="no extraction on unresolved document",
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

        ratings = extract_rating_actions(doc, issuer_id, scales)
        items = extract_edgar_items(doc, issuer_id)
        for skipped in ratings.skipped:
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
        for event in [*ratings.events, *items.events]:
            _record_event(db, doc, event, summary)
        db.mark_processed(doc.doc_id)
        db.audit(
            AuditEntry(
                step="process",
                doc_id=doc.doc_id,
                message=(
                    f"{len(ratings.events) + len(items.events)} event(s), "
                    f"{len(ratings.skipped)} rejected candidate(s)"
                ),
            )
        )
    return summary
