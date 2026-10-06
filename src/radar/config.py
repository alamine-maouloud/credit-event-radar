"""Loaders and validated models for the YAML configuration files and the ratings seed.

This is the only Phase 1 module that performs I/O. Everything it returns is a validated
pydantic model consumed by the pure modules (:mod:`radar.ratings`, later ``materiality``).
"""

from __future__ import annotations

import csv
import os
import re
from collections.abc import MutableMapping
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from radar.models import (
    AgencyRating,
    CompositeMethod,
    CompositeRounding,
    EventFamily,
    Outlook,
    Priority,
    Watch,
)
from radar.ratings import RatingScales, normalize_rating

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
SEEDS_DIR = ROOT / "data" / "seeds"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    return data


# --------------------------------------------------------------------------- #
# rating_scales.yaml
# --------------------------------------------------------------------------- #


def load_rating_scales(path: Path = CONFIG_DIR / "rating_scales.yaml") -> RatingScales:
    return RatingScales.model_validate(load_yaml(path))


# --------------------------------------------------------------------------- #
# settings.yaml
# --------------------------------------------------------------------------- #


ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]


class ModelRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["anthropic", "openai", "azure_openai"]
    model: str
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    reasoning_effort: ReasoningEffort | None = None


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_provider: Literal["anthropic", "openai", "azure_openai"]
    cache_dir: str
    run_budget_usd_env: str = "LLM_RUN_BUDGET_USD"
    pricing_file: str = "config/llm_pricing.yaml"
    roles: dict[Literal["extraction", "notes", "verification"], ModelRole]
    benchmark_alternatives: dict[str, dict[str, ModelRole]] = Field(default_factory=dict)


class RatingsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    composite_method: CompositeMethod = "middle"
    composite_rounding: CompositeRounding = "nearest_weaker"
    composite_agencies: list[str] = Field(default_factory=lambda: ["SP", "MOODYS", "FITCH"])


class PathsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    db: str
    raw: str
    outputs: str
    seeds: str


class SecSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_agent_env: str = "SEC_USER_AGENT"
    max_requests_per_second: int = Field(default=8, ge=1, le=10)


class IRSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_agent_env: str = "IR_USER_AGENT"
    min_interval_seconds: float = Field(default=2.0, ge=0.5)
    respect_robots: bool = True
    timeout_seconds: float = Field(default=30.0, ge=1.0)


class IngestionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_interval_minutes: int = Field(ge=1)
    sec: SecSettings
    ir: IRSettings = Field(default_factory=IRSettings)


class AlertRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[Literal["teams", "email", "local"]]
    mode: Literal["immediate", "digest"]
    digest_every_hours: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _local_always_on(self) -> AlertRoute:
        if "local" not in self.channels:
            raise ValueError("local rendering is mandatory for every priority")
        if self.mode == "digest" and self.digest_every_hours is None:
            raise ValueError("digest mode requires digest_every_hours")
        return self


class AlertsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routing: dict[Priority, AlertRoute]
    teams_webhook_env: str = "TEAMS_WEBHOOK_URL"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    llm: LLMSettings
    ratings: RatingsSettings
    paths: PathsSettings
    ingestion: IngestionSettings
    alerts: AlertsSettings


def load_settings(path: Path = CONFIG_DIR / "settings.yaml") -> Settings:
    return Settings.model_validate(load_yaml(path))


# --------------------------------------------------------------------------- #
# universe.yaml
# --------------------------------------------------------------------------- #

ISSUER_ID_RE = re.compile(r"^[A-Z0-9_]+$")


IRSourceKind = Literal["rss", "sitemap", "page_links", "page"]
DocumentType = Literal["press_release", "ratings_page", "rating_report", "debt_page", "other"]
TableProfile = Literal["sec_as_of", "current_by_agency", "dated_by_agency"]


