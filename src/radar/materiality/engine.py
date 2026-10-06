"""Deterministic materiality engine (docs/SPEC.md section 9, config/rules.yaml).

Pure functions: an event, a rating state at D, an optional prior context, the rules and
the scales go in; a fully explained Decision comes out. No I/O, no LLM. Every rule and
modifier is evaluated and reported, triggered or not. Categories and notches are always
recomputed from the rating labels and the scale, never read from the event's extras.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from radar.config import Rule, RuleDirection, Rules
from radar.materiality.state import RatingState
from radar.models import CreditEvent, DecisionStatus, Priority, PriorityDecision
from radar.ratings import RatingScales, category, is_at_boundary, notch_delta, to_notch

PRIORITY_RANK: dict[str, int] = {"P3": 1, "P2": 2, "P1": 3}
RANK_PRIORITY: dict[int, Priority] = {1: "P3", 2: "P2", 3: "P1"}
SCOPED_GUIDANCE_METRICS = {
    "revenue": "revenue", "revenues": "revenue", "sales": "revenue", "net sales": "revenue",
    "net revenue": "revenue", "ebitda": "ebitda", "adjusted ebitda": "ebitda", "fcf": "fcf",
    "free cash flow": "fcf", "free_cash_flow": "fcf",
}  # fmt: skip
CRITICAL_FLAGS = {"liquidity", "going_concern", "covenant"}
SUBORDINATED = {"subordinated", "hybrid", "AT1", "T2"}
ISSUANCE_TYPES = {"new_issue", "tap"}
LLM_ROLE_BY_METHOD = {
    "structured": "none",
    "llm_validated": "field extraction (validated against source text)",
}


class PriorEvent(BaseModel):
    event_id: str
    effective_date: date
    base_priority: Priority | None
    negative: bool


class Fundamentals(BaseModel):
    leverage: float
    as_of: date
    source: str
    verified: bool


class PriorContext(BaseModel):
    prior_events: list[PriorEvent] = Field(default_factory=list)
    fundamentals: Fundamentals | None = None


class RuleOutcome(BaseModel):
    id: str
    priority: Priority
    direction: RuleDirection
    triggered: bool
    reason: str
    data: dict[str, Any] = Field(default_factory=dict)


class ModifierOutcome(BaseModel):
    id: str
    effect: str
    applied: bool
    reason: str
    data: dict[str, Any] = Field(default_factory=dict)


class Provenance(BaseModel):
    agency_ratings_used: bool
    composite_used: bool = False
    llm_used: str
    extraction_method: str


class Decision(BaseModel):
    event_id: str
    issuer_id: str
    effective_date: date | None
    base_priority: Priority | None
    final_priority: Priority | None
    decision_status: DecisionStatus
    negative: bool
    rules: list[RuleOutcome]
    modifiers: list[ModifierOutcome]
    state_before: RatingState | None
    state_after: RatingState | None
    provenance: Provenance
    rules_version: str

    def triggered_ids(self) -> list[str]:
        return [r.id for r in self.rules if r.triggered] + [
            m.id for m in self.modifiers if m.applied
        ]

    def to_priority_decision(self) -> PriorityDecision:
        details = [f"{r.id}: {r.reason}" for r in self.rules if r.triggered]
        details += [f"{m.id}: {m.reason}" for m in self.modifiers if m.applied]
        return PriorityDecision(
            event_id=self.event_id,
            priority=self.final_priority,
            decision_status=self.decision_status,
            base_priority=self.base_priority,
            triggered_rules=self.triggered_ids(),
            rule_details=details,
            rules_version=self.rules_version,
            llm_role=self.provenance.llm_used,
        )


class _Context:
    """Everything a rule function may look at, computed once per evaluation."""

    def __init__(
        self,
        event: CreditEvent,
        state_before: RatingState | None,
        prior: PriorContext,
        rules: Rules,
        scales: RatingScales,
    ) -> None:
        self.event = event
        self.fields = event.fields
        self.family = event.family
        self.event_type = event.event_type
        self.prior = prior
        self.rules = rules
        self.scales = scales
        self.agency: str | None = self.fields.get("agency") if event.family == "rating" else None
        self.effective_date: date | None = event.effective_date or (
            state_before.as_of if state_before else None
        )
        self.label_error: str | None = None
        self.old_label = self.new_label = self.current_label = None
        self.old_notch = self.new_notch = None
        if self.agency:
            self.old_label, err_old = self._canonical(self.fields.get("old_rating"))
            self.new_label, err_new = self._canonical(self.fields.get("new_rating"))
            self.current_label, err_cur = self._canonical(self.fields.get("rating"))
            self.label_error = err_old or err_new or err_cur
            if self.old_label:
                self.old_notch = to_notch(self.agency, self.old_label, scales)
            if self.new_label:
                self.new_notch = to_notch(self.agency, self.new_label, scales)
        self.state_before = state_before
        self.state_after = self._state_after()

    def _canonical(self, raw: Any) -> tuple[str | None, str | None]:
        if raw is None or self.agency is None:
            return None, None
        if self.agency not in self.scales.agencies:
            return None, f"unknown agency {self.agency!r}"
        label = self.scales.agency(self.agency).canonical_label(str(raw))
        if label is None:
            return None, f"unknown rating label {raw!r} for {self.agency}"
        return label, None

    def _state_after(self) -> RatingState | None:
        if self.state_before is None or not self.agency or not self.new_label:
            return self.state_before
        return self.state_before.after_action(
            self.agency,
            self.new_label,
            self.scales,
            outlook=self.fields.get("new_outlook"),
            watch=self.fields.get("watch") or "none",
        )

    # ---- helpers ---------------------------------------------------------- #

    def agency_name(self) -> str:
        if self.agency and self.agency in self.scales.agencies:
            return self.scales.agencies[self.agency].display_name
        return str(self.agency)

    def cat(self, notch: int) -> str:
        return category(notch, self.scales)

    def transition(self) -> bool:
        return self.old_notch is not None and self.new_notch is not None

    def delta(self) -> int:
        assert self.old_notch is not None and self.new_notch is not None
        return notch_delta(old_notch=self.old_notch, new_notch=self.new_notch)

    def crosses_ig_to_hy(self) -> bool:
        return (
            self.transition()
            and self.cat(self.old_notch) == "IG"
            and self.cat(self.new_notch) == "HY"
        )  # type: ignore[arg-type]

    def rating_after_action(self) -> tuple[str | None, int | None, str]:
        """Acting agency's rating after the action: new_rating, else rating, else state."""
        if self.new_label and self.new_notch is not None:
            return self.new_label, self.new_notch, "event new_rating"
        if self.current_label:
            return (
                self.current_label,
                to_notch(self.agency, self.current_label, self.scales),
                "event rating",
            )  # type: ignore[arg-type]
        if self.state_after and self.agency in self.state_after.entries:
            entry = self.state_after.entries[self.agency]
            return entry.rating, entry.notch, f"rating state as_of {entry.as_of.isoformat()}"
        return None, None, "rating after action unknown"

    def previous_outlook(self) -> tuple[str | None, str]:
        old = self.fields.get("old_outlook")
        if old:
            return str(old), "event old_outlook"
        if self.state_before and self.agency in self.state_before.entries:
            entry = self.state_before.entries[self.agency]
            if entry.outlook:
                return entry.outlook, f"rating state as_of {entry.as_of.isoformat()}"
        return None, "previous outlook unknown"

    def guidance(self) -> tuple[str | None, float | None]:
        raw_metric = self.fields.get("guidance_metric")
        metric = (
            SCOPED_GUIDANCE_METRICS.get(str(raw_metric).strip().casefold()) if raw_metric else None
        )
        pct = self.fields.get("guidance_change_pct")
        if pct is None:
            old, new = self.fields.get("guidance_old"), self.fields.get("guidance_new")
            if isinstance(old, int | float) and isinstance(new, int | float) and old:
                pct = (new - old) / abs(old) * 100.0
        return metric, (float(pct) if pct is not None else None)

    def flags(self) -> set[str]:
        return {str(f) for f in (self.fields.get("flags") or [])}

    def rule_bool(self, condition: str) -> bool:
        return RULES[condition](self)[0]


