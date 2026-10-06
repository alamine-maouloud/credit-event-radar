# Architecture decisions

Short records of the decisions that shape the code. One entry per decision, newest last.
Pipeline and module layout are described in `CLAUDE.md` and `docs/SPEC.md` section 8.

## ADR-001 · Agency-level ratings are authoritative, the composite is metadata

Date: 2026-10-06. Status: accepted.

**Context.** `docs/SPEC.md` 9.2 defines a composite rating (middle or average of the agency
notches) and the first version of the materiality matrix triggered RAT-01, RAT-02, RAT-08 and
MOD-01 on that composite. A composite is a house methodology: with three agencies, a real
agency crossing from BBB- to BB+ can leave the composite unchanged, and a rule keyed on the
composite would then stay silent on an event the analyst must see.

**Decision.** Agency-level ratings are the authoritative input for credit events. The
composite rating is analytical metadata only: it may be displayed (dashboard, "Why this
priority?" context, committee note header) but it never triggers or modifies a deterministic
rule. `config/rules.yaml` 1.1 rewrites RAT-01, RAT-02, RAT-08 and MOD-01 on agency ratings
(MOD-01 uses the weakest agency rating). A test (`test_no_rule_depends_on_the_composite`)
enforces the rule on the matrix.

**Consequences.** The fallen-angel rule fires on the first agency crossing, which is the
conservative behaviour wanted for a monitoring tool. Split ratings are reported through
RAT-02. `composite_rating()` keeps its place in `ratings.py` for context and sorting only.

## ADR-002 · Rating types are explicit and only issuer-level ratings feed the composite

Date: 2026-10-06. Status: accepted.

**Context.** The hand-collected seed mixes long-term issuer ratings, issuer default ratings
(Fitch IDR), senior preferred ratings (banks), senior unsecured ratings and a bond rating.
A senior preferred rating is not an issuer rating and must not be treated as one.

**Decision.** `rating_type` is a closed enumeration (`long_term_issuer`,
`long_term_issuer_default`, `senior_unsecured`, `senior_preferred`, `senior_non_preferred`,
`subordinated`, `instrument`). Only `long_term_issuer` and `long_term_issuer_default` are
composite-eligible. Other rows stay in the seed, fully sourced, and are reported as such.
An unknown rating type makes the loader fail.

**Consequences.** Intesa Sanpaolo (senior preferred only) and the OMV Moody's row (senior
unsecured) currently contribute no issuer rating; an issuer-level rating has to be sourced
before they enter any composite.

## ADR-003 · Seed validation ladder before any row becomes golden

Date: 2026-10-06. Status: accepted.

**Context.** The seed will be shown to credit professionals. A row collected from a public
page may still carry the wrong legal entity, the wrong rating type or an approximate date.

**Decision.** Every seed row carries a cumulative `verification_status`:
`SOURCE_VERIFIED`, `ENTITY_VERIFIED`, `TYPE_VERIFIED`, `DATE_VERIFIED`, `GOLDEN`. The checks
are described in `docs/SEED_VALIDATION.md` and the log of each pass lives there too. The
composite only accepts `GOLDEN` rows by default. Promotion to `GOLDEN` is a human decision.

## ADR-004 · Deterministic end-to-end before any LLM

Date: 2026-10-06. Status: accepted.

**Context.** The project's credibility rests on the deterministic chain: source, event,
rule, priority, audit. The LLM only summarises and contextualises afterwards.

**Decision.** Phases are executed in the order 2 (ingestion), 4 (materiality engine), then
3 (LLM extraction). The structured, deterministic extraction path advances with Phase 2.
The first end-to-end target is a real historical rating action ingested from a public
filing, extracted deterministically, scored P1 by RAT-01, with its source attached and an
audit log entry, without any LLM call. Model identifiers for the second LLM backend stay
`TO_CONFIRM` until the benchmark is built.

## ADR-005 · Evidence windows are typed, automatic extraction starts with sentences

Date: 2026-10-06. Status: accepted.

**Context.** Financial filings state rating actions in sentences, but also in tables with
footnotes that refer to a row. A rule such as "agency, old rating and new rating in the
same sentence" is precise but will miss table-plus-footnote cases.

**Decision.** `EvidenceSpan` carries `evidence_type` (`sentence`, `table_row`, `footnote`)
and `extractor_version`. Version `structured-rating-1.0` only emits `sentence` spans and
requires the agency and the "from X to Y" transition inside one sentence, with labels
validated against `rating_scales.yaml`, a direction-consistent verb, and no "short-term"
wording between the verb and the transition. Table rows and footnotes are future
extractor versions, not a model change.

**Consequences.** Every rejected candidate is written to the audit log with its reason
(`no_action_verb`, `short_term_rating`, `label_not_in_scale`, `verb_direction_mismatch`),
so misses are visible instead of silent. The Harley-Davidson 10-Q, whose action sits in a
footnote sentence under the ratings table, is extracted by the sentence rule.

## ADR-006 · Two hashes per document

Date: 2026-10-06. Status: accepted.

