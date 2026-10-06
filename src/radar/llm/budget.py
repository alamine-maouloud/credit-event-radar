"""Run budget with a hard stop before any call (CLAUDE.md rule 9, docs/SPEC.md 18).

A call is reserved at its estimated cost; if the reservation would exceed the limit the
call is refused before it is made. Settlement replaces the estimate by the actual cost and
writes a ledger entry with every figure the owner asked for.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from radar.llm.pricing import Pricing

BUDGET_ENV = "LLM_RUN_BUDGET_USD"


class BudgetExceeded(RuntimeError):
    """Raised before a call that would exceed the run budget, or when no budget is set."""


@dataclass
class Reservation:
    reservation_id: int
    model_id: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_before_call: float


class LedgerEntry(BaseModel):
    reservation_id: int
    model_id: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_before_call: float
    actual_input_tokens: int
    actual_output_tokens: int
    actual_cost_after_call: float
    cumulative_run_cost: float
    budget_remaining: float
    settled_at: datetime


class RunBudget:
    def __init__(self, limit_usd: float, pricing: Pricing) -> None:
        if limit_usd <= 0:
            raise BudgetExceeded(f"run budget must be positive, got {limit_usd}")
        self.limit_usd = float(limit_usd)
        self.pricing = pricing
        self.cumulative_cost = 0.0
        self.ledger: list[LedgerEntry] = []
        self._open: dict[int, Reservation] = {}
        self._next_id = 1

    @classmethod
    def from_env(cls, pricing: Pricing, env: Mapping[str, str] | None = None) -> RunBudget:
        source = os.environ if env is None else env
        raw = (source.get(BUDGET_ENV) or "").strip()
        if not raw:
            raise BudgetExceeded(
                f"{BUDGET_ENV} is not set: no LLM call is allowed without a run budget"
            )
        try:
            return cls(float(raw), pricing)
        except ValueError as exc:
            raise BudgetExceeded(f"{BUDGET_ENV}={raw!r} is not a number") from exc

    @property
    def reserved(self) -> float:
        return sum(r.estimated_cost_before_call for r in self._open.values())

    @property
    def remaining(self) -> float:
        return self.limit_usd - self.cumulative_cost - self.reserved

    def reserve(
        self, model_id: str, *, estimated_input_tokens: int, estimated_output_tokens: int
    ) -> Reservation:
        estimate = self.pricing.cost(
            model_id, input_tokens=estimated_input_tokens, output_tokens=estimated_output_tokens
        )
        if self.cumulative_cost + self.reserved + estimate > self.limit_usd + 1e-12:
            raise BudgetExceeded(
                f"estimated {estimate:.4f} USD would exceed the run budget: spent "
                f"{self.cumulative_cost:.4f}, reserved {self.reserved:.4f}, limit {self.limit_usd:.2f}"  # noqa: E501
            )
        reservation = Reservation(
            self._next_id, model_id, estimated_input_tokens, estimated_output_tokens, estimate
        )
        self._open[reservation.reservation_id] = reservation
        self._next_id += 1
        return reservation

    def release(self, reservation: Reservation) -> None:
        self._open.pop(reservation.reservation_id, None)

    def settle(
        self, reservation: Reservation, *, actual_input_tokens: int, actual_output_tokens: int
    ) -> LedgerEntry:
        actual = self.pricing.cost(
            reservation.model_id,
            input_tokens=actual_input_tokens,
            output_tokens=actual_output_tokens,
        )
        self._open.pop(reservation.reservation_id, None)
        self.cumulative_cost += actual
        entry = LedgerEntry(
            reservation_id=reservation.reservation_id,
            model_id=reservation.model_id,
            estimated_input_tokens=reservation.estimated_input_tokens,
            estimated_output_tokens=reservation.estimated_output_tokens,
            estimated_cost_before_call=reservation.estimated_cost_before_call,
            actual_input_tokens=actual_input_tokens,
            actual_output_tokens=actual_output_tokens,
            actual_cost_after_call=actual,
            cumulative_run_cost=self.cumulative_cost,
            budget_remaining=self.limit_usd - self.cumulative_cost,
            settled_at=datetime.now(UTC),
        )
        self.ledger.append(entry)
        return entry
