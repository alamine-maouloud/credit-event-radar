"""Pydantic schemas for Credit Event Radar (docs/SPEC.md, section 7).

All models are plain data containers with deterministic validation. No I/O, no LLM.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, JsonValue, model_validator

# --------------------------------------------------------------------------- #
# Shared literals
# --------------------------------------------------------------------------- #

SourceType = Literal["edgar", "ir_feed", "news_rss", "manual"]
EvidenceType = Literal["sentence", "table_row", "footnote", "llm_statement"]
EventFamily = Literal["rating", "earnings", "issuance", "other"]
ExtractionMethod = Literal["structured", "llm_validated"]
Priority = Literal["P1", "P2", "P3"]
Lang = Literal["fr", "en"]
ClaimStatus = Literal["VERIFIED", "PARTIAL", "UNSUPPORTED"]

Agency = Literal["SP", "MOODYS", "FITCH", "DBRS"]
RatingCategory = Literal["IG", "HY", "DEFAULT"]
Outlook = Literal["positive", "stable", "negative", "developing"]
Watch = Literal["none", "negative", "positive", "developing"]
RatingScope = Literal["issuer", "instrument"]
Seniority = Literal["senior", "subordinated", "hybrid", "AT1", "T2"]
RatingType = Literal[
    "long_term_issuer",
    "long_term_issuer_default",
    "senior_unsecured",
    "senior_preferred",
    "senior_non_preferred",
    "subordinated",
    "instrument",
]
# Only issuer-level ratings feed the composite. Senior preferred, senior unsecured and
# instrument ratings are kept in the seed but never stand in for an issuer rating.
COMPOSITE_ELIGIBLE_RATING_TYPES: frozenset[str] = frozenset(
    {"long_term_issuer", "long_term_issuer_default"}
)
# Seed validation ladder (docs/SEED_VALIDATION.md). Levels are cumulative.
SeedVerification = Literal[
    "SOURCE_VERIFIED", "ENTITY_VERIFIED", "TYPE_VERIFIED", "DATE_VERIFIED", "GOLDEN"
]
VERIFICATION_ORDER: dict[str, int] = {
    "SOURCE_VERIFIED": 0,
    "ENTITY_VERIFIED": 1,
    "TYPE_VERIFIED": 2,
    "DATE_VERIFIED": 3,
    "GOLDEN": 4,
}
CompositeMethod = Literal["middle", "average"]
CompositeRounding = Literal["nearest_weaker", "ceil", "floor"]

PRIORITY_ORDER: dict[str, int] = {"P3": 0, "P2": 1, "P1": 2}
GuidanceStatus = Literal["reaffirmed", "in_line", "raised", "cut", "withdrawn"]
AsOfBasis = Literal["stated", "retrieval"]


# --------------------------------------------------------------------------- #
# Documents and evidence
# --------------------------------------------------------------------------- #


class RawDocument(BaseModel):
    """A source document exactly as retrieved, with full provenance.

    Two hashes answer two different questions: ``content_hash`` is the SHA-256 of the raw
    bytes as delivered by the source (did the source change the document?), ``doc_id`` is
    the SHA-256 of the normalised text produced by ``normalizer_version`` (did our
    normaliser change?). Evidence offsets always refer to ``text``.
    """

    doc_id: str = Field(description="sha256 of the normalised text", min_length=64, max_length=64)
    source_type: SourceType
    url: HttpUrl
    title: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime
    content_hash: str = Field(
        description="sha256 of the raw bytes, before any parsing", min_length=64, max_length=64
    )
    raw_size_bytes: int = Field(ge=0)
    normalizer_version: str
    text: str
    raw_path: str = Field(description="Path of the raw snapshot on disk")
    issuer_hint: str | None = None
    extra: dict[str, JsonValue] = Field(
        default_factory=dict,
        description=(
            "Connector metadata only (e.g. accession_number, form, items, filing_date, "
            "primary_document). Never business data."
        ),
    )


class EvidenceSpan(BaseModel):
    """A passage of a source document that supports an extracted field or a claim.

    Offsets are character positions in ``RawDocument.text``. ``evidence_type`` says how the
    passage was delimited; Phase 2 automatic extraction only produces ``sentence``, the
    other types exist so that table rows and footnotes can be added without changing the
    model.
    """

    doc_id: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    quote: str = Field(min_length=1)
    evidence_type: EvidenceType = "sentence"
    extractor_version: str
    match_score: float = Field(
        ge=0.0, le=100.0, description="rapidfuzz score after normalisation (0 to 100)"
    )
    field: str | None = Field(
        default=None, description="Name of the extracted field this span supports, if any"
    )

    @model_validator(mode="after")
    def _check_offsets(self) -> EvidenceSpan:
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        return self


# --------------------------------------------------------------------------- #
# Typed event fields (SPEC 7.2)
# --------------------------------------------------------------------------- #


class RatingFields(BaseModel):
    agency: Agency | None = None
    old_rating: str | None = None
    new_rating: str | None = None
    old_outlook: Outlook | None = None
    new_outlook: Outlook | None = None
    watch: Watch | None = None
    scope: RatingScope | None = None
    instrument_seniority: Seniority | None = None


class EarningsFields(BaseModel):
    period: str | None = None
    guidance_metric: str | None = None
    guidance_old: float | None = None
    guidance_new: float | None = None
    guidance_change_pct: float | None = None
    guidance_change_points: float | None = Field(
        default=None,
        description="Change of a growth-rate guidance in points, never compared to the threshold",
    )
    guidance_qualified_significant: bool | None = Field(
        default=None, description="The issuer itself calls the guidance cut significant"
    )
    guidance_status: GuidanceStatus | None = Field(
        default=None,
        description="Explicit deterministic statement about guidance found in the source",
    )
    flags: list[Literal["liquidity", "going_concern", "covenant", "impairment"]] = Field(
        default_factory=list
    )


class IssuanceFields(BaseModel):
    amount: float | None = None
    currency: str | None = None
    amount_eur_equiv: float | None = None
    coupon: float | None = None
    maturity: date | None = None
    seniority: Seniority | None = None
    use_of_proceeds: str | None = None


# --------------------------------------------------------------------------- #
# Events, decisions, claims
# --------------------------------------------------------------------------- #


class CreditEvent(BaseModel):
    event_id: str
    issuer_id: str
    family: EventFamily
    event_type: str = Field(
        description="e.g. downgrade, outlook_change, watch, guidance_cut, new_issue"
    )
    effective_date: date | None = None
    fields: dict[str, Any] = Field(
        default_factory=dict, description="Typed per event_type, see RatingFields and friends"
    )
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    extraction_method: ExtractionMethod
    source_doc_ids: list[str] = Field(default_factory=list)
    # How the event was detected never changes; an enrichment by validated LLM statements
    # is recorded separately so that the explanation can name both (lot 3.3b).
    enrichment_method: Literal["llm_validated"] | None = None


DecisionStatus = Literal["DECIDED", "NO_APPLICABLE_RULE"]


class PriorityDecision(BaseModel):
    """Outcome of the materiality engine for one event.

    ``priority`` is null when no base rule applies (``decision_status`` is then
    NO_APPLICABLE_RULE): P3 is a real, weak materiality level, never a default bucket.
    """

    event_id: str
    priority: Priority | None
    decision_status: DecisionStatus = "DECIDED"
    base_priority: Priority | None = None
    triggered_rules: list[str] = Field(
        default_factory=list, description='e.g. ["RAT-02", "MOD-01"]'
    )
    rule_details: list[str] = Field(default_factory=list)
    rules_version: str
    llm_role: str = Field(
        description=(
            'e.g. "field extraction (validated against source text)". '
            'Never "None" if an LLM extracted a field.'
        )
    )


class Claim(BaseModel):
    claim_id: str
    text: str
    lang: Lang
    citations: list[EvidenceSpan] = Field(default_factory=list)
    status: ClaimStatus
    checks: dict[str, Any] = Field(
        default_factory=dict, description="quote_found, numbers_match, support_judgment"
    )


# --------------------------------------------------------------------------- #
# Ratings
# --------------------------------------------------------------------------- #


class AgencyRating(BaseModel):
    """One agency rating for one issuer, as recorded in data/seeds/ratings_seed.csv.

    ``as_of`` is only set when the source gives a full date. Partial dates such as
    "2025-03" are kept verbatim in ``as_of_raw`` and never completed.
    """

    issuer_id: str
    legal_entity: str | None = None
    scope: RatingScope = "issuer"
    agency: Agency
    rating_type: RatingType
    rating: str
    outlook: Outlook | None = None
    watch: Watch = "none"
    as_of: date | None = None
    as_of_raw: str | None = None
    source_url: HttpUrl
    source_title: str | None = None
    retrieved_at: date
    verification_status: SeedVerification

    @model_validator(mode="after")
    def _scope_matches_type(self) -> AgencyRating:
        if (self.scope == "instrument") != (self.rating_type == "instrument"):
            raise ValueError("scope 'instrument' and rating_type 'instrument' go together")
        return self

    @property
    def composite_eligible(self) -> bool:
        return self.rating_type in COMPOSITE_ELIGIBLE_RATING_TYPES

    def verified_at_least(self, level: SeedVerification) -> bool:
        return VERIFICATION_ORDER[self.verification_status] >= VERIFICATION_ORDER[level]


class RatingObservation(BaseModel):
    """An agency rating read from a document by a deterministic extractor.

    Distinct from the hand-verified seed: ``verification_method`` says how the value was
    obtained and ``evidence_span_id`` points at the exact table row or sentence.

    ``rating_date`` is the date the source states for the rating (an "as of" line, a dated
    row). ``observed_at`` is the day the value was observed in the document. When the
    source gives no date (a current ratings page), ``as_of_basis`` is ``retrieval``: the
    observation may feed decisions from ``observed_at`` onwards, never earlier (ADR-011).
    """

    observation_id: str
    issuer_id: str
    agency: Agency
    rating: str
    outlook: Outlook | None = None
    watch: Watch = "none"
    rating_type: RatingType
    scope: RatingScope = "issuer"
    rating_date: date | None
    observed_at: date
    as_of_basis: AsOfBasis
    doc_id: str
    evidence_span_id: str
    evidence: EvidenceSpan
    extractor_version: str
    verification_method: Literal[
        "structured_table", "structured_table_current", "structured_sentence"
    ]

    @model_validator(mode="after")
    def _basis_matches_dates(self) -> RatingObservation:
        if self.as_of_basis == "stated" and self.rating_date is None:
            raise ValueError("as_of_basis 'stated' requires rating_date")
        if self.as_of_basis == "retrieval" and self.rating_date is not None:
            raise ValueError("as_of_basis 'retrieval' means the source states no rating date")
        return self

    @property
    def as_of(self) -> date:
        """Date from which the observation may be used for a decision."""
        return self.rating_date if self.rating_date is not None else self.observed_at


class CompositeRating(BaseModel):
    """Result of the composite rating computation (SPEC 9.2).

    Analytical metadata only: agency-level ratings are authoritative for credit events
    and no deterministic rule may be triggered by the composite (ADR-001).
    """

    notch: int = Field(ge=1, le=22)
    label: str = Field(description="Canonical S&P-style label for the notch")
    category: RatingCategory
    method: CompositeMethod
    rounding: CompositeRounding | None = Field(
        default=None, description="Only set for the average method"
    )
    inputs: list[AgencyRating] = Field(description="Ratings actually used, one per agency")

    @property
    def n_ratings(self) -> int:
        return len(self.inputs)
