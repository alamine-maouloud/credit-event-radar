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

Two more guards came out of the first live run on real issuer pages. Issuer web pages are
normalised with `ir-html-1.1`, which drops navigation, footers, asides and "related
content" teasers (a sports press release carried a teaser of an earlier hybrid issuance,
which must not become an event of that page). Version 1.0 matched those class names on any
element and silently dropped the whole OMV article, whose wrapper is
`<article class="... has-sidebar ...">`: since 1.1 the semantic wrappers `html`, `body`,
`main` and `article` are never dropped by their attributes. Only the OMV fixtures changed
text and hash; every other recorded `normalized_sha256` is unchanged. Agency reports are read in report mode: a
sentence dated differently from the report header (a "Related Research" bibliography
entry, a criteria reference) is a reference to a past action, not the action of the
document. DBRS actions are extracted and shown but trigger no rating rule, as DBRS is not
an admissible agency in rules.yaml 1.x.

## ADR-013 · Entity guard and historical references in sentence extraction

Date: 2026-10-06. Status: accepted.

**Context.** On the first live run two DBRS documents produced false positives: a rating
change of VW Credit Canada, Inc. was attributed to Volkswagen AG because the document
belonged to Volkswagen, and a rating-history line ("Confirms Issuer Rating at A (low)",
31 July 2025) became a 2025 affirmation.

**Decision.** A rating action is never attributed to the document's issuer merely because
the document is theirs. When a sentence names only another legal entity (corporate suffix
detection, agencies excluded), the event is attached to that entity if the universe
resolves it exactly, otherwise the candidate is rejected as ``unresolved_entity``. A
sentence that names the issuer or refers to "the Company" keeps the issuer, even when
co-rated entities are listed. In agency reports, a sentence sitting in a rating-history or
bibliography section, written as a quoted title followed by a date, or dated more than
ninety days before the document date, is classified ``historical_reference`` and never
becomes an event; a report may legitimately describe an action taken a few weeks earlier.
The document date comes from the publication date, else the header, else the URL.
Normalisers and hashes are unchanged.

## ADR-014 · LLM proposes, code validates, rules decide

Date: 2026-10-06. Status: accepted (Phase 3.1).

**Context.** Deterministic extractors leave real documents without priority when the
information is unstructured (a guidance range in prose). An LLM can read it, but must not
become a second engine, and a false extraction must be traceable to its layer.

**Decision.** The LLM only extracts fields that the deterministic layer could not, with
verbatim evidence and offsets, under a strict closed JSON schema (no confidence field, no
priority, no summary). The model reads raw bounds and units; Python computes midpoints,
deltas and direction, and the model's own direction claim is recorded for comparison only.
Every statement passes a two-level validator: text match (exact at the offsets, exact
unique elsewhere, or rapidfuzz partial ratio of at least 90, which is never sufficient
alone) and semantic field validation (every number in the quote, unit tokens consistent,
metric label present and consistent with the metric, numbers in the sentence that names
the metric, no other legal entity as subject, no date after the document, plausible
period). Any failed check rejects and is written to the audit log.

Providers implement one interface (``complete(request)``) and record the id used and the
model name the API resolved to. Prices live in ``config/llm_pricing.yaml`` (versioned);
the run budget (``LLM_RUN_BUDGET_USD``) reserves the estimated cost before each call and
refuses the call when it would exceed the limit, then settles the actual cost in a ledger
(estimated before, actual after, cumulative, remaining). The cache key covers the
normalised document hash, extractor version, provider, exact model id, prompt version,
schema version and reasoning effort. The document text is passed to the model as data and
the prompt says so; nothing the model returns is executed.

Benchmark models: gpt-5.6-terra (main) and gpt-5.6-sol (high-quality reference) as given
by the project owner on 2026-10-06; the Anthropic id stays TO_CONFIRM until the official
catalogue is checked on the day of the first real call, which the providers enforce.

## ADR-015 · Benchmark protocol for the LLM extraction (Phase 3.2, 2026-10-06)