`content_hash` is the SHA-256 of the raw bytes exactly as delivered; `doc_id` is the
SHA-256 of the normalised text produced by `normalizer_version`. Deduplication of
documents uses the raw hash. A change of normaliser changes `doc_id` and every stored
offset, which is why `normalizer_version` is recorded on each document and checked by the
fixture tests.

## ADR-007 · Rating state at the event date, anti look-ahead

Date: 2026-10-06. Status: accepted.

**Context.** Several rules (RAT-02, RAT-04, RAT-06, MOD-01, MOD-02, MOD-03) need to know
what the other agencies, the previous outlook or prior events looked like when the event
happened. For a historical backtest, using anything published after the event would
contaminate the decision.

**Decision.** The engine receives a `RatingState` built for the event's effective date D
from dated candidates only: seed rows, rating observations extracted from documents, and
previously stored rating events. A candidate is used only if its `as_of` is on or before
D, its date is complete, its age is at most `max_rating_age_days`, its agency is in
`admissible_agencies` and its rating type is issuer-level. Every refused candidate is kept
with its reason and shown in the explanation. Windows for MOD-02 and MOD-03 use
effective dates and as-of dates, never ingestion timestamps. The parameters live in
`config/rules.yaml` under `rating_state`.

400 days is a conservative prototype freshness threshold, not a statement about rating
validity. It is configurable and should be calibrated against production data and team
requirements. Undated seed rows therefore never influence a decision; they remain
displayable reference data.

**Consequences.** The Harley-Davidson control case is decided with Moody's Baa3 and Fitch
BBB as of 2026-06-30, read from the same 10-Q by `structured-ratings-table-1.0`, eight
days before the S&P action. DBRS ratings are kept in the seed but ignored by the rules.

## ADR-008 · No priority when no rule applies

Date: 2026-10-06. Status: accepted.

An event that triggers no base rule receives `priority = null` with
`decision_status = NO_APPLICABLE_RULE`. P3 means "relevant but weak", never "nothing
matched". Modifiers are not applied without a base rule. This keeps precision and recall
measurable: an alert exists only when a rule fired.

## ADR-009 · Orthogonal rules, one explanation per rule

Date: 2026-10-06. Status: accepted.

Rules describe properties of an event and can be true together (a two-notch downgrade
that crosses IG/HY triggers RAT-01 and RAT-03). The only exclusions are the ones the
specification states: RAT-05 excludes the IG/HY crossing, RAT-07 excludes the boundary
case of RAT-04, RAT-09 excludes the rising star of RAT-08, ISS-04 excludes ISS-01 and
ISS-02. Every rule and modifier is evaluated and reported, triggered or not, with the
data it used, so `radar show-event <id> --explain` is the engine's own output.

## ADR-010 · Issuer sources: terms checked by hand, private fixtures, short quotes only

Date: 2026-10-06. Status: accepted.

**Context.** Phase 2b reads official investor-relations sites. Their robots.txt files
allow crawling (Volkswagen, TRATON and OMV were checked on 2026-10-06) but their legal
notices restrict reproduction: TRATON forbids reproduction without agreement, OMV allows
personal and informative use only. Agency releases republished by Volkswagen are S&P,
Fitch and DBRS copyright. The repository is meant to be public.

**Decision.** Every source is listed in `universe.yaml` with a note on what was checked.
The adapter declares its User-Agent, honours robots.txt and keeps a two-second cadence per
host. Raw snapshots stay on the local disk. Real documents used as fixtures are private:
only their manifest (URL, hashes, dates, size, expected extraction) is committed, the
bytes live in a gitignored file fetched by `scripts/fetch_ir_fixture.py`, and the local
integration test skips when they are absent. Public tests use synthetic documents. Any
user-facing output quotes short spans with the source URL, never full documents.

## ADR-011 · rating_date versus observed_at

Date: 2026-10-06. Status: accepted.

A rating observation carries ``rating_date`` (the date the source states, if any),
``observed_at`` (the day the value was seen in the document) and ``as_of_basis``. A
current ratings page states no date: its observations are ``retrieval``-based and may feed
decisions from ``observed_at`` onwards, never before, which the rating state enforces by
treating ``observed_at`` as the as-of date. The explanation prints the basis next to every
rating used so an analyst can tell a stated date from a page visit.

## ADR-012 · Nothing is inferred: NO_EVENT, UNREADABLE_TEXT and tightened rules

Date: 2026-10-06. Status: accepted.

**Decision.** A stored document that matches no deterministic extractor ends with outcome
``NO_EVENT`` and an audit entry; a document whose normalised text is not readable English
(a PDF font without a Unicode map, for instance) ends with ``UNREADABLE_TEXT`` and is not
extracted. Rules that previously inferred a benign reading were tightened in rules.yaml
1.3 and 1.4: ERN-04 needs an explicit statement that guidance was maintained or that
results are in line; ISS-04 needs a known EUR-equivalent amount below the threshold or an
explicit tap; redemptions, intentions to issue and amounts that also cover a loan never
become issuance events. A results release that recaps a past bond does not create a new
issuance event. The consequence is accepted: many real documents end with no priority,
which is the honest answer until Phase 3 adds validated extraction of quantities.