class IRSource(BaseModel):
    """One official investor-relations source of an issuer and how to discover documents in it.

    kind: rss (feed entries), sitemap (URLs under path_prefix with lastmod), page_links
    (links of a page matching link_pattern), page (the page itself is the document).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    kind: IRSourceKind
    url: HttpUrl
    document_type: DocumentType
    content: Literal["html", "pdf"] = "html"
    path_prefix: str | None = None
    link_pattern: str | None = None
    include_title: str | None = None
    include_categories: list[str] = Field(default_factory=list)
    table_profile: TableProfile | None = None
    max_items: int = Field(default=50, ge=1, le=500)
    note: str | None = None

    @model_validator(mode="after")
    def _kind_parameters(self) -> IRSource:
        if self.kind == "sitemap" and not self.path_prefix:
            raise ValueError(f"{self.id}: sitemap sources need path_prefix")
        if self.kind == "page_links" and not self.link_pattern:
            raise ValueError(f"{self.id}: page_links sources need link_pattern")
        if self.kind != "page" and self.table_profile:
            raise ValueError(f"{self.id}: table_profile only applies to kind page")
        for pattern in (self.link_pattern, self.include_title):
            if pattern:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(
                        f"{self.id}: invalid regular expression {pattern!r}: {exc}"
                    ) from exc
        return self


class Issuer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    legal_entity: str | None = None
    aliases: list[str] = Field(default_factory=list)
    sector: str
    country: str = Field(pattern=r"^[A-Z]{2}$")
    sec_cik: str | None = Field(default=None, pattern=r"^\d{10}$")
    ratings_page: HttpUrl | None = None
    ir_sources: list[IRSource] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    rating_status: Literal["verified", "unverified"] = "verified"
    notes: str | None = None

    @model_validator(mode="after")
    def _id_format(self) -> Issuer:
        if not ISSUER_ID_RE.match(self.id):
            raise ValueError(f"issuer id {self.id!r} must match {ISSUER_ID_RE.pattern}")
        return self


class Universe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    disclosure: str
    retrieved_as_of: date
    issuers: list[Issuer]

    @model_validator(mode="after")
    def _unique(self) -> Universe:
        ids = [i.id for i in self.issuers]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate issuer ids")
        source_ids = [s.id for i in self.issuers for s in i.ir_sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("duplicate ir_sources ids across issuers")
        seen: dict[str, str] = {}
        for issuer in self.issuers:
            for alias in [issuer.name, *issuer.aliases]:
                key = alias.casefold()
                if key in seen and seen[key] != issuer.id:
                    raise ValueError(f"alias {alias!r} used by {seen[key]} and {issuer.id}")
                seen[key] = issuer.id
        return self

    def by_id(self, issuer_id: str) -> Issuer:
        for issuer in self.issuers:
            if issuer.id == issuer_id:
                return issuer
        raise KeyError(issuer_id)

    @property
    def ids(self) -> set[str]:
        return {i.id for i in self.issuers}


def load_universe(path: Path = CONFIG_DIR / "universe.yaml") -> Universe:
    return Universe.model_validate(load_yaml(path))


# --------------------------------------------------------------------------- #
# rules.yaml
# --------------------------------------------------------------------------- #

RULE_ID_RE = re.compile(r"^(RAT|ERN|ISS|EDG)-\d{2}$")
MODIFIER_ID_RE = re.compile(r"^MOD-\d{2}$")


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guidance_cut_p1_pct: float = Field(gt=0)
    issuance_p2_eur: float = Field(gt=0)
    leverage_threshold: float | None = None
    mod02_window_days: int = Field(ge=1)
    mod02_min_events: int = Field(ge=2)


RuleDirection = Literal["negative", "positive", "neutral"]


class RatingStateParams(BaseModel):
    """How the issuer's rating state at the event date is built (ADR-007)."""

    model_config = ConfigDict(extra="forbid")

    admissible_agencies: list[str]
    eligible_rating_types: list[str]
    max_rating_age_days: int = Field(ge=1)
    require_complete_date: bool = True
    allow_future_observation: bool = False
    stale_policy: Literal["exclude"] = "exclude"


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    family: EventFamily | Literal["edgar"]
    priority: Priority
    direction: RuleDirection
    condition: str = Field(description="Symbolic condition name implemented by the engine")
    description: str
    definition: str = Field(description="Formal statement of the rule, tested in tests/")
    scope: Literal["must", "should"] = "must"

    @model_validator(mode="after")
    def _id_format(self) -> Rule:
        if not RULE_ID_RE.match(self.id):
            raise ValueError(f"rule id {self.id!r} must match {RULE_ID_RE.pattern}")
        return self


class Modifier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    effect: Literal["+1", "set_P1"]
    condition: str
    description: str
    definition: str
    requires_sourced_data: bool = False

    @model_validator(mode="after")
    def _id_format(self) -> Modifier:
        if not MODIFIER_ID_RE.match(self.id):
            raise ValueError(f"modifier id {self.id!r} must match {MODIFIER_ID_RE.pattern}")
        return self


class Rules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    changelog: list[str] = Field(default_factory=list)
    thresholds: Thresholds
    rating_state: RatingStateParams
    rules: list[Rule]
    modifiers: list[Modifier]

    @model_validator(mode="after")
    def _unique_ids(self) -> Rules:
        ids = [r.id for r in self.rules] + [m.id for m in self.modifiers]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule or modifier ids")
        return self

    def by_id(self, rule_id: str) -> Rule | Modifier:
        for item in [*self.rules, *self.modifiers]:
            if item.id == rule_id:
                return item
        raise KeyError(rule_id)


