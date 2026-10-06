"""From validated statements to one GuidanceEnrichment, in pure code.

Rules (validated 2026-10-06):
- statements on the same (metric, period) must agree after unit normalisation, otherwise the
  metric is in conflict and contributes nothing decisional;
- the governing metric among the cuts in the scope of ERN-02/03 (revenue, ebitda, fcf) is
  chosen by the rule it would trigger (P1 before P2), then by the larger comparable
  magnitude, then by the fixed order revenue, ebitda, fcf;
- a cut whose magnitude is not comparable to the percent threshold (qualitative wording or a
  growth-rate range in points) keeps guidance_change_pct null and lets ERN-03 apply;
- reaffirmed with no cut gives guidance_status reaffirmed (ERN-04); raised alone gives no
  status; every statement is kept in fields.llm_guidance for the audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from radar.config import Rules
from radar.llm.compute import compute_guidance_change
from radar.llm.schemas import GuidanceStatement

SCOPED_ORDER = {"revenue": 0, "ebitda": 1, "fcf": 2}
RULE_RANK = {"ERN-02": 2, "ERN-03": 1}


@dataclass(frozen=True)
class Candidate:
    statement_id: str
    llm_call_id: int | None
    doc_id: str
    metric: str
    period: str | None
    status: str
    basis: str
    unit: str
    previous_lower: float | None
    previous_upper: float | None
    current_lower: float | None
    current_upper: float | None
    midpoint_previous: float | None
    midpoint_current: float | None
    delta_percent: float | None
    delta_points: float | None
    change_basis: str
    matched_start: int | None
    matched_end: int | None
    match_score: float
    rule_simulated: str | None = None
    equivalent_ids: tuple[str, ...] = ()


@dataclass
class GuidanceEnrichment:
    statements: list[dict[str, Any]]
    conflicts: list[tuple[str, str | None, list[str]]]
    governing: Candidate | None
    guidance_status: str | None
    decisional_fields: dict[str, Any]
    applied_statement_ids: list[str]
    notes: list[str] = field(default_factory=list)


def _scale(unit: str) -> float:
    return 1000.0 if unit.endswith("_BN") else 1.0


def _normalised_key(c: Candidate) -> tuple:
    def n(v: float | None) -> float | None:
        return None if v is None else round(v * _scale(c.unit), 6)

    currency = c.unit.split("_")[0] if c.basis == "absolute" else "PCT"
    return (
        c.status,
        c.basis,
        currency,
        n(c.previous_lower),
        n(c.previous_upper),
        n(c.current_lower),
        n(c.current_upper),
    )


def candidate_from_row(row: dict[str, Any]) -> Candidate:
    st = GuidanceStatement.model_validate(row["statement_json"])
    change = compute_guidance_change(st)
    validation = row.get("validation_json") or {}
    points = None
    if st.basis == "yoy_change_pct" and change.midpoint_previous is not None:
        if change.midpoint_current is not None:
            points = change.midpoint_current - change.midpoint_previous
    return Candidate(
        statement_id=row["statement_id"],
        llm_call_id=row.get("llm_call_id"),
        doc_id=row["doc_id"],
        metric=st.metric,
        period=st.period,
        status=st.status,
        basis=st.basis,
        unit=st.unit,
        previous_lower=st.previous_lower,
        previous_upper=st.previous_upper,
        current_lower=st.current_lower,
        current_upper=st.current_upper,
        midpoint_previous=change.midpoint_previous,
        midpoint_current=change.midpoint_current,
        delta_percent=change.delta_percent if st.basis == "absolute" else None,
        delta_points=points,
        change_basis=change.change_basis,
        matched_start=validation.get("matched_start"),
        matched_end=validation.get("matched_end"),
        match_score=float(validation.get("match_score") or 0.0),
    )


def simulate_rule(c: Candidate, threshold_pct: float) -> str | None:
    """The ERN rule a cut would trigger on its own, or None when the status is not a cut,
    the metric is outside the rule's scope, or the numbers contradict the cut."""
    if c.status != "cut" or c.metric not in SCOPED_ORDER:
        return None
    if c.basis == "absolute" and c.delta_percent is not None:
        if c.delta_percent <= -threshold_pct:
            return "ERN-02"
        if c.delta_percent < 0:
            return "ERN-03"
        return None  # a cut whose figures do not decrease: inconsistent, left to the audit
    return "ERN-03"  # qualitative cut or growth-rate range in points


