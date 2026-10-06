"""Text rendering of a Decision: the "Why this priority?" panel (docs/SPEC.md 9.5)."""

from __future__ import annotations

from radar.materiality.engine import Decision
from radar.models import CreditEvent, RawDocument

AGENCY_DISPLAY = {
    "SP": "S&P Global Ratings",
    "MOODYS": "Moody's",
    "FITCH": "Fitch",
    "DBRS": "Morningstar DBRS",
}


def _h(title: str) -> list[str]:
    return [title, "-" * len(title)]


def render_explanation(
    decision: Decision, event: CreditEvent, documents: dict[str, RawDocument]
) -> str:
    lines: list[str] = []
    final = decision.final_priority or "NONE"
    lines.append(f"FINAL PRIORITY: {final}")
    lines.append(f"Base priority: {decision.base_priority or 'NONE'}")
    lines.append(f"Decision status: {decision.decision_status}")
    when = decision.effective_date or "unknown"
    lines.append(f"Event: {event.issuer_id} {event.family}/{event.event_type} effective {when}")
    lines.append(f"Rules version: {decision.rules_version}")
    lines.append("")

    lines += _h("Triggered")
    triggered = [r for r in decision.rules if r.triggered]
    if not triggered:
        lines.append("(none)")
    for r in triggered:
        lines.append(f"{r.id}  TRUE  [{r.priority}]")
        lines.append(f"Reason: {r.reason}")
    lines.append("")

    lines += _h("Not triggered")
    for r in decision.rules:
        if not r.triggered:
            lines.append(f"{r.id}  FALSE  {r.reason}")
    lines.append("")

    lines += _h("Modifiers")
    for m in decision.modifiers:
        status = "APPLIED" if m.applied else "NOT APPLIED"
        lines.append(f"{m.id}  {status}  ({m.effect}): {m.reason}")
    lines.append("")

    state = decision.state_after
    if state is not None:
        lines += _h(f"Rating state at {state.as_of.isoformat()} (after action)")
        if not state.entries:
            lines.append("(no admissible rating known)")
        for entry in state.entries.values():
            lines.append(AGENCY_DISPLAY.get(entry.agency, entry.agency))
            extra = []
            if entry.outlook:
                extra.append(f"outlook {entry.outlook}")
            if entry.watch and entry.watch != "none":
                extra.append(f"watch {entry.watch}")
            lines.append(
                f"  {entry.rating}  {entry.category}" + (f"  ({', '.join(extra)})" if extra else "")
            )
            if entry.as_of_basis == "retrieval":
                basis = "retrieval: observed on the source that day, prospective use only"
            else:
                basis = "stated by the source"
            lines.append(f"  as_of: {entry.as_of.isoformat()} ({basis})")
            lines.append(f"  age: {entry.age_days} days")
            lines.append(f"  source: {entry.source} ({entry.origin}, {entry.verification})")
        if state.ignored:
            lines.append("Ignored:")
            for ig in state.ignored:
                when = f" as_of {ig.as_of.isoformat()}" if ig.as_of else ""
                name = AGENCY_DISPLAY.get(ig.agency, ig.agency)
                lines.append(f"  {name} {ig.rating}{when}  IGNORED  reason: {ig.reason}")
        lines.append("")

    lines += _h("Decision provenance")
    detectors = sorted(
        {s.extractor_version for s in event.evidence if s.evidence_type != "llm_statement"}
    )
    lines.append(
        f"Detected by: deterministic extractor ({event.extraction_method}"
        + (f", {', '.join(detectors)}" if detectors else "")
        + ")"
    )
    source = event.fields.get("guidance_source") if isinstance(event.fields, dict) else None
    if event.enrichment_method == "llm_validated" and isinstance(source, dict):
        n = len(source.get("statement_ids") or [])
        recorded = len(event.fields.get("llm_guidance") or [])
        lines.append(
            f"Enriched by: validated LLM statements ({source.get('model_id')}, prompt "
            f"{source.get('prompt_version')}, schema {source.get('schema_version')}, "
            f"{n} statement{'s' if n != 1 else ''} applied, {recorded} recorded)"
        )
        conflicts = event.fields.get("llm_guidance_conflicts") or []
        if conflicts:
            lines.append(
                "Enrichment conflicts: deterministic reading kept for "
                + ", ".join(
                    f"{c['field']} ({c['deterministic']} vs LLM {c['llm']})" for c in conflicts
                )
            )
    else:
        lines.append("Enriched by: none (deterministic fields only)")
    lines.append(
        f"Agency ratings used: {'YES' if decision.provenance.agency_ratings_used else 'NO'}"
    )
    lines.append("Composite rating used: NO")
    llm = decision.provenance.llm_used
    lines.append(f"LLM used: {'NO' if llm == 'none' else llm}")
    lines.append(
        f"Priority decided by: deterministic rules engine (rules.yaml {decision.rules_version})"
    )
    lines.append("")

    lines += _h("Evidence")
    for doc_id in event.source_doc_ids:
        doc = documents.get(doc_id)
        if doc is None:
            lines.append(f"{doc_id} (document not available)")
            continue
        lines.append(doc.title or doc_id)
        lines.append(str(doc.url))
        published = doc.published_at.date().isoformat() if doc.published_at else "unknown"
        lines.append(f"published {published}, retrieved {doc.retrieved_at.isoformat()}")
        lines.append(f"raw sha256 {doc.content_hash} ({doc.raw_size_bytes} bytes)")
        lines.append(f"normalised sha256 {doc.doc_id} ({doc.normalizer_version})")
    if event.evidence:
        lines.append("Offsets:")
        for span in event.evidence:
            if span.field:
                lines.append(f"  [{span.field}] {span.char_start}-{span.char_end}: {span.quote}")
            else:
                lines.append(f"  [{span.evidence_type}] {span.char_start}-{span.char_end}")
    return "\n".join(lines) + "\n"