Outcome = tuple[bool, str, dict[str, Any]]
RuleFn = Callable[[_Context], Outcome]
RULES: dict[str, RuleFn] = {}
MODIFIERS: dict[str, Callable[[_Context, Priority, bool], Outcome]] = {}


def rule(condition: str) -> Callable[[RuleFn], RuleFn]:
    def register(fn: RuleFn) -> RuleFn:
        RULES[condition] = fn
        return fn

    return register


def modifier(condition: str):
    def register(fn):
        MODIFIERS[condition] = fn
        return fn

    return register


# --------------------------------------------------------------- ratings --- #


def _downgrade_guard(ctx: _Context) -> str | None:
    if ctx.family != "rating" or ctx.event_type != "downgrade":
        return "not a rating downgrade"
    if ctx.label_error:
        return ctx.label_error
    if not ctx.transition():
        return "old_rating or new_rating missing"
    return None


@rule("agency_rating_crosses_ig_to_hy")
def rat01(ctx: _Context) -> Outcome:
    if guard := _downgrade_guard(ctx):
        return False, guard, {}
    move = f"{ctx.agency_name()} {ctx.old_label} -> {ctx.new_label}"
    cats = f"{ctx.cat(ctx.old_notch)} -> {ctx.cat(ctx.new_notch)}"  # type: ignore[arg-type]
    data = {
        "agency": ctx.agency,
        "old_rating": ctx.old_label,
        "new_rating": ctx.new_label,
        "old_category": ctx.cat(ctx.old_notch),
        "new_category": ctx.cat(ctx.new_notch),
    }  # type: ignore[arg-type]
    if ctx.crosses_ig_to_hy():
        return True, f"{move}, {cats} (fallen angel at agency level)", data
    return False, f"{move} stays {cats}", data


