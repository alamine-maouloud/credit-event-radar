# Benchmark of the guidance extraction, gold set V1

Frozen gold set: eval/gold/guidance_v1.jsonl, lock ee2670bd (30 documents, 57 occurrences,
10 Volkswagen, 8 TRATON, 12 OMV). Prompt extraction.guidance 1.1.0, schema guidance-1.0,
validator at two levels, figures computed in code. Every run directory under eval/runs/
carries run.json, metrics.json and results.jsonl (sanitised: quotes replaced by their hash);
outputs.jsonl with the verbatim excerpts stays local.

No label was changed after a model output was seen. The validator was corrected once
(signed numbers printed with a figure dash or an en dash) and the run that exposed the
defect is kept unchanged next to the re-validated one, which replays the same cached
answers at zero cost.

## Terra, gpt-5.6-terra, reasoning effort low, 2026-10-06

29 real calls and one cache hit (the smoke test document), 266 282 input tokens, 58 942
output tokens including reasoning, 1.19 USD against a hard budget of 10 USD, no JSON
failure, no truncation at a ceiling of 32 768 output tokens.

| Run | Behaviour accuracy | Proposed | Valid | Matched | Precision (valid) | Precision (all) | Recall | F1 | Invalid span rate | Unsupported claim rate | Span exact |
|---|---|---|---|---|---|---|---|---|---|---|---|
| terra-full-2026-10-06 (validator before the sign fix) | 0.933 | 76 | 61 | 50 | 0.820 | 0.658 | 0.877 | 0.847 | 0.197 | 0.342 | 0.720 |
| terra-full-2026-10-06-revalidated (same answers, from the cache) | 0.967 | 76 | 66 | 55 | 0.833 | 0.724 | 0.965 | 0.894 | 0.132 | 0.276 | 0.709 |

### Per issuer (re-validated run)

| Issuer | Documents | Behaviour correct | Gold occurrences | Proposed | Valid | Matched | Precision (valid) | Recall |
|---|---|---|---|---|---|---|---|---|
| VOLKSWAGEN | 10 | 10 | 35 | 35 | 34 | 34 | 1.00 | 0.97 |
| TRATON | 8 | 8 | 15 | 16 | 14 | 14 | 1.00 | 0.93 |
| OMV | 12 | 11 | 7 | 25 | 18 | 7 | 0.39 | 1.00 |

### Cost and latency (first run, real calls)

| Issuer | Cost USD (first run) | Mean latency s | Max input tokens |
|---|---|---|---|
| VOLKSWAGEN | 0.393 | 37.2 | 4830 |
| TRATON | 0.273 | 29.7 | 5441 |
| OMV | 0.528 | 13.8 | 31295 |

### What the model got right

- Volkswagen: 34 of 35 occurrences matched with every field equal (status, bounds, unit,
  period, change_basis). The July 2025 cut with the earlier ranges in brackets, the
  "still expected" and "continues to expect" reaffirmations and the Automotive Division
  metrics are all read as the guide labels them.