The guidance gold set V1 (30 documents, 57 occurrences, eval/gold/guidance_v1.yaml built
into guidance_v1.jsonl) is frozen by eval/gold/guidance_v1.lock.json: hashes of the labeling
guide, of the labels and of every document. `radar llm-extract` refuses a document whose
bytes no longer match the frozen hashes. A run writes a directory under eval/runs/<run_id>/:
outputs.jsonl (one row per document: run status, cost, latency, resolved model, every
statement with its two level validation and the figures computed in code), run.json (model,
effort, prompt version and content hash, schema version, gold and lock hashes, budget) and
metrics.json. `radar llm-eval` re-scores a run directory. The scorer matches occurrences on
(metric, period) inside each document, counts only validated statements for recall and
reports the unsupported claim rate as the share of statements that are invalid or match no
gold occurrence; the document behaviour is derived from validated statements only.

Two rules make the comparison between models honest: the prompt is versioned and identical
for every model of a benchmark round (cache key includes it), and no label of a frozen gold
version is edited after model outputs have been seen. A correction creates guidance_v2 with
its own lock and its justification; errors of a model stay visible in the run that produced
them. `--dry-run` renders every prompt and sums the price of the estimated input tokens plus
the output ceiling, so the budget is checked before the first real call.

Two lessons of the first real calls (2026-10-06) are built into the layer. The Responses API
rejects the temperature parameter for the gpt-5 family, so the backend omits the key for
those models instead of sending null. Reasoning tokens count against max_output_tokens: at
4096 the second document came back incomplete with no text at all, and that empty answer
had been cached as a schema failure. A response flagged incomplete by the API is now the
status `truncated`, only complete and parseable answers enter the cache, and the benchmark
reserves a ceiling of 32768 output tokens against the budget before each call.

## ADR-016 · Deterministic scope guard on extracted statements (2026-10-06)

The guidance prompt tells the model to report Group level figures only. On the frozen gold
set Terra ignored that instruction on every OMV quarterly report and proposed segment CAPEX
figures as faithful, validated quotes; Sol followed it. A prompt instruction is not a
control, so the rule now lives in code: each issuer declares in universe.yaml its principal
division (Volkswagen Automotive Division, TRATON Operations) and the segment names that
never carry its guidance. The validator rejects a statement whose metric label names a
segment, or whose metric sentence introduces one with "for", "of", "in" or "at", with the
reason OUT_OF_SCOPE_SEGMENT. Re-scoring both runs from the cache showed the guard removing
the whole precision gap between the models without touching recall. The pattern is the
one of the whole project: the model proposes, the code validates, the rules decide, and a
weakness found in a model becomes a deterministic check rather than a longer prompt.

## ADR-017 · Guidance enrichment: the model extracts, the code constrains, the engine decides (2026-10-06)

Validated LLM statements reach the materiality rules through two separate steps.
`radar llm-extract --events` runs the guidance extraction on the documents behind the
stored earnings_release events and stores every statement with its two level validation,
its computed figures and its provenance (llm_statements: call id, model, prompt and schema
versions, verified span). It never writes to an event. `radar llm-apply` then builds a
GuidanceEnrichment from the VALID statements only and applies it:

- statements on the same metric and period must agree after unit normalisation; a
  conflict blocks that metric and is audited, the others still apply;
- among the cuts on revenue, EBITDA or free cash flow, the governing metric is the one
  whose simulated rule is the most severe (ERN-02 before ERN-03), then the larger
  comparable magnitude, then the fixed order revenue, EBITDA, free cash flow; a cut whose
  magnitude is not comparable to the percent threshold keeps guidance_change_pct null and
  falls under ERN-03 (rules 1.5);
- reaffirmed with no cut gives ERN-04; raised, new or mentioned alone decide nothing; every
  statement stays in fields.llm_guidance for the audit;
- only empty deterministic fields are filled; one disagreement with a deterministic value
  keeps the whole deterministic reading and records the conflict; flags are never touched;
- the event keeps extraction_method structured and gains enrichment_method llm_validated,
  so the explanation can say what detected the event, what enriched it and what decided;
- evidence spans of type llm_statement are the document's own passages at the verified
  offsets, never the model's text;
- the application is idempotent twice over: a key on the statement ids and a hash of the
  applied payload, so a later extraction with new statement ids but the same content is
  reported as the same semantic enrichment;
- the engine decides again with rules 1.5 and the audit records the priority before and
  after.

End to end tests cover a quantitative cut at and below the threshold, an explicit
reaffirmation, a mentioned statement, a scope violation, an invalid statement, a replay
from the cache at zero cost and the protection of a deterministic field.

## ADR-018 · Liquidity statements: the model qualifies, the code checks the polarity and sets the flag (Phase 3.4a, 2026-10-06)