@rule("agency_rating_crosses_to_hy_while_another_agency_stays_ig")
def rat02(ctx: _Context) -> Outcome:
    if guard := _downgrade_guard(ctx):
        return False, guard, {}
    if not ctx.crosses_ig_to_hy():
        return False, f"{ctx.agency_name()} does not cross IG to HY", {}
    state = ctx.state_after
    others = [e for a, e in (state.entries.items() if state else []) if a != ctx.agency]
    if not others:
        return False, "no other admissible agency rating known at D", {"other_ig_agencies": []}
    ig = sorted(e.agency for e in others if e.category == "IG")
    listing = ", ".join(
        f"{e.agency} {e.rating} {e.category} (as_of {e.as_of.isoformat()})"
        for e in sorted(others, key=lambda e: e.agency)
    )
    data = {"other_ig_agencies": ig, "others": [e.model_dump(mode="json") for e in others]}
    if ig:
        return (
            True,
            f"split rating after action: {ctx.agency} {ctx.new_label} HY while {listing}",
            data,
        )
    return False, f"other admissible ratings at D are all HY: {listing}", data


@rule("downgrade_two_or_more_notches")
def rat03(ctx: _Context) -> Outcome:
    if guard := _downgrade_guard(ctx):
        return False, guard, {}
    d = ctx.delta()
    data = {"notch_delta": d}
    if d >= 2:
        return (
            True,
            f"{ctx.agency_name()} {ctx.old_label} -> {ctx.new_label}: {d} notches in one action",
            data,
        )
    return False, f"{d} notch only", data


@rule("negative_watch_at_boundary")
def rat04(ctx: _Context) -> Outcome:
    if ctx.family != "rating" or ctx.fields.get("watch") != "negative":
        return False, "no negative watch", {}
    if ctx.label_error:
        return False, ctx.label_error, {}
    label, notch, origin = ctx.rating_after_action()
    if notch is None:
        return False, origin, {}
    data = {"rating_after_action": label, "notch": notch, "origin": origin}
    if is_at_boundary(notch, ctx.scales):
        return (
            True,
            f"negative watch with {ctx.agency_name()} rating at {label} (last IG notch, {origin})",
            data,
        )
    return (
        False,
        f"negative watch but rating after action is {label} (notch {notch}, not the boundary)",
        data,
    )


