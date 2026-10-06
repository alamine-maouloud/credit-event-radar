"""Model routing per extraction kind (ADR-020). The benchmarks decide which exact model
reads which family; the settings name a default and a challenger per kind with the reason,
and every run, statement and explanation carries that reason, not only the model's name."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from radar.config import LLMSettings, ModelRole

TO_BENCHMARK = "TO_BENCHMARK"


class RoutingNotBenchmarked(RuntimeError):
    """The family has no benchmarked default yet: an explicit --alternative is required."""


@dataclass(frozen=True)
class ModelSelection:
    kind: str
    role: str  # default | challenger | explicit
    name: str
    model: ModelRole
    reason: str

    def as_record(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "role": self.role,
            "name": self.name,
            "model_id": self.model.model,
            "provider": self.model.provider,
            "reason": self.reason,
        }


def select_model(
    llm: LLMSettings, kind: str, *, alternative: str | None = None, challenger: bool = False
) -> ModelSelection:
    """The model for one extraction kind: the routed default, its challenger, or an explicit
    alternative named on the command line (always allowed, always recorded as such)."""
    if alternative is not None:
        role = llm.benchmark_alternatives[alternative]["extraction"]  # KeyError on an unknown name
        return ModelSelection(
            kind, "explicit", alternative, role,
            f"alternative {alternative!r} requested on the command line, routing bypassed",
        )  # fmt: skip
    route = llm.routing.get(kind)
    if route is None:
        return ModelSelection(
            kind, "default", "roles.extraction", llm.roles["extraction"],
            f"no route configured for {kind!r}: settings.llm.roles.extraction",
        )  # fmt: skip
    name = route.challenger if challenger else route.default
    if name is None or name == TO_BENCHMARK:
        raise RoutingNotBenchmarked(
            f"no {'challenger' if challenger else 'default'} model benchmarked for {kind!r} "
            f"(settings.llm.routing): run the benchmark first, or pass --alternative <name> "
            f"explicitly"
        )
    return ModelSelection(
        kind, "challenger" if challenger else "default", name,
        llm.benchmark_alternatives[name]["extraction"], route.reason,
    )  # fmt: skip