Each risk family gets its own schema and prompt rather than one generic extraction. For
liquidity the model reports every statement of the issuer about its own liquidity and
qualifies it from the words of the passage: deteriorated, concern, stable, improved or
mentioned. The code then checks that qualification. A negative status is accepted only when
the passage holds a deterioration or worry wording that is not negated in its sentence;
"we have no liquidity concerns" read as concern is rejected as POLARITY_MISMATCH, read as
stable or mentioned it is valid. Positive and neutral statuses are never rejected on
polarity: a wrong positive reading costs a miss, never a false alert. Span, entity, date,
segment scope, numbers and unit are checked as for guidance.

The flag is set by code alone: fields.flags gains "liquidity" when at least one VALID
statement has a negative status at Group level, which ERN-01 then turns into a P1. Generic
mentions ("liquidity risk management") stay recorded as mentioned and never set the flag. A
deterministic flag is never removed; when the model reads the same passage as stable, the
disagreement is recorded and shown. The deterministic liquidity pattern was tightened at the
same time: a bare "liquidity risk" of a risk section and a negated worry no longer raise it.
Covenants and going concern will follow the same mould, with the negated going concern
sentence of the OMV report as a permanent test.

## ADR-019 · Covenant statements: a stated breach stays a breach, its resolution is kept apart (Phase 3.4b, 2026-10-06)