@rule("downgrade_one_notch")
def rat05(ctx: _Context) -> Outcome:
    if guard := _downgrade_guard(ctx):
        return False, guard, {}
    d = ctx.delta()
    if d == 1 and not ctx.crosses_ig_to_hy():
        return (
            True,
            f"{ctx.agency_name()} {ctx.old_label} -> {ctx.new_label}: one notch, "
            f"stays {ctx.cat(ctx.new_notch)}",
            {"notch_delta": d},
        )  # type: ignore[arg-type]
    if d == 1:
        return False, "one-notch move crosses IG to HY: covered by RAT-01", {"notch_delta": d}
    return False, f"{d} notches", {"notch_delta": d}


@rule("outlook_to_negative")
def rat06(ctx: _Context) -> Outcome:
    if ctx.family != "rating" or ctx.fields.get("new_outlook") != "negative":
        return False, "outlook not revised to negative", {}
    previous, origin = ctx.previous_outlook()
    data = {"previous_outlook": previous, "origin": origin}
    if previous in ("stable", "positive"):
        return True, f"{ctx.agency_name()} outlook {previous} -> negative ({origin})", data
    if previous is None:
        return False, "outlook revised to negative but previous outlook unknown", data
    return False, f"previous outlook already {previous}", data


@rule("negative_watch")
def rat07(ctx: _Context) -> Outcome:
    if ctx.family != "rating" or ctx.fields.get("watch") != "negative":
        return False, "no negative watch", {}
    if ctx.rule_bool("negative_watch_at_boundary"):
        return False, "negative watch at the boundary: covered by RAT-04", {}
    label, _, origin = ctx.rating_after_action()
    where = f" at {label}" if label else ""
    return (
        True,
        f"{ctx.agency_name()} negative watch{where} ({origin})",
        {"rating_after_action": label},
    )


@rule("agency_rating_crosses_hy_to_ig")
def rat08(ctx: _Context) -> Outcome:
    if ctx.family != "rating" or ctx.event_type != "upgrade":
        return False, "not a rating upgrade", {}
    if ctx.label_error:
        return False, ctx.label_error, {}
    if not ctx.transition():
        return False, "old_rating or new_rating missing", {}
    old_c, new_c = ctx.cat(ctx.old_notch), ctx.cat(ctx.new_notch)  # type: ignore[arg-type]
    data = {"old_category": old_c, "new_category": new_c}
    if old_c != "IG" and new_c == "IG":
        return (
            True,
            f"{ctx.agency_name()} {ctx.old_label} -> {ctx.new_label}, {old_c} -> IG "
            "(rising star at agency level)",
            data,
        )
    return False, f"{ctx.old_label} -> {ctx.new_label} stays {old_c} -> {new_c}", data


@rule("upgrade_or_positive_outlook")
def rat09(ctx: _Context) -> Outcome:
    if ctx.family != "rating":
        return False, "not a rating event", {}
    reasons = []
    if (
        ctx.event_type == "upgrade"
        and ctx.transition()
        and not ctx.rule_bool("agency_rating_crosses_hy_to_ig")
    ):
        reasons.append(f"upgrade {ctx.old_label} -> {ctx.new_label} without crossing")
    if ctx.fields.get("new_outlook") == "positive":
        reasons.append("outlook positive")
    if ctx.fields.get("watch") == "positive":
        reasons.append("positive watch")
    if reasons:
        return True, f"{ctx.agency_name()}: " + ", ".join(reasons), {}
    return False, "no upgrade without crossing, no positive outlook or watch", {}


@rule("affirmation")
def rat10(ctx: _Context) -> Outcome:
    if ctx.family != "rating" or ctx.event_type != "affirmation":
        return False, "not an affirmation", {}
    if ctx.fields.get("watch") not in (None, "none"):
        return False, "affirmation with a watch placement", {}
    new_outlook = ctx.fields.get("new_outlook")
    previous, _ = ctx.previous_outlook()
    if new_outlook and previous and new_outlook != previous:
        return False, f"affirmation with outlook change {previous} -> {new_outlook}", {}
    label, _, _ = ctx.rating_after_action()
    return True, f"{ctx.agency_name()} affirmed {label or 'the rating'} without change", {}


# --------------------------------------------------------------- earnings --- #


