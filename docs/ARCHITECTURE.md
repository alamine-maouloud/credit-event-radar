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