The covenant family follows the liquidity mould with one schema of its own: a status
(compliant, risk_of_breach, breached, mentioned) and a resolution (none, waived, cured,
amended). A breach actually stated stays "breached" whatever happened next: for a credit
analyst the breach is the event, the waiver or the cure is the context, so the code sets
the covenant flag on a stated breach and the explanation names the resolution ("flag:
covenant (resolution waived)"). A preventive amendment or a waiver without a breach
demonstrated is a mention with a resolution and never a flag; an anticipated breach is
recorded without a flag in V1.

The validator checks the wording per status: a breach needs a non-compliance, breach,
violation or failure to meet stated for the issuer, neither negated nor hypothetical; an
event of default counts only when the window ties it to a covenant or a non-compliance,
because a payment default is not a covenant breach; "compliant" is contradicted by a
stated breach; "risk_of_breach" needs an anticipation; the resolution needs its word.
The shared checks apply (span, entity, dates, segment scope including the segment as the
sentence's subject, agency report as a third party document). The deterministic covenant
flag was tightened the same way: a waiver, an amendment or a definition alone no longer
raises it.

Both flag families share one enrichment (the flag follows a validated negative status, a
deterministic flag is never removed, disagreements are recorded) and one scorer (statement
precision, recall and status accuracy, negative statement precision and recall, document
flag precision, recall and false positive rate, resolution accuracy for covenants), per
split. The gold set V1 is frozen before any call; the final blind holdout and the runs wait
for API credits, in the agreed order: the four Sol documents of liquidity holdout V3, then
Terra on covenants, the dev reading, Sol, the blind holdout, the closing.

Frozen on 2026-10-06 after the blind holdout V2 (docs/BENCHMARK.md): five filings chosen from
EDGAR metadata, Sol flags the three breach cases and Terra two, neither sets a false flag on
the two controls. Nothing was tuned on the holdout. The limits recorded there are the first
items of a validator V2 to decide before the going concern family: a resolution without its
word should be salvaged like the agreement and the covenant label instead of rejecting the
breach, and a future covenant test date inside a breach stated in the past tense should not
reject it. The decision proposed, and not yet implemented, is Sol as the default extractor
for this family with Terra as the challenger, one model per extraction kind.

## ADR-020 · One routed model per extraction kind, with its reason in the audit (2026-10-07)

The benchmarks of 2026-10-06 gave a different answer per family: Terra with the
deterministic guardrails for guidance (precision 1.000, recall 0.965, the lower cost),
Terra for liquidity (same stress cases found, lower cost), Sol for covenants (four of four
breach cases on the two holdouts against two of four, no false flag for either, about
twice the cost). One global extraction model would have forced one family to accept the
worse reading. The settings now route each kind: `llm.routing.<kind>` names a default and
a challenger among `llm.benchmark_alternatives`, with the reason the benchmarks gave;
`TO_BENCHMARK` refuses the default until the family has its benchmark (going concern).

The reason travels with the statements, not only the model's name: `radar llm-extract`
records the selection (kind, role, name, model, reason) in run.json, in every stored
statement (schema 9, `llm_statements.model_selection_json`), in the audit log line of the
extraction and in the event's provenance, and the explanation prints "Model chosen: <model>
as <role> for <kind>: <reason>" under each "Enriched by" line. `--challenger` runs the
routed challenger, `--alternative <name>` any configured entry and says so in the reason.
The covenant conclusion stays phrased as what it is: on covenant gold V1 and its holdouts,
Sol gives the better decision-level recall without an observed rise in false flags, not a
general claim.

## ADR-021 · Going concern statements: the strictest family, one wording rule shared by the deterministic flag and the validator (Phase 3.4c, 2026-10-07)

The last flag family follows the liquidity and covenant mould with four statuses: doubt
(stated for now), alleviated (management's plans alleviate it, stated as a conclusion),
negated (the doubt or the impact denied), mentioned (the basis of preparation, the
description of the evaluation, a hypothetical, a dependency). The code sets the flag on
doubt alone and feeds ERN-01; an alleviated doubt is recorded on the event without a flag,
so the history of the doubt is kept; negated and mentioned set nothing.

The liquidity lesson is written once, in `radar.extract.going_concern`, and read by both
passes: a modal before the doubt wording ("could raise substantial doubt"), a condition
("if we cannot raise capital"), or the description of the evaluation ("evaluated whether
there are conditions that raise substantial doubt") is a hypothetical, not a doubt, unless
a conclusion word precedes ("management has concluded that the company may be unable to
continue as a going concern" is a doubt); "plans to alleviate", "will mitigate" and the
standard's own test are not an alleviation; "may not alleviate", "do not alleviate", "has
not been alleviated" keep the doubt. The deterministic extractor (structured-earnings-1.2)
now skips those passages with a reason (hypothetical_flag, alleviated_flag, negated_flag)
instead of raising a P1 the enrichment could never remove; the OMV "not impacted" sentence
stays the permanent control of both passes.

Gold V1 (lock 92aca395): 19 documents already in hand, labelled again on their own merits,
126 statements, 66 doubt, 9 documents with the flag, 14 dev and 5 holdout. Future dates in
a going concern passage are legitimate for every status (the assessment looks a year ahead
by construction); the period named by the model is still checked. The routing keeps
TO_BENCHMARK for this kind until its benchmark: `radar llm-extract --kind going_concern`
refuses the default and takes `--alternative`. Dry runs, no call: Terra 9.82 USD estimated
(about 2 USD calibrated on the covenant V1 ratio), Sol 17.15 USD estimated (about 3.4 USD).

Closed on 2026-10-07 after the Terra run on gold V1 (docs/BENCHMARK.md): seven of seven
doubt cases on the dev split and two of two on the holdout split, no false flag on ten
documents without a doubt, the OMV denial read as negated, no correction justified by the
dev split, nothing changed after the holdout was read. Terra is the routed default of the
family without a benchmarked challenger: Sol was deliberately not run, to control the
evaluation cost, and no other paid benchmark follows. With this family the extraction
layer is complete (ratings, guidance, liquidity, covenants, going concern); the effort
moves to what an analyst sees, the alert, the explanation, the source proof and the
interface.

## ADR-022 · The Alert object is the contract of the product layer (Phase P1, 2026-10-07)

Everything an analyst sees is built from one object, `radar.alerts.model.Alert`, assembled
from a stored decision, its event, the source documents and the issuer: the priority and
the status, the issuer and its universe (live watchlist, historical stress case, other,
from the universe tags, never mixed), a title written by code from the event fields, the
facts as the documents' own passages tied to numbered sources with offsets and hashes, the
triggered rules with their description and reason, the rating state after the action, the
model provenance per family (model, prompt, schema, routed role and benchmark reason) and
the decision provenance (decided by the rules engine, composite never used, the LLM role).
The full "Why this priority?" text travels with it. The alert id is a stable hash of the
event, the priority, the rules version, the triggered rules and the fact offsets, so the
same decision yields the same alert.

Renders are code, not prompts: a self-contained HTML page with no external resource and
every value escaped (the local channel, always on, SPEC 11.4), an Adaptive Card 1.4 with
the badge, three sourced facts, the rules, the provenance and the links to the sources and
to the viewer's notes, wrapped as a Teams Workflows message. `radar alert` writes the
three files under outputs/alerts/<date>/ and records the route and the files in the audit
log; Teams and e-mail are sent only with `--send` and when .env names a webhook or an SMTP
host, never by default. The viewer (P2) and the committee note (P3) consume the same
object.

## ADR-023 · A minimal viewer over the Alert object, two universes never mixed (Phase P2, 2026-10-07)

The viewer is Streamlit, four screens and no more: a dashboard (counts, latest alerts,
the routed model per family with its reason), the watchlist, the alerts and the event
detail. The event detail is the screen that matters: the badge, the title, "Why this
priority?" with each triggered rule and its reason, the rating state after the action,
the provenance lines (decided by the rules engine, composite never used, the LLM never
decides), the evidence as verbatim passages opened in their source context with the
passage highlighted, the sources with their hashes, the model provenance and the audit
trail. It reads the database only, through one data layer shared with the static export
(`radar export-html`), the portable fallback that needs no server and serves the README
captures.

The live watchlist (Volkswagen, TRATON, OMV) and the historical stress cases (public
filings of issuers outside the watchlist, used as controls for the detectors) are two
sections everywhere, from the universe tags, so that no control issuer can be read as a
position. The composite shown on the watchlist is the structural one from the seed, with
its basis ("seed, not signed off" until rows are GOLDEN); it is metadata and never a rule
input (ADR-001).

## ADR-024 · Periodic reports are results publications; flag hardening on long filings; cache-only replays (2026-10-07)

A 10-Q or 10-K went through the EDGAR item mapping only (8-K items, prospectus forms), so
a quarterly report carried no earnings event and the flag families could not attach to it
in the pipeline. The quarterly or annual report is a results publication in substance
(SPEC 6.1 names the 8-K 2.02; the report is the document behind it), so
`extract_periodic_report` now yields one earnings_release event per 10-Q or 10-K, dated at
filing, with the period read from the cover ("for the quarterly period ended June 30,
2026", which is the event's own passage) and no guidance reading (a report does not
guide). No new event type: the same family, type, rules and enrichment apply. The scan is
one function shared by both extractors (structured-earnings-1.3).

Integrating the reports exposed a weakness of the simple deterministic patterns: the
generic risk factors of a 10-Q ("we could face liquidity constraints", "breaches of our
covenants could ...") matched the liquidity and covenant patterns and raised false
positives on Deere, PACCAR, General Motors and Compass Diversified, filings where the
validated statements of the LLM path find no flag. On periodic reports, the liquidity,
covenant and impairment families therefore rely on the validated LLM statements only,
while the going concern keeps its strict deterministic path (the shared wording rules of
ADR-021: a doubt stated for now, not hypothetical, not described, not alleviated). The
candidates set aside are recorded in the audit with their reason. Results releases, short
and factual, keep the deterministic flags of every family. A local test on the real
filings guards the behaviour. The "Liquidity and Going Concern" section heading is also
kept out of the liquidity pattern.

`radar llm-extract --cache-only` replays the cached answers of the benchmark runs on the
pipeline events with a provider that refuses every call and a budget below any estimate:
the demo database (`scripts/build_demo_db.py`) is built from the fixtures in hand and the
cache at zero cost, then applied, decided, alerted and exported.

## ADR-025 · The committee note is deterministic first; model prose is verified sentence by sentence or excluded (Phase P3, 2026-10-07)

`radar note <event_id> --lang fr|en --no-llm` produces a complete note from the Alert
object without any key: header (issuer, universe, priority and status, event, effective
date, ratings after the action, the composite named as never used), the key facts as
verbatim passages numbered and tied to their sources, "why it matters" as one claim per
triggered rule (the rules engine's own reason), the key indicators from the event's
fields or "not available in the sources", the points to verify as open questions per
family and flag, the triggered rules, the sources with their hashes, and the verification
count. The model improves the restitution, it is never needed to know the facts.

Without `--no-llm`, the notes model (Terra, settings.llm.roles.notes) writes the two prose
sections from the verified facts only, numbered as the note cites them. Each generated
sentence passes the deterministic controls of SPEC 10: a citation [n] is present, every
cited passage exists, every figure of the sentence appears in the cited passages or the
event header. A sentence that fails is UNSUPPORTED: excluded from the body, listed in the
annex with its reason, counted. No model judges a claim, no confidence is self-declared.
One cap covers every note of the demo (DEMO_GENERATION_BUDGET_USD, a ledger under
outputs/notes): beyond it the prose is refused before any call and the note says so. The
viewer renders the deterministic note for the "Note FR" and "Note EN" links of the Teams
card. Known limit: the rule descriptions quoted in the French note are the English text
of rules.yaml.