@rule("liquidity_going_concern_or_covenant_flag")
def ern01(ctx: _Context) -> Outcome:
    if ctx.family != "earnings":
        return False, "not an earnings event", {}
    hits = sorted(ctx.flags() & CRITICAL_FLAGS)
    if hits:
        return True, f"flags {', '.join(hits)} (verified span)", {"flags": hits}
    return False, "no liquidity, going-concern or covenant flag", {}


@rule("guidance_cut_at_or_above_threshold")
def ern02(ctx: _Context) -> Outcome:
    if ctx.family != "earnings":
        return False, "not an earnings event", {}
    metric, pct = ctx.guidance()
    threshold = ctx.rules.thresholds.guidance_cut_p1_pct
    data = {"metric": metric, "guidance_change_pct": pct, "threshold_pct": threshold}
    if ctx.fields.get("guidance_qualified_significant") is True:
        return True, "guidance cut qualified as significant by the issuer", data
    if metric and pct is not None and pct <= -threshold:
        return True, f"{metric} guidance {pct:+.2f} % (threshold {threshold} %)", data
    return False, "no guidance cut at or above the threshold on revenue, EBITDA or FCF", data


@rule("guidance_cut_below_threshold_or_significant_impairment")
def ern03(ctx: _Context) -> Outcome:
    if ctx.family != "earnings":
        return False, "not an earnings event", {}
    metric, pct = ctx.guidance()
    threshold = ctx.rules.thresholds.guidance_cut_p1_pct
    data = {"metric": metric, "guidance_change_pct": pct}
    reasons = []
    if metric and pct is not None and -threshold < pct < 0:
        reasons.append(f"{metric} guidance {pct:+.2f} % (below the {threshold} % threshold)")
    if "impairment" in ctx.flags():
        reasons.append("significant impairment flag")
    if reasons:
        return True, "; ".join(reasons), data
    return False, "no guidance cut below the threshold and no impairment", data


@rule("results_in_line_with_guidance")
def ern04(ctx: _Context) -> Outcome:
    if ctx.family != "earnings":
        return False, "not an earnings event", {}
    status = ctx.fields.get("guidance_status")
    data = {"guidance_status": status}
    if status not in ("reaffirmed", "in_line"):
        if status:
            return False, f"guidance statement is '{status}', not a confirmation", data
        return False, "no explicit guidance statement in the source (ADR-012)", data
    metric, pct = ctx.guidance()
    if metric and pct is not None and pct < 0:
        return False, f"guidance statement contradicted by a {metric} cut of {pct:+.2f} %", data
    if ctx.fields.get("guidance_qualified_significant") is True:
        return False, "issuer qualified the guidance cut as significant", data
    flagged = sorted(ctx.flags() & (CRITICAL_FLAGS | {"impairment"}))
    if flagged:
        return False, f"flags present: {', '.join(flagged)}", data
    return True, f"explicit deterministic evidence: guidance {status} (verified span)", data


# --------------------------------------------------------------- issuance --- #


@rule("issuance_at_or_above_threshold")
def iss01(ctx: _Context) -> Outcome:
    if ctx.family != "issuance" or ctx.event_type not in ISSUANCE_TYPES:
        return False, "not a new issuance or tap", {}
    amount = ctx.fields.get("amount_eur_equiv")
    threshold = ctx.rules.thresholds.issuance_p2_eur
    data = {"amount_eur_equiv": amount, "threshold_eur": threshold}
    if isinstance(amount, int | float) and amount >= threshold:
        return True, f"EUR equivalent {amount:,.0f} >= {threshold:,.0f}", data
    if amount is None:
        return False, "EUR equivalent amount unknown", data
    return False, f"EUR equivalent {amount:,.0f} below {threshold:,.0f}", data


@rule("subordinated_hybrid_at1_or_t2_issuance")
def iss02(ctx: _Context) -> Outcome:
    if ctx.family != "issuance" or ctx.event_type not in ISSUANCE_TYPES:
        return False, "not a new issuance or tap", {}
    seniority = ctx.fields.get("seniority")
    if seniority in SUBORDINATED:
        return True, f"{seniority} issuance", {"seniority": seniority}
    return False, f"seniority {seniority or 'unknown'}", {"seniority": seniority}