def _summary(c: Candidate) -> dict[str, Any]:
    return {
        "statement_id": c.statement_id,
        "metric": c.metric,
        "period": c.period,
        "status": c.status,
        "basis": c.basis,
        "unit": c.unit,
        "previous_lower": c.previous_lower,
        "previous_upper": c.previous_upper,
        "current_lower": c.current_lower,
        "current_upper": c.current_upper,
        "change_basis": c.change_basis,
        "delta_percent": c.delta_percent,
        "delta_points": c.delta_points,
        "rule_simulated": c.rule_simulated,
    }


def build_enrichment(rows: list[dict[str, Any]], rules: Rules) -> GuidanceEnrichment:
    threshold = float(rules.thresholds.guidance_cut_p1_pct)
    candidates = sorted(
        (candidate_from_row(r) for r in rows if r.get("validation_status", "VALID") == "VALID"),
        key=lambda c: c.statement_id,
    )
    groups: dict[tuple[str, str | None], list[Candidate]] = {}
    for c in candidates:
        groups.setdefault((c.metric, c.period), []).append(c)
    conflicts: list[tuple[str, str | None, list[str]]] = []
    representatives: list[Candidate] = []
    notes: list[str] = []
    for (metric, period), members in groups.items():
        keys = {_normalised_key(m) for m in members}
        if len(keys) > 1:
            conflicts.append((metric, period, [m.statement_id for m in members]))
            notes.append(f"conflicting statements on {metric} {period}: no decisional use")
            continue
        head = members[0]
        rule = simulate_rule(head, threshold)
        representatives.append(
            Candidate(
                **{**head.__dict__, "rule_simulated": rule},
                equivalent_ids=tuple(m.statement_id for m in members),
            )
            if False
            else _with(head, rule, tuple(m.statement_id for m in members))
        )
    cuts = [r for r in representatives if r.rule_simulated is not None]
    governing = None
    if cuts:
        governing = max(
            cuts,
            key=lambda c: (
                RULE_RANK[c.rule_simulated or ""],
                abs(c.delta_percent) if c.delta_percent is not None else -1.0,
                -SCOPED_ORDER[c.metric],
            ),
        )
    statuses = {r.status for r in representatives}
    if "cut" in statuses:
        guidance_status: str | None = "cut"
    elif "withdrawn" in statuses:
        guidance_status = "withdrawn"
    elif "reaffirmed" in statuses:
        guidance_status = "reaffirmed"
    else:
        guidance_status = None  # raised, new or mentioned alone decide nothing
    decisional: dict[str, Any] = {
        "guidance_status": guidance_status,
        "guidance_metric": None,
        "guidance_old": None,
        "guidance_new": None,
        "guidance_change_pct": None,
        "guidance_change_points": None,
    }
    applied: list[str] = []
    carrier: Candidate | None = None
    if governing is not None:
        carrier = governing
    elif guidance_status in ("cut", "withdrawn"):
        carrier = sorted(
            (r for r in representatives if r.status == guidance_status),
            key=lambda c: (SCOPED_ORDER.get(c.metric, 9), c.metric),
        )[0]
    elif guidance_status == "reaffirmed":
        carrier = sorted(
            (r for r in representatives if r.status == "reaffirmed"),
            key=lambda c: (SCOPED_ORDER.get(c.metric, 9), c.metric),
        )[0]
    if carrier is not None:
        decisional["guidance_metric"] = carrier.metric
        if carrier.status in ("cut", "withdrawn"):
            decisional["guidance_old"] = carrier.midpoint_previous
            decisional["guidance_new"] = carrier.midpoint_current
            if carrier.basis == "absolute" and carrier.change_basis == "quantitative":
                decisional["guidance_change_pct"] = carrier.delta_percent
            if carrier.basis == "yoy_change_pct":
                decisional["guidance_change_points"] = carrier.delta_points
        applied = list(carrier.equivalent_ids)
    return GuidanceEnrichment(
        statements=[_summary(r) for r in representatives]
        + [
            {**_summary(c), "conflict": True}
            for c in candidates
            if any(c.statement_id in ids for _, _, ids in conflicts)
        ],
        conflicts=conflicts,
        governing=governing,
        guidance_status=guidance_status,
        decisional_fields=decisional,
        applied_statement_ids=applied,
        notes=notes,
    )


def _with(c: Candidate, rule: str | None, ids: tuple[str, ...]) -> Candidate:
    return Candidate(**{**c.__dict__, "rule_simulated": rule, "equivalent_ids": ids})