- TRATON: 14 of 15, including the qualitative raise of July 2026 ("to the upper end of the
  previous ranges", previous bounds left null) and the quantitative cut of July 2025.
- OMV: all 7 Group organic CAPEX occurrences found in 80 to 120 thousand character reports,
  including the raise from EUR 3.2 bn to 3.4 bn with the earlier guidance quoted.
- The nine no_guidance documents returned has_guidance false with an empty list: no
  invented statement on deliveries, unit sales, financing or exploration releases.
- Every quoted span overlaps the gold span; 71 percent are character for character the
  gold span, the rest are longer passages that also hold the status wording.

### What the model got wrong

- Segment figures despite the prompt: 11 valid statements for OMV segment CAPEX
  (Chemicals, Fuels, Energy) that the guide excludes. They pass the validator because they
  are faithful quotes; they are the whole of the over-extraction and the reason OMV
  precision is 0.39. Six more OMV statements (exploration and appraisal expenditure as
  capex) were rejected by the validator's metric check.
- One non-verbatim quote (TRATON FY 2025: "unit sales and" dropped from the sentence),
  rejected by the span check with a fuzzy score below 90.
- Two unit errors on "mentioned" statements (EUR instead of percent for "in line with the
  previous year"), rejected by the unit check; one earlier range invented as 0 for a net
  cash flow "around EUR 0 billion".
- One behaviour miss: the OMV FY 2025 KPI page ("production reached the guided level")
  returned no guidance where the gold says mentioned, not actionable.

### What the validator got wrong, and the fix

The first scoring rejected six correct extractions of negative bounds (TRATON "5% to +5%" with a figure dash before the 5,
VW "3 to 0 percent" with an en dash before the 3) because the number pattern read the typographic dash as a
separator. Recall was 0.877 before the fix and 0.965 after, from the same cached answers.
The defect is covered by a regression test; the first run stays in the repository as
evidence of the correction.

### Reading for the materiality rules

With validated statements only, the document behaviour is correct on 29 of 30 documents
and every status and change_basis of a matched occurrence agrees with the gold. ERN-02,
ERN-03 and ERN-04 can therefore be fed by validated statements; the open question for the
pipeline is the over-extraction of segment figures, to be handled by the scope rule in
code (an occurrence whose label names a segment is metadata, never a trigger) rather than
by trusting the prompt.

## Sol, gpt-5.6-sol, reasoning effort low, 2026-10-06

Same gold set, documents, prompt 1.1.0, schema, effort and validator as the re-validated
Terra run, no deterministic scope guard yet. 30 real calls, 1.96 USD, no JSON failure, no
truncation, mean latency 24.8 s.

### Terra versus Sol, raw model output behind the same validator

| Metric | terra-full-2026-10-06-revalidated (gpt-5.6-terra) | sol-full-2026-10-06 (gpt-5.6-sol) |
|---|---|---|
| Gold occurrences found | 55/57 | 56/57 |
| Recall (occurrences) | 0.965 | 0.982 |
| Precision on validated statements | 0.833 | 1.000 |
| Precision on all statements | 0.724 | 0.949 |
| Documents with the correct behaviour | 29/30 | 29/30 |
| No-guidance documents with a false guidance | 0/9 | 0/9 |
| Statements proposed | 76 | 59 |
| Supported (validated and in the gold) | 55 | 56 |
| Scope violations (valid quote, outside the gold scope) | 11 | 0 |
| Field inconsistencies (numbers, unit or metric not in the quote) | 9 | 3 |
| Span failures (quote not in the document) | 1 | 0 |
| Ungrounded (other entity or impossible date) | 0 | 0 |
| Cost USD | 0.00 | 1.96 |
| Mean latency s | n/a | 24.8 |

Every statement is in exactly one row of the lower block. The 11 Terra "scope violations"
are faithful quotes of OMV segment CAPEX (Chemicals, Fuels, Energy) that the prompt and the
guide exclude: they are not hallucinations, they are a scope instruction not followed. Sol
followed it on all seven OMV reports and proposed 59 statements for 57 gold occurrences.

What separates the two models on this gold set:

- Scope discipline: 0 segment figures for Sol against 11 for Terra. This is the whole of
  the precision gap (1.000 against 0.833 on validated statements).
- Faithfulness of quotes: no span failure for Sol, one for Terra (a sentence shortened).
- Shared errors: both give a EUR unit to the VW "in line with the previous year" revenue
  mention (the only gold occurrence Sol misses), both report OMV exploration and appraisal
  expenditure as capex once or more (rejected by the metric check), both read the OMV
  FY 2025 KPI page as no guidance where the gold says mentioned, not actionable.
- Status reading: Sol labels two TRATON 9M 2025 occurrences `new` where the gold says
  `reaffirmed` ("continues to expect", "lower end of the guidance range"); the guide's own
  tie-break ("when in doubt, choose new") makes this a defensible reading, the gold stays.
- Cost: Sol costs 1.64 times Terra on the same documents (1.96 against 1.19 USD) for the
  same latency; the difference is the per-token price, not the volume of reasoning.

Reading for the pipeline: with validated statements only, both models give the right
document behaviour on 29 of 30 documents and agree with the gold on every bound, unit and
change_basis of a matched occurrence. The decision between them is economic once a
deterministic scope guard removes segment figures from both outputs (next step, re-scored
from the cache at zero cost).

## Raw model output versus model plus deterministic guardrails

After the Terra versus Sol comparison, the scope rule of the guide was added to the code:
issuers declare their principal division and their segment names in universe.yaml, and a
statement whose metric label names a segment, or whose metric sentence introduces one
("Organic CAPEX for Chemicals"), is rejected with OUT_OF_SCOPE_SEGMENT whatever the prompt
said. Both runs were then re-scored from the cache, same answers, zero cost.

| Metric | terra-full-2026-10-06-revalidated (gpt-5.6-terra) | terra-full-2026-10-06-guarded (gpt-5.6-terra) | sol-full-2026-10-06 (gpt-5.6-sol) | sol-full-2026-10-06-guarded (gpt-5.6-sol) |
|---|---|---|---|---|
| Gold occurrences found | 55/57 | 55/57 | 56/57 | 56/57 |
| Recall (occurrences) | 0.965 | 0.965 | 0.982 | 0.982 |
| Precision on validated statements | 0.833 | 1.000 | 1.000 | 1.000 |
| Precision on all statements | 0.724 | 0.724 | 0.949 | 0.949 |
| Documents with the correct behaviour | 29/30 | 29/30 | 29/30 | 29/30 |
| No-guidance documents with a false guidance | 0/9 | 0/9 | 0/9 | 0/9 |
| Statements proposed | 76 | 76 | 59 | 59 |
| Supported (validated and in the gold) | 55 | 55 | 56 | 56 |
| Scope violations (valid quote, outside the gold scope) | 11 | 0 | 0 | 0 |
| Scope violations rejected by the guard | 0 | 12 | 0 | 0 |
| Field inconsistencies (numbers, unit or metric not in the quote) | 9 | 8 | 3 | 3 |
| Span failures (quote not in the document) | 1 | 1 | 0 | 0 |
| Ungrounded (other entity or impossible date) | 0 | 0 | 0 | 0 |
| Cost USD | 0.00 | 0.00 | 1.96 | 0.00 |
| Mean latency s | n/a | n/a | 24.8 | n/a |

Reading:

- The guard closes the whole precision gap: Terra goes from 0.833 to 1.000 on validated
  statements, Sol stays at 1.000. Twelve Terra statements are now refused by code (the 11
  segment CAPEX figures and one Chemicals CAPEX line that the metric check had already
  caught for another reason); none of Sol's.
- Nothing else moves: recall, behaviour accuracy and the false positive count on
  no-guidance documents are unchanged, because the guard only touches statements that
  name a segment. Precision on all statements stays at 0.724 for Terra by construction:
  the model still proposed 76 statements, the code simply discards 21 of them.
- What remains between the models after the guard is one gold occurrence (Sol 56/57,
  Terra 55/57, Terra's extra miss being a shortened quote) and the price: Terra 1.19 USD,
  Sol 1.96 USD for the same 30 documents and the same latency.

Conclusion for the pipeline: with the two level validator and the scope guard, the
statements that reach the materiality rules are identical in quality for both models on
this gold set, every bound, unit, status and change_basis of a matched occurrence agreeing
with the gold. The remaining model errors are the two shared "mentioned" statements with a
EUR unit (rejected), one exploration expenditure labelled capex (rejected) and one KPI page
read as no guidance instead of mentioned. The cheaper model plus guardrails is the
economic choice for the extraction role; the benchmark can be replayed on a new gold
version or a new prompt with the same commands.

Decision (Phase 3.3, validated 2026-10-06): gpt-5.6-terra with the deterministic guardrails
is the default backend for guidance extraction, gpt-5.6-sol the reference and challenger
model for evaluation. This holds for gold set V1, 30 documents of three issuers, and is not
a universal conclusion: a new issuer, document type or gold version reopens it.

## Performance under the current code (validator and rules 1.5, re-scored from the cache)

The runs above are the performance at the time of the benchmark and stay untouched. Since
then the validator changed twice (a statement without numbers no longer needs a unit token,
and the scope guard) and rules.yaml moved to 1.5. The same cached answers re-scored under
the current code, zero cost, are the runs suffixed current-rules-1.5:

| Metric | terra-full-2026-10-06-guarded (gpt-5.6-terra) | terra-full-2026-10-06-current-rules-1.5 (gpt-5.6-terra) | sol-full-2026-10-06-guarded (gpt-5.6-sol) | sol-full-2026-10-06-current-rules-1.5 (gpt-5.6-sol) |
|---|---|---|---|---|
| Gold occurrences found | 55/57 | 56/57 | 56/57 | 57/57 |
| Recall (occurrences) | 0.965 | 0.982 | 0.982 | 1.000 |
| Precision on validated statements | 1.000 | 0.982 | 1.000 | 0.983 |
| Precision on all statements | 0.724 | 0.737 | 0.949 | 0.966 |
| Documents with the correct behaviour | 29/30 | 29/30 | 29/30 | 29/30 |
| No-guidance documents with a false guidance | 0/9 | 0/9 | 0/9 | 0/9 |
| Statements proposed | 76 | 76 | 59 | 59 |
| Supported (validated and in the gold) | 55 | 56 | 56 | 57 |
| Scope violations (valid quote, outside the gold scope) | 0 | 1 | 0 | 1 |
| Scope violations rejected by the guard | 12 | 12 | 0 | 0 |
| Field inconsistencies (numbers, unit or metric not in the quote) | 8 | 6 | 3 | 1 |
| Span failures (quote not in the document) | 1 | 1 | 0 | 0 |
| Ungrounded (other entity or impossible date) | 0 | 0 | 0 | 0 |
| Cost USD | 0.00 | 0.00 | 0.00 | 0.00 |
| Mean latency s | n/a | n/a | n/a | n/a |

What moved: the two "in line with the previous year" statements the models had given a EUR
unit are now accepted, since a statement without a number has no unit to support. The VW
Q3 2025 one matches its gold occurrence (recall 56/57 for Terra, 57/57 for Sol); the
TRATON H1 2026 one is a mention of net cash flow the gold did not label, counted as a scope
violation on both models (one each). Gold V1 stays as frozen; whether that mention belongs
in the gold is a question for a V2, not an edit.

## Real chain replay at zero cost (lot 3.3b, 2026-10-06)

A replay database was built from the private IR and gold fixtures (`radar ingest --source
fixtures`), the response cache was copied from the main database, and the chain ran with
the budget forced to 0.01 USD so that any non cached call would have been refused:
`radar process`, then `radar llm-extract --events` (19 documents, 19 cache hits, 0 calls,
0.00 USD, 60 statements stored, 42 valid), then `radar llm-apply` twice (15 events
enriched, then 15 already applied, 3 without a valid statement).

| Event | Deterministic reading | LLM statements | Decision |
|---|---|---|---|
| VW H1 2025 (2025-07-25) | guidance cut, no figure (ERN-03, P2) | net cash flow EUR 2 to 5 bn to 1 to 3 bn, quantitative | fcf -42.9 %, ERN-02, P2 becomes P1 |
| VW H1 2026 (2026-07-24) | guidance reaffirmed (ERN-04, P3) | revenue cut -3..0 against 0..+3, margin and capex reaffirmed | conflict on guidance_status, deterministic reading kept, P3, conflict shown |
| VW Q1 2026 (2026-04-30) | no guidance statement | five ranges, status new | nothing decisional, five statements recorded, no rule |
| TRATON Q1 2026 (2026-04-28) | guidance reaffirmed (P3) | two reaffirmed | consistent, P3 |
| TRATON H1 2026 (2026-07-22) | no guidance statement | two qualitative raises | nothing decisional, recorded, no rule |
| OMV quarterly reports (7) | no guidance statement | Group organic CAPEX (one raise) | nothing decisional, no rule |
| deliveries and KPI pages (3) | no guidance statement | none | no valid statement, untouched |

`radar show-event <id> --explain` now names the three actors: "Detected by: deterministic
extractor (structured, structured-earnings-1.1)", "Enriched by: validated LLM statements
(gpt-5.6-terra, prompt 1.1.0, schema guidance-1.0, 1 statement applied, 5 recorded)" and
"Priority decided by: deterministic rules engine (rules.yaml 1.5)", with the LLM evidence
span quoted from the document at its verified offsets.

## Liquidity gold set V1 (Phase 3.4a, 2026-10-06)

Frozen set eval/gold/liquidity_v1.jsonl, lock 051341a0: 20 documents, 15 dev and 5
holdout, 52 statements of which 18 negative, 9 ineligible passages (agency reports,
subsidiary instruments, investment policy), 3 documents that must carry the flag (GoPro,
Microvast and Chicago Rivet 10-Q filings of 2026, private fixtures). Prompt
extraction.liquidity 1.0.0, schema liquidity-1.0, reasoning effort low for both models.

Four results are kept, two per model: the raw run and the same cached answers re-scored
under the validator corrected after the Terra run (credit facilities and lines count as the
liquidity topic; a statement found in an agency report is THIRD_PARTY_DOCUMENT). Both
corrections were motivated by dev documents only (OMV debt page, DBRS report).

| Metric | Terra raw | Sol raw | Terra revalidated | Sol revalidated |
|---|---|---|---|---|
| [dev] documents | 15 | 15 | 15 | 15 |
| [dev] statements gold / proposed / valid / matched | 41 / 54 / 38 / 25 | 41 / 76 / 41 / 28 | 41 / 54 / 38 / 26 | 41 / 76 / 38 / 29 |
| [dev] statement precision (valid) | 0.658 | 0.683 | 0.684 | 0.763 |
| [dev] statement recall | 0.610 | 0.683 | 0.634 | 0.707 |
| [dev] status accuracy on matched | 0.760 | 0.857 | 0.769 | 0.862 |
| [dev] negative statements gold / valid / matched | 18 / 13 / 8 | 18 / 19 / 13 | 18 / 13 / 8 | 18 / 19 / 13 |
| [dev] negative statement precision | 0.615 | 0.684 | 0.615 | 0.684 |
| [dev] negative statement recall | 0.444 | 0.722 | 0.444 | 0.722 |
| [dev] document flag precision | 1.000 | 1.000 | 1.000 | 1.000 |
| [dev] document flag recall | 1.000 | 1.000 | 1.000 | 1.000 |
| [dev] false flag rate on documents without flag | 0.000 (of 12) | 0.000 (of 12) | 0.000 (of 12) | 0.000 (of 12) |
| [dev] ineligible passages proposed as valid | 3 | 3 | 0 | 0 |
| [holdout] documents | 5 | 5 | 5 | 5 |
| [holdout] statements gold / proposed / valid / matched | 11 / 16 / 7 / 7 | 11 / 14 / 8 / 7 | 11 / 16 / 7 / 7 | 11 / 14 / 8 / 7 |
| [holdout] statement precision (valid) | 1.000 | 0.875 | 1.000 | 0.875 |
| [holdout] statement recall | 0.636 | 0.636 | 0.636 | 0.636 |
| [holdout] status accuracy on matched | 0.571 | 0.429 | 0.571 | 0.429 |
| [holdout] negative statements gold / valid / matched | 0 / 0 / 0 | 0 / 1 / 0 | 0 / 0 / 0 | 0 / 1 / 0 |
| [holdout] negative statement precision | n/a | 0.000 | n/a | 0.000 |
| [holdout] negative statement recall | n/a | n/a | n/a | n/a |
| [holdout] document flag precision | n/a | 0.000 | n/a | 0.000 |
| [holdout] document flag recall | n/a | n/a | n/a | n/a |
| [holdout] false flag rate on documents without flag | 0.000 (of 5) | 0.200 (of 5) | 0.000 (of 5) | 0.200 (of 5) |
| [holdout] ineligible passages proposed as valid | 0 | 0 | 0 | 0 |
| cost USD | 0.96 | 1.82 | 0.00 | 0.00 |

### Reading

- Document flag, the output that feeds ERN-01: both models find the three stress cases
  and flag none of the 12 dev documents without a flag. On the holdout Terra flags nothing;
  Sol flags Harley-Davidson once, on a generic risk factor ("its liquidity could be
  adversely impacted by changes in tariffs, inflation, work stoppages ...") read as concern
  where the gold says mentioned. The validator accepts it because the wording holds a
  negative marker; whether a generic forward-looking risk factor is a worry is exactly the
  judgment the benchmark measures, and it is not encoded in code.
- Negative statements: Sol finds 13 of the 18 (recall 0.72) against 8 for Terra (0.44).
  Terra misses the "we" variants of the GoPro note and most Microvast risk sentences.
- Statement precision is understated for both models by omissions of gold V1: several
  validated negative predictions are true worries that the labels do not carry ("our
  projected cash flow may not be sufficient to fund operations and meet debt obligations
  over the next twelve months", "certain elements of these plans have not been fully
  implemented"). They count against precision here; a gold V2 should add them. The gold is
  not edited.
- Status accuracy: the main disagreement is a labeling convention. Both models read
  "continues to pursue its objective of maintaining a solid financing and liquidity policy"
  as stable where the guide says mentioned (five occurrences); the models' reading is
  defensible.
- The corrected validator removes the three DBRS passages accepted as the issuer's
  statements (ineligible proposed 3 to 0 for both) and recovers the OMV credit facilities
  sentence; nothing else moves, which is what a guardrail should do.
- Cost: Terra 0.96 USD, Sol 1.82 USD for the same 20 documents.

### Transparency on the holdout

The two post-run validator corrections come from dev documents, so the five holdout
documents remain unseen for them. One reserve: before the freeze, a sentence of the
Harley-Davidson 10-Q ("believes its current cash ... are sufficient to meet its liquidity
requirements") was used in a unit test as a positive example that must reject a negative
status. No rule was derived from it, but that document is not strictly never seen. A
holdout V2 of five documents never read, selected without any later change to prompt,
schema or validator, is the final blind test to run before Phase 3.4a is closed.

### Decision proposal

For the flag that reaches the rules, Terra plus the deterministic guardrails gives the same
three true positives and no false positive on 17 documents, at half the price; Sol reads
more of the individual negative sentences and sets one false flag on a generic risk factor.
Terra stays the default extraction backend for liquidity as for guidance, Sol the
challenger; the conclusion holds for this V1 set and its three stress cases only.