@rule("at1_or_hybrid_non_call")
def iss03(ctx: _Context) -> Outcome:
    if ctx.family != "issuance" or ctx.event_type != "non_call":
        return False, "not a non-call event", {}
    seniority = ctx.fields.get("seniority")
    if seniority in ("AT1", "hybrid"):
        return True, f"non-call of a {seniority} instrument", {"seniority": seniority}
    return False, f"non-call of a {seniority or 'unknown'} instrument", {"seniority": seniority}


@rule("issuance_below_threshold_tap_or_routine_refinancing")
def iss04(ctx: _Context) -> Outcome:
    if ctx.family != "issuance" or ctx.event_type not in ISSUANCE_TYPES:
        return False, "not a new issuance or tap", {}
    if ctx.event_type == "tap":
        return True, "explicit tap of an existing line", {}
    if ctx.rule_bool("issuance_at_or_above_threshold") or ctx.rule_bool(
        "subordinated_hybrid_at1_or_t2_issuance"
    ):
        return False, "covered by ISS-01 or ISS-02", {}
    amount = ctx.fields.get("amount_eur_equiv")
    threshold = ctx.rules.thresholds.issuance_p2_eur
    if not isinstance(amount, int | float):
        return False, "EUR-equivalent amount unknown: nothing can be concluded (ADR-012)", {}
    return (
        True,
        f"EUR equivalent {amount:,.0f} below {threshold:,.0f}, not subordinated",
        {"amount_eur_equiv": amount},
    )


# ------------------------------------------------------------------ edgar --- #


@rule("edgar_8k_item_2_04")
def edg01(ctx: _Context) -> Outcome:
    item = ctx.fields.get("edgar_item")
    if item == "2.04":
        return (
            True,
            "8-K Item 2.04: triggering event accelerating a financial obligation",
            {"edgar_item": item},
        )
    return False, f"edgar item {item or 'none'}", {}


# -------------------------------------------------------------- modifiers --- #


@modifier("negative_event_with_weakest_agency_rating_at_boundary")
def mod01(ctx: _Context, base: Priority, negative: bool) -> Outcome:
    if not negative:
        return False, "event is not negative", {}
    state = ctx.state_after
    weakest = state.weakest() if state else None
    if weakest is None:
        return False, "no admissible rating known at D", {}
    data = {"weakest": {"agency": weakest.agency, "rating": weakest.rating, "notch": weakest.notch}}
    if is_at_boundary(weakest.notch, ctx.scales):
        return (
            True,
            f"weakest admissible rating {weakest.agency} {weakest.rating} is at the IG/HY boundary",
            data,
        )
    if weakest.category != "IG":
        return (
            False,
            f"weakest admissible rating {weakest.agency} {weakest.rating} is already HY",
            data,
        )
    return (
        False,
        f"weakest admissible rating {weakest.agency} {weakest.rating} is above the boundary",
        data,
    )


@modifier("two_or_more_negative_p2_events_in_window")
def mod02(ctx: _Context, base: Priority, negative: bool) -> Outcome:
    if not negative or base != "P2":
        return False, "event is not a negative P2 event", {}
    d = ctx.effective_date
    if d is None:
        return False, "no effective date to anchor the window", {}
    window = ctx.rules.thresholds.mod02_window_days
    start = date.fromordinal(d.toordinal() - window)
    matches = [
        p
        for p in ctx.prior.prior_events
        if p.negative
        and p.base_priority == "P2"
        and start <= p.effective_date <= d
        and p.event_id != ctx.event.event_id
    ]
    count = 1 + len(matches)
    data = {
        "count": count,
        "window_days": window,
        "window_start": start.isoformat(),
        "prior_event_ids": [p.event_id for p in matches],
    }
    if count >= ctx.rules.thresholds.mod02_min_events:
        return (
            True,
            f"{count} negative P2 events between {start.isoformat()} and {d.isoformat()} "
            "(effective dates)",
            data,
        )
    return False, f"{count} negative P2 event in the {window}-day window", data