def load_rules(path: Path = CONFIG_DIR / "rules.yaml") -> Rules:
    return Rules.model_validate(load_yaml(path))


# --------------------------------------------------------------------------- #
# data/seeds/ratings_seed.csv
# --------------------------------------------------------------------------- #

SEED_COLUMNS = [
    "issuer_id",
    "issuer",
    "legal_entity",
    "instrument_or_issuer_rating",
    "rating_agency",
    "rating_type",
    "rating",
    "outlook",
    "rating_date",
    "source_url",
    "source_title",
    "retrieved_at",
    "verification_status",
]

_WATCH_PATTERNS: dict[Watch, tuple[str, ...]] = {
    "negative": ("creditwatch negative", "watch negative", "review for downgrade", "rwn"),
    "positive": ("creditwatch positive", "watch positive", "review for upgrade", "rwp"),
    "developing": ("creditwatch developing", "watch developing", "review direction uncertain"),
}
_OUTLOOKS: tuple[Outlook, ...] = ("positive", "stable", "negative", "developing")


def parse_outlook(text: str | None) -> tuple[Outlook | None, Watch]:
    """Split a free-text outlook cell into (outlook, watch). Unknown text raises."""
    if text is None or not text.strip():
        return None, "none"
    key = text.strip().casefold()
    for watch, patterns in _WATCH_PATTERNS.items():
        if any(p in key for p in patterns):
            return None, watch
    if key in _OUTLOOKS:
        return key, "none"  # type: ignore[return-value]
    raise ValueError(f"unrecognised outlook/watch text: {text!r}")


_FULL_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_partial_date(text: str | None) -> tuple[date | None, str | None]:
    """Return (as_of, as_of_raw). Only a full YYYY-MM-DD date populates as_of."""
    if text is None or not text.strip():
        return None, None
    raw = text.strip()
    if _FULL_DATE_RE.match(raw):
        return date.fromisoformat(raw), raw
    if re.match(r"^\d{4}(-\d{2})?$", raw):
        return None, raw
    raise ValueError(f"unrecognised date: {text!r}")


def load_ratings_seed(
    path: Path,
    scales: RatingScales,
    universe: Universe | None = None,
) -> list[AgencyRating]:
    """Load the hand-verified ratings seed into validated AgencyRating rows.

    Every agency name must resolve in ``scales``, every rating must exist in that agency's
    scale, and (when ``universe`` is given) every issuer_id must be in the universe.
    Nothing is guessed or completed.
    """
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != SEED_COLUMNS:
            raise ValueError(f"{path}: expected columns {SEED_COLUMNS}, got {reader.fieldnames}")
        rows = [r for r in reader if any((v or "").strip() for v in r.values())]

    ratings: list[AgencyRating] = []
    for n, row in enumerate(rows, start=2):
        agency = scales.resolve_agency(row["rating_agency"])
        if agency is None:
            raise ValueError(f"{path}:{n}: unknown agency {row['rating_agency']!r}")
        if universe is not None and row["issuer_id"] not in universe.ids:
            raise ValueError(f"{path}:{n}: issuer_id {row['issuer_id']!r} not in universe")
        outlook, watch = parse_outlook(row["outlook"])
        as_of, as_of_raw = parse_partial_date(row["rating_date"])
        ratings.append(
            AgencyRating(
                issuer_id=row["issuer_id"],
                legal_entity=row["legal_entity"] or None,
                scope=row["instrument_or_issuer_rating"],  # type: ignore[arg-type]
                agency=agency,  # type: ignore[arg-type]
                rating_type=row["rating_type"],  # type: ignore[arg-type]
                rating=normalize_rating(agency, row["rating"], scales),
                outlook=outlook,
                watch=watch,
                as_of=as_of,
                as_of_raw=as_of_raw,
                source_url=row["source_url"],
                source_title=row["source_title"] or None,
                retrieved_at=date.fromisoformat(row["retrieved_at"]),
                verification_status=row["verification_status"],  # type: ignore[arg-type]
            )
        )
    return ratings


# --------------------------------------------------------------------------- #
# .env
# --------------------------------------------------------------------------- #


def load_dotenv(path: Path = ROOT / ".env", env: MutableMapping[str, str] | None = None) -> int:
    """Load KEY=VALUE lines from .env into ``env`` without overriding existing keys.

    Returns the number of keys set. Missing file: nothing happens. Secrets never go
    anywhere else than the process environment.
    """
    target = os.environ if env is None else env
    if not path.exists():
        return 0
    count = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in target:
            target[key] = value
            count += 1
    return count
