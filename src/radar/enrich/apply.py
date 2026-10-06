"""`radar llm-apply`: validated statements enrich an earnings_release event, the engine
decides again, the priority change is audited before and after."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from radar.audit import AuditEntry, stable_hash
from radar.config import Rules
from radar.db import Database
from radar.enrich.guidance import GuidanceEnrichment, build_enrichment
from radar.enrich.liquidity import LiquidityEnrichment, build_liquidity_enrichment
from radar.models import EvidenceSpan
from radar.pipeline import decide_event
from radar.ratings import RatingScales

ENRICHMENT_VERSION = "guidance-enrichment-1.0"
DECISIONAL_FIELDS = (
    "guidance_status",
    "guidance_metric",
    "guidance_old",
    "guidance_new",
    "guidance_change_pct",
    "guidance_change_points",
)


@dataclass
class ApplyResult:
    event_id: str
    status: str  # applied | already_applied | same_semantic_enrichment | no_valid_statement
    priority_before: str | None = None
    priority_after: str | None = None
    enrichment_id: str | None = None
    applied_statement_ids: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


def apply_event(
    db: Database,
    event_id: str,
    rules: Rules,
    scales: RatingScales,
    *,
    version: str = ENRICHMENT_VERSION,
) -> ApplyResult:
    event = db.get_event(event_id)
    if event is None:
        raise KeyError(event_id)
    rows = [r for d in event.source_doc_ids for r in db.statements_for_document(d)]
    by_kind: dict[str, list[dict[str, Any]]] = {"guidance": [], "liquidity": []}
    for r in rows:
        by_kind.setdefault(r.get("statement_kind") or "guidance", []).append(r)
    valid_guidance = [r for r in by_kind["guidance"] if r["validation_status"] == "VALID"]
    valid_liquidity = [r for r in by_kind["liquidity"] if r["validation_status"] == "VALID"]
    valid = valid_guidance + valid_liquidity
    before = db.priority_of(event_id)
    priority_before = before[0] if before else None
    if not valid:
        db.audit(
            AuditEntry(
                step="llm_apply",
                event_id=event_id,
                status="skipped",
                message=f"no valid statement ({len(rows)} stored), event untouched",
            )
        )
        return ApplyResult(event_id, "no_valid_statement", priority_before, priority_before)
    enrichment = build_enrichment(valid_guidance, rules) if valid_guidance else None
    liquidity = build_liquidity_enrichment(valid_liquidity) if valid_liquidity else None
    statement_ids = sorted(r["statement_id"] for r in valid)
    enrichment_id = stable_hash({"event_id": event_id, "statements": statement_ids, "v": version})
    if db.enrichment_exists(enrichment_id):
        db.audit(
            AuditEntry(
                step="llm_apply",
                event_id=event_id,
                status="skipped",
                message=f"already applied (enrichment {enrichment_id[:12]}), nothing changed",
            )
        )
        return ApplyResult(
            event_id, "already_applied", priority_before, priority_before, enrichment_id
        )
    payload_hash = stable_hash(
        {
            "fields": enrichment.decisional_fields if enrichment else None,
            "conflicts": enrichment.conflicts if enrichment else [],
            "liquidity_flag": liquidity.flag if liquidity else None,
            "v": version,
        }
    )
    earlier = db.enrichment_with_payload(event_id, payload_hash)
    if earlier is not None:
        db.audit(
            AuditEntry(
                step="llm_apply",
                event_id=event_id,
                status="skipped",
                message=f"same semantic enrichment as {earlier[:12]} with other statement ids, no material change",  # noqa: E501
            )
        )
        return ApplyResult(
            event_id, "same_semantic_enrichment", priority_before, priority_before, earlier
        )

    fields_before = dict(event.fields)
    fields_after = dict(fields_before)
    conflicts: list[dict[str, Any]] = []
    applied: list[str] = []
    by_id = {r["statement_id"]: r for r in valid}
    if enrichment is not None:
        fields_after, g_conflicts, g_applied = _merge(fields_after, enrichment)
        conflicts += g_conflicts
        applied += g_applied
        fields_after["llm_guidance"] = enrichment.statements
        fields_after["guidance_source"] = _source(valid_guidance, g_applied, version) | {
            "conflicting_statements": [list(ids) for _, _, ids in enrichment.conflicts],
            "notes": enrichment.notes,
        }
        if g_conflicts:
            fields_after["llm_guidance_conflicts"] = g_conflicts
    if liquidity is not None:
        fields_after, l_conflicts, l_applied = _merge_liquidity(fields_after, liquidity)
        conflicts += l_conflicts
        applied += l_applied
        fields_after["llm_liquidity"] = liquidity.statements
        fields_after["liquidity_source"] = _source(valid_liquidity, l_applied, version)
        if l_conflicts:
            fields_after["llm_liquidity_conflicts"] = l_conflicts
    spans = _spans(db, [by_id[s] for s in applied])
    db.update_event_enrichment(event_id, fields_after, spans, "llm_validated")
    updated = db.get_event(event_id)
    assert updated is not None
    decision = decide_event(db, updated, rules, scales)
    db.upsert_decision(decision)
    priority_after = decision.final_priority
    db.insert_enrichment(
        {
            "enrichment_id": enrichment_id,
            "event_id": event_id,
            "enrichment_version": version,
            "statement_ids": statement_ids,
            "payload_hash": payload_hash,
            "fields_before": fields_before,
            "fields_after": fields_after,
            "priority_before": priority_before,
            "priority_after": priority_after,
        }
    )
    db.audit(
        AuditEntry(
            step="llm_apply",
            event_id=event_id,
            doc_id=event.source_doc_ids[0] if event.source_doc_ids else None,
            status="ok",
            message=(
                f"enrichment {enrichment_id[:12]}: {len(applied)} statement(s) applied, "
                f"{len(conflicts)} deterministic conflict(s), priority before {priority_before} "
                f"after {priority_after} ({decision.decision_status}), "
                f"rules {decision.rules_version}"
            ),
        )
    )
    return ApplyResult(
        event_id, "applied", priority_before, priority_after, enrichment_id, applied, conflicts
    )


def _source(rows: list[dict[str, Any]], applied: list[str], version: str) -> dict[str, Any]:
    by_id = {r["statement_id"]: r for r in rows}
    return {
        "method": "llm_validated",
        "enrichment_version": version,
        "statement_ids": applied,
        "llm_call_ids": sorted(
            {by_id[s]["llm_call_id"] for s in applied if s in by_id and by_id[s]["llm_call_id"]}
        ),
        "model_id": _one({r["model_id"] for r in rows}),
        "resolved_model": _one({r["resolved_model"] for r in rows}),
        "prompt_version": _one({r["prompt_version"] for r in rows}),
        "schema_version": _one({r["schema_version"] for r in rows}),
    }


def _merge_liquidity(
    fields: dict[str, Any], liquidity: LiquidityEnrichment
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """The code alone sets the flag: a VALID negative statement adds "liquidity" to the
    flags; a deterministic flag is never removed, a disagreement is recorded."""
    after = dict(fields)
    flags = list(after.get("flags") or [])
    conflicts: list[dict[str, Any]] = []
    if liquidity.flag:
        if "liquidity" not in flags:
            flags.append("liquidity")
        after["flags"] = flags
        return after, conflicts, list(liquidity.negative_ids)
    if "liquidity" in flags:
        conflicts.append(
            {
                "field": "flags:liquidity",
                "deterministic": True,
                "llm": liquidity.statuses[0] if liquidity.statuses else None,
            }
        )
    return after, conflicts, []


def apply_all(
    db: Database, rules: Rules, scales: RatingScales, issuer_id: str | None = None
) -> list[ApplyResult]:
    out = []
    for event in db.list_events(issuer_id):
        if event.family == "earnings" and event.event_type == "earnings_release":
            out.append(apply_event(db, event.event_id, rules, scales))
    return out


def _merge(
    before: dict[str, Any], enrichment: GuidanceEnrichment
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Fill only empty deterministic fields. A single disagreement with a deterministic
    value keeps the whole deterministic reading and records the conflict."""
    conflicts = []
    for key in DECISIONAL_FIELDS:
        llm_value = enrichment.decisional_fields.get(key)
        existing = before.get(key)
        if llm_value is not None and existing is not None and existing != llm_value:
            conflicts.append({"field": key, "deterministic": existing, "llm": llm_value})
    after = dict(before)
    if conflicts:
        return after, conflicts, []
    for key in DECISIONAL_FIELDS:
        llm_value = enrichment.decisional_fields.get(key)
        if llm_value is not None and before.get(key) is None:
            after[key] = llm_value
    return after, conflicts, list(enrichment.applied_statement_ids)


def _spans(db: Database, rows: list[dict[str, Any]]) -> list[EvidenceSpan]:
    spans = []
    for row in rows:
        v = row["validation_json"]
        start, end = v.get("matched_start"), v.get("matched_end")
        doc = db.get_document(row["doc_id"])
        if start is None or end is None or doc is None:
            continue
        spans.append(
            EvidenceSpan(
                doc_id=row["doc_id"],
                char_start=start,
                char_end=end,
                quote=doc.text[start:end],  # the document's own passage, never the model's text
                evidence_type="llm_statement",
                extractor_version=f"{row['model_id']}/{row['prompt_version']}",
                match_score=float(v.get("match_score") or 0.0),
                field=(
                    "flag:liquidity"
                    if (row.get("statement_kind") or "guidance") == "liquidity"
                    else f"guidance:{row['statement_json'].get('metric')}"
                ),
            )
        )
    return spans


def _one(values: set) -> Any:
    values = {v for v in values if v is not None}
    return next(iter(values)) if len(values) == 1 else (sorted(values) if values else None)