@modifier("verified_leverage_above_threshold")
def mod03(ctx: _Context, base: Priority, negative: bool) -> Outcome:
    threshold = ctx.rules.thresholds.leverage_threshold
    if threshold is None:
        return False, "leverage_threshold not configured", {}
    if not negative:
        return False, "event is not negative", {}
    f = ctx.prior.fundamentals
    if f is None:
        return False, "no sourced leverage figure", {}
    data = {
        "leverage": f.leverage,
        "as_of": f.as_of.isoformat(),
        "source": f.source,
        "threshold": threshold,
    }
    if not f.verified:
        return False, "leverage figure not verified", data
    if ctx.effective_date and f.as_of > ctx.effective_date:
        return False, f"leverage as_of {f.as_of.isoformat()} is after D", data
    if f.leverage > threshold:
        return (
            True,
            f"verified leverage {f.leverage} above {threshold} "
            f"({f.source}, as_of {f.as_of.isoformat()})",
            data,
        )
    return False, f"verified leverage {f.leverage} not above {threshold}", data


# ----------------------------------------------------------------- engine --- #


class MaterialityEngine:
    def __init__(self, rules: Rules, scales: RatingScales) -> None:
        self.rules = rules
        self.scales = scales
        missing = [r.condition for r in rules.rules if r.condition not in RULES]
        missing += [m.condition for m in rules.modifiers if m.condition not in MODIFIERS]
        if missing:
            raise ValueError(f"rules.yaml conditions without implementation: {missing}")

    @property
    def conditions(self) -> list[str]:
        return [*RULES, *MODIFIERS]

    def evaluate(
        self, event: CreditEvent, state: RatingState | None, prior: PriorContext | None = None
    ) -> Decision:
        ctx = _Context(event, state, prior or PriorContext(), self.rules, self.scales)
        outcomes: list[RuleOutcome] = []
        for r in self.rules.rules:
            triggered, reason, data = RULES[r.condition](ctx)
            outcomes.append(
                RuleOutcome(
                    id=r.id,
                    priority=r.priority,
                    direction=r.direction,
                    triggered=triggered,
                    reason=reason,
                    data=data,
                )
            )
        triggered_rules: list[tuple[Rule, RuleOutcome]] = [
            (r, o) for r, o in zip(self.rules.rules, outcomes, strict=True) if o.triggered
        ]
        negative = any(r.direction == "negative" for r, _ in triggered_rules)
        base_rank = max((PRIORITY_RANK[r.priority] for r, _ in triggered_rules), default=0)
        base: Priority | None = RANK_PRIORITY.get(base_rank)

        modifiers: list[ModifierOutcome] = []
        final_rank = base_rank
        for m in self.rules.modifiers:
            if base is None:
                modifiers.append(
                    ModifierOutcome(
                        id=m.id, effect=m.effect, applied=False, reason="no base rule to modify"
                    )
                )
                continue
            applied, reason, data = MODIFIERS[m.condition](ctx, base, negative)
            modifiers.append(
                ModifierOutcome(id=m.id, effect=m.effect, applied=applied, reason=reason, data=data)
            )
            if applied:
                final_rank = max(final_rank, 3 if m.effect == "set_P1" else min(3, base_rank + 1))

        return Decision(
            event_id=event.event_id,
            issuer_id=event.issuer_id,
            effective_date=ctx.effective_date,
            base_priority=base,
            final_priority=RANK_PRIORITY.get(final_rank),
            decision_status="DECIDED" if base else "NO_APPLICABLE_RULE",
            negative=negative,
            rules=outcomes,
            modifiers=modifiers,
            state_before=state,
            state_after=ctx.state_after,
            provenance=Provenance(
                agency_ratings_used=event.family == "rating" or bool(state and state.entries),
                llm_used=LLM_ROLE_BY_METHOD.get(event.extraction_method, event.extraction_method),
                extraction_method=event.extraction_method,
            ),
            rules_version=self.rules.version,
        )


def evaluate_event(
    event: CreditEvent,
    state: RatingState | None,
    rules: Rules,
    scales: RatingScales,
    prior: PriorContext | None = None,
) -> Decision:
    return MaterialityEngine(rules, scales).evaluate(event, state, prior)
