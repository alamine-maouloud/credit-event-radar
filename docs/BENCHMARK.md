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

### Decision (validated 2026-10-06)

For the decisional task, setting liquidity_flag for ERN-01, Terra plus the deterministic
guardrails offers the best precision and cost compromise on Liquidity Gold V1: the same
three true positives as Sol, no false flag on the 17 documents without one, at about half
the price. Sol is better at exhaustive extraction of the individual signals (negative
statement recall 0.72 against 0.44, higher status accuracy), but that advantage does not
translate into a better decision on this benchmark and it introduces one false positive on
the holdout. Terra stays the default backend for liquidity, Sol the challenger. The
conclusion holds for this V1 set and its three stress cases only; the final blind test is
the holdout V2 below.

### Holdout V2, the blind test (2026-10-06)

Five filings chosen from EDGAR metadata only, never read before their labels were written,
frozen with lock 7333d639; after the freeze nothing changed (prompt 1.0.0, schema
liquidity-1.0, validator, polarity rules, labels). Terra 0.71 USD, Sol 1.40 USD.

| Document | Expected | Terra | Sol | Validated negative statements Terra / Sol |
|---|---|---|---|---|
| New Fortress Energy, 10-Q 2026-08-06 (restructuring after defaults) | FLAG | FLAG | FLAG | 3 / 4 |
| Sleep Number, 10-Q 2026-05-12 (going concern considerations) | FLAG | FLAG | FLAG | 3 / 4 |
| Caterpillar, 10-Q 2026-08-05 | NO FLAG | NO FLAG | NO FLAG | 0 / 0 |
| Deere, 10-Q 2026-08-27 | NO FLAG | FLAG | FLAG | 1 / 1 |
| Ford Motor, 10-Q 2026-07-29 (strong liquidity, many risk factors) | NO FLAG | NO FLAG | FLAG | 0 / 1 |

Statement level: Terra precision 0.44, recall 0.50, status accuracy 0.75; Sol precision
0.54, recall 0.62, status accuracy 0.73. Negative statements: Terra 3 of 6 found, Sol 4 of
6. Sol proposed four Ford Credit sentences that the scope guard rejected as a segment;
Terra proposed none.

Reading, document by document:

- Both models find the two real stress cases. New Fortress Energy is found on the right
  sentences ("not probable to be sufficient", "substantial doubt ... satisfy our liquidity
  needs"). Sleep Number is found on the right sentences but validated for the wrong reason:
  the filing's HTML wraps sentences across lines, the sentence splitter cuts at the line
  break, and the negation "are not" and its object "expected to be sufficient" fall in
  different fragments; the quotes passed because they also contain the heading "Going
  Concern Considerations", whose word "Concern" is a negative marker. The labelled quote of
  the same sentence, without the heading, had failed the same check at build time, which
  was recorded before the run.
- The false flags come from one failure mode shared by both models: a generic,
  forward-looking risk factor read as a present worry. Deere: "Lower credit ratings
  generally result in higher borrowing costs ... and may adversely impact our liquidity"
  (both models). Ford: "Pension and other postretirement liabilities could adversely affect
  Ford's liquidity" (Sol). The validator accepts them because "adversely impact" and
  "adversely affect" are negative markers; nothing in the code distinguishes a conditional
  risk factor from a stated deterioration. The same mode produced Sol's false flag on
  Harley-Davidson in the V1 holdout.

Verdict against the Phase 3.4a exit criterion (no false P1, every real stress case
found): the real stress cases are found by both models, but the "no false P1" half is not
met: Terra sets one false flag on three documents without a problem, Sol two. Phase 3.4a
is not closed on this test. The next step is a deterministic rule, not a prompt change:
a passage whose only negative wording is a conditional "may / could / might ... adversely
affect or impact" with no present-tense deterioration is not a worry, and the sentence
splitter must not cut at a line break inside a wrapped sentence. Both go in with tests, both
runs of V1 and V2 are re-scored from the cache, and a fresh blind holdout V3 gives the final
test. V1 and V2 results stay as they are.

### Closing rules and holdout V3, the final test (2026-10-06)

Two rules were added after V2, with tests first and no prompt change: a window whose only
negative wording is a conditional "may, could, might or would adversely affect or impact"
is a HYPOTHETICAL_RISK (the statement stays valid as mentioned, never a flag), the
reduction marker requires liquidity as its object, and sentence-window-1.1 no longer ends a
window at a line break inside a wrapped sentence (text and hashes unchanged). V1 and V2
re-scored from the cache under these rules (runs suffixed closing-rules): V2 is 5 of 5 for
both models, V1 keeps 3 of 3 with no false flag on 17 documents for both, Sol's false flag
on Harley-Davidson is gone. Negative statement recall drops (Terra 0.39, Sol 0.61) because
the GoPro and Microvast sentences "would further adversely impact liquidity", labelled
concern in gold V1, are hypothetical under the rule; the gold is not edited.

Holdout V3, five filings chosen from EDGAR metadata, labelled before any call, lock
10c407c6, distribution as found: one real problem, two stable issuers, two documents with
stress vocabulary and no current deterioration statement.

| Document | Expected | Terra | Sol |
|---|---|---|---|
| Hydrofarm, 10-Q 2026-08-14 (substantial doubt, event of default) | FLAG | FLAG | FLAG |
| Compass Diversified, 10-Q 2026-08-10 (2025 forbearance, amended covenants) | NO FLAG | NO FLAG | not run |
| PACCAR, 10-Q 2026-07-29 | NO FLAG | NO FLAG | not run |
| Cummins, 10-Q 2026-08-04 | NO FLAG | NO FLAG | not run |
| General Motors, 10-Q 2026-07-21 (GM Financial segment) | NO FLAG | FLAG | not run |

Terra: 0.62 USD, statement precision 0.61, recall 0.42, status accuracy 0.73. Sol: the
OpenAI account ran out of credits after the first document (HTTP 429, insufficient quota);
the four remaining documents are provider errors in the run directory and will be run when
credits are available, without any change in between.

Reading:

- The real stress case is found by both models on the right sentences (substantial doubt
  tied to cash flows and debt due within twelve months).
- Terra's false flag on General Motors is boilerplate: "Our liquidity plans are subject to a
  number of risks and uncertainties, including those described in the Forward-Looking
  Statements section", read as concern and accepted because "uncertainties" is a negative
  marker. The closing rule handles conditional adverse effects, not this cross-reference
  wording. By the protocol agreed before V3, it is not corrected: it is the first recorded
  limit of Liquidity V1.
- The Compass Diversified sentence "our ability to maintain adequate liquidity and comply
  with the amended covenants will depend on our operating performance" is a worry in
  substance that carries none of the validator's markers; both the gold (mentioned) and the
  models leave it without a flag. Second recorded limit: a worry expressed as a dependency,
  without negation or deterioration words, is invisible to the polarity check.

### Liquidity V1 closed, with its limits

Across V1 dev, V1 holdout, V2 and V3 under the closing rules, Terra plus the guardrails finds
every real stress case (3 of 3, 2 of 2, 1 of 1) and sets one false flag on 24 documents
without a problem (General Motors, boilerplate cross-reference); Sol finds every stress
case it was run on and sets no false flag under the closing rules, at about twice the
price, with four V3 documents still to run. The decision of Phase 3.4a stands: Terra plus
guardrails is the default backend for liquidity_flag, Sol the challenger. Known limits,
documented rather than tuned away: boilerplate "risks and uncertainties" cross-references
can pass as a worry; a worry expressed as a dependency without negative words is not seen;
quotes that include a "Going Concern" heading pass the polarity check on the heading's
word; the per-statement precision figures understate both models because gold V1 omits
several true worries. Further changes to the liquidity rules belong to a V2 of the gold and
of the rules, not to this phase.

## Covenant gold set V1, Terra, DEV iteration (2026-10-06)

Frozen set eval/gold/covenant_v1.jsonl, lock 39080bfa: 18 documents, 13 dev and 5 holdout,
43 statements of which 11 breached, 3 documents expected to carry the flag. Prompt
extraction.covenant 1.0.0, schema covenant-1.0, effort low. Terra ran once (1.83 USD); the
run was then re-scored from the cache after two validator corrections motivated by the
dev split only. The holdout is reported for completeness and was not used for anything.

| Level | covenant-terra-2026-10-06 (gpt-5.6-terra) | covenant-terra-2026-10-06-revalidated (gpt-5.6-terra) |
|---|---|---|
| [dev] documents | 13 | 13 |
| [dev] Extraction: statement recall | 0.222 | 0.306 |
| [dev] Extraction: statement precision (valid) | 0.727 | 0.647 |
| [dev] Qualification: status accuracy | 0.875 | 0.909 |
| [dev] Qualification: resolution accuracy | 0.875 | 0.727 |
| [dev] Qualification: negative statement precision | 0.667 | 0.714 |
| [dev] Qualification: negative statement recall | 0.200 | 0.500 |
| [dev] Decision: document flag precision | 1.000 | 1.000 |
| [dev] Decision: document flag recall | 0.500 | 1.000 |
| [dev] Decision: false flag rate on documents without flag | 0.000 (of 11) | 0.000 (of 11) |
| [holdout] documents | 5 | 5 |
| [holdout] Extraction: statement recall | 0.286 | 0.429 |
| [holdout] Extraction: statement precision (valid) | 0.500 | 0.429 |
| [holdout] Qualification: status accuracy | 1.000 | 0.667 |
| [holdout] Qualification: resolution accuracy | 1.000 | 0.667 |
| [holdout] Qualification: negative statement precision | n/a | n/a |
| [holdout] Qualification: negative statement recall | 0.000 | 0.000 |
| [holdout] Decision: document flag precision | n/a | n/a |
| [holdout] Decision: document flag recall | 0.000 | 0.000 |
| [holdout] Decision: false flag rate on documents without flag | 0.000 (of 4) | 0.000 (of 4) |
| Cost USD | 1.83 | 0.00 |

| Document | split | expected | covenant-terra-2026-10-06 | covenant-terra-2026-10-06-revalidated |
|---|---|---|---|---|
| C-GPRO-01 | dev | FLAG | FLAG | FLAG |
| C-CVR-01 | dev | FLAG | NO FLAG | FLAG |
| C-MVST-01 | dev | NO FLAG | NO FLAG | NO FLAG |
| C-HYFM-01 | dev | NO FLAG | NO FLAG | NO FLAG |
| C-CODI-01 | dev | NO FLAG | NO FLAG | NO FLAG |
| C-GM-01 | dev | NO FLAG | NO FLAG | NO FLAG |
| C-CAT-01 | dev | NO FLAG | NO FLAG | NO FLAG |
| C-F-01 | dev | NO FLAG | NO FLAG | NO FLAG |
| C-PCAR-01 | dev | NO FLAG | NO FLAG | NO FLAG |
| C-CMI-01 | dev | NO FLAG | NO FLAG | NO FLAG |
| C-VW-FY25 | dev | NO FLAG | NO FLAG | NO FLAG |
| C-OMV-Q4-25 | dev | NO FLAG | NO FLAG | NO FLAG |
| C-VW-DEL-H1-26 | dev | NO FLAG | NO FLAG | NO FLAG |
| C-NFE-01 | holdout | FLAG | NO FLAG | NO FLAG |
| C-SNBR-01 | holdout | NO FLAG | NO FLAG | NO FLAG |
| C-HOG-01 | holdout | NO FLAG | NO FLAG | NO FLAG |
| C-DE-01 | holdout | NO FLAG | NO FLAG | NO FLAG |
| C-TR-H1-26 | holdout | NO FLAG | NO FLAG | NO FLAG |

### What the dev split showed, and what changed

- Terra is conservative: it proposes few statements (23 for 36 gold on dev) and sets no
  false flag on the 11 dev documents without a problem. The problem was not the model
  inventing breaches but the validator discarding good extractions before the engine.
- Six rejections came from one auxiliary field: the model named the agreement ("March 2025
  Credit Agreement") outside the quoted sentence, and that alone invalidated four correct
  breaches of Chicago Rivet, the whole of the missing flag. Correction: field-level salvage.
  An auxiliary field the quote does not support (agreement, covenant_label) is dropped and
  audited; the statement stays valid when its span, status, entity and scope hold. The
  decisional fields are never salvaged.
- One rejection was a date after the document inside a passage that describes future
  covenant test periods. Correction: a future date is legitimate when the passage is
  prospective and claims no breach already realised; a breach claimed at a future date
  stays inconsistent. (That particular passage stays invalid for other reasons.)
- Result on dev, same answers: flag precision 1.000 and recall 0.500 become 1.000 and
  1.000, no new false flag; negative statement recall 0.20 becomes 0.50; resolution
  accuracy drops from 0.875 to 0.727 because the recovered Chicago Rivet statements carry
  the model's "waived" where the gold says "none" (the waiver sits in the next sentence),
  a qualification nuance that changes no decision.

### Status and resolution mismatches of the dev split, case by case (no change made)

| Document | Citation | Gold | Terra | Validator | Reading |
|---|---|---|---|---|---|
| Caterpillar | "consolidated net worth was $19.463 billion, which was above the $9.000 billion required covenant in the Credit Facility" | mentioned / none | compliant / none | STATUS_MISMATCH, no compliance statement | A level above the requirement is compliance in substance; the validator only knows "in compliance" wording. Gold V1 convention (levels are mentions) is debatable, noted for V2; no decision changes either way. |
| Caterpillar | "Cat Financial's covenant interest coverage ratio was 1.54 to 1. This was above the 1.15 to 1 minimum ratio" | unlabelled (segment) | compliant / none | STATUS_MISMATCH | Same wording question; the passage is about a segment, which the scope guard did not catch because the segment is not the sentence's first word. |
| Caterpillar | "Cat Financial's six-month covenant leverage ratio was 7.96 to 1. This was below the maximum ratio" | unlabelled (segment) | compliant / none | STATUS_MISMATCH | Same as above. |
| Ford | "The corporate credit facility contains a liquidity covenant that requires us to maintain a minimum of $4 billion" | mentioned / none | mentioned / amended | RESOLUTION_MISMATCH, no amendment word | The model's resolution is unsupported by the span: a qualification error of the model, kept as such. |

Remaining misses on dev are sentences the model did not propose (three GoPro breaches, one
Chicago Rivet), a recall limit of the model, not of the validator.

### Covenant V1: Terra against Sol under the final DEV rules (2026-10-06)

Two more defects were found on the dev split once Sol had run, both corrected with tests
and applied to both models from the cache: "we were not in compliance" was read as
hypothetical because "were" sat in the hypothetical prefixes, and "anticipate
non-compliance" was not an anticipation. Four runs are kept: each model raw and each model
re-validated under the final rules.

| Level | covenant-terra-2026-10-06 (gpt-5.6-terra) | covenant-terra-2026-10-06-revalidated-3 (gpt-5.6-terra) | covenant-sol-2026-10-06 (gpt-5.6-sol) | covenant-sol-2026-10-06-revalidated (gpt-5.6-sol) |
|---|---|---|---|---|
| [dev] documents | 13 | 13 | 13 | 13 |
| [dev] Extraction: statement recall | 0.222 | 0.333 | 0.583 | 0.583 |
| [dev] Extraction: statement precision (valid) | 0.727 | 0.667 | 0.477 | 0.447 |
| [dev] Qualification: status accuracy | 0.875 | 0.833 | 0.905 | 0.905 |
| [dev] Qualification: resolution accuracy | 0.875 | 0.750 | 0.810 | 0.810 |
| [dev] Qualification: negative statement precision | 0.667 | 0.714 | 0.421 | 0.400 |
| [dev] Qualification: negative statement recall | 0.200 | 0.500 | 0.800 | 0.800 |
| [dev] Decision: document flag precision | 1.000 | 1.000 | 1.000 | 1.000 |
| [dev] Decision: document flag recall | 0.500 | 1.000 | 1.000 | 1.000 |
| [dev] Decision: false flag rate on documents without flag | 0.000 (of 11) | 0.000 (of 11) | 0.000 (of 11) | 0.000 (of 11) |
| [holdout] documents | 5 | 5 | 5 | 5 |
| [holdout] Extraction: statement recall | 0.286 | 0.429 | 0.429 | 0.429 |
| [holdout] Extraction: statement precision (valid) | 0.500 | 0.429 | 0.750 | 0.750 |
| [holdout] Qualification: status accuracy | 1.000 | 0.667 | 1.000 | 1.000 |
| [holdout] Qualification: resolution accuracy | 1.000 | 0.667 | 1.000 | 1.000 |
| [holdout] Qualification: negative statement precision | n/a | n/a | 1.000 | 1.000 |
| [holdout] Qualification: negative statement recall | 0.000 | 0.000 | 1.000 | 1.000 |
| [holdout] Decision: document flag precision | n/a | n/a | 1.000 | 1.000 |
| [holdout] Decision: document flag recall | 0.000 | 0.000 | 1.000 | 1.000 |
| [holdout] Decision: false flag rate on documents without flag | 0.000 (of 4) | 0.000 (of 4) | 0.000 (of 4) | 0.000 (of 4) |
| Cost USD | 1.83 | 0.00 | 3.68 | 0.00 |

| Document | split | expected | covenant-terra-2026-10-06 | covenant-terra-2026-10-06-revalidated-3 | covenant-sol-2026-10-06 | covenant-sol-2026-10-06-revalidated |
|---|---|---|---|---|---|---|
| C-GPRO-01 | dev | FLAG | FLAG | FLAG | FLAG | FLAG |
| C-CVR-01 | dev | FLAG | NO FLAG | FLAG | FLAG | FLAG |
| C-MVST-01 | dev | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-HYFM-01 | dev | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-CODI-01 | dev | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-GM-01 | dev | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-CAT-01 | dev | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-F-01 | dev | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-PCAR-01 | dev | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-CMI-01 | dev | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-VW-FY25 | dev | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-OMV-Q4-25 | dev | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-VW-DEL-H1-26 | dev | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-NFE-01 | holdout | FLAG | NO FLAG | NO FLAG | FLAG | FLAG |
| C-SNBR-01 | holdout | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-HOG-01 | holdout | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-DE-01 | holdout | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |
| C-TR-H1-26 | holdout | NO FLAG | NO FLAG | NO FLAG | NO FLAG | NO FLAG |

Reading, by level:

- Decision: on the dev split both models find the two breach cases (GoPro, Chicago Rivet)
  and set no false flag on the 11 documents without a problem. On the holdout, reported
  but never used, Sol finds New Fortress Energy's non-compliance with covenant requirements
  and Terra does not propose that sentence at all; neither sets a false flag on Sleep
  Number (forbearance on "Specified Defaults" never tied to a covenant), Harley-Davidson,
  Deere or TRATON.
- Qualification: Sol reads status and resolution better (0.905 and 0.810 against 0.833 and
  0.750) and finds four of the five breached statements of the dev split against two and a
  half for Terra (negative recall 0.80 against 0.50).
- Extraction: Sol proposes many more passages (statement recall 0.58 against 0.33); its
  lower precision is largely gold V1 omissions: the GoPro note and MD&A say the same
  breaches twice with "the Company" and with "we", and several of the "we" variants are
  not labelled, so Sol's correct quotes of them count against it. Noted for a V2.
- Cost: Terra 1.83 USD, Sol 3.68 USD for the same 18 documents.

Notes for a gold V2, no edit of V1: label the "we" variants of the GoPro breaches; levels
reported above a covenant requirement are compliance, not a mention (Caterpillar); the
covenant table of Compass Diversified (requirements against actual ratios) is a compliance
statement the validator cannot read.

The decision between the models waits for the blind holdout: on the dev split they are
equal at the decisional level, and the one holdout breach separates them on a single
document.

### Covenant holdout V2, the blind test (2026-10-06): Phase 3.4b frozen

Selection, from EDGAR metadata only, at commit b73f2f1 (the final DEV rules): the 10-Q
filings since May 2026 outside the universe hit by three covenant stress full-text queries
("not in compliance" "financial covenants"; "covenant" "waiver" "not in compliance";
"covenant" "event of default" "forbearance"), ranked by the number of queries hit then by
filing date (Boxlight, three queries; American Shared Hospital Services and FTC Solar, two
queries, the most recent), plus the latest 10-Q of two investment grade industrials as
controls (Honeywell, Emerson). Nothing was read before the labels; no prompt, schema,
validator, guide, segment or label changed after the selection; the gold was frozen (lock
5176f873, 91 statements, 38 breached, 19 ineligible passages) before the first call.
Distribution as found: three issuers with stated current breaches under waivers or
forbearance, two filings that never speak of debt covenants, no trap document this time.
Passages the frozen wording rules cannot read were labelled mentioned by the V1 convention
and listed as limits in the gold notes before the runs.

| Level | covenant-holdout-v2-terra-2026-10-06 (gpt-5.6-terra) | covenant-holdout-v2-sol-2026-10-06 (gpt-5.6-sol) |
|---|---|---|
| [holdout] documents | 5 | 5 |
| [holdout] Extraction: statement recall | 0.066 | 0.121 |
| [holdout] Extraction: statement precision (valid) | 0.750 | 0.846 |
| [holdout] Qualification: status accuracy | 0.833 | 0.909 |
| [holdout] Qualification: resolution accuracy | 0.333 | 0.545 |
| [holdout] Qualification: negative statement precision | 0.600 | 0.857 |
| [holdout] Qualification: negative statement recall | 0.079 | 0.158 |
| [holdout] Decision: document flag precision | 1.000 | 1.000 |
| [holdout] Decision: document flag recall | 0.667 | 1.000 |
| [holdout] Decision: false flag rate on documents without flag | 0.000 (of 2) | 0.000 (of 2) |
| Cost USD | 0.50 | 1.04 |

| Document | split | expected | covenant-holdout-v2-terra-2026-10-06 | covenant-holdout-v2-sol-2026-10-06 |
|---|---|---|---|---|
| CH2-BOXL | holdout | FLAG | NO FLAG | FLAG |
| CH2-AMS | holdout | FLAG | FLAG | FLAG |
| CH2-FTCI | holdout | FLAG | FLAG | FLAG |
| CH2-HON | holdout | NO FLAG | NO FLAG | NO FLAG |
| CH2-EMR | holdout | NO FLAG | NO FLAG | NO FLAG |

Terra, document by document (0.50 USD, five documents, 16 statements proposed, 8 valid):

- CH2-BOXL (Boxlight, 10-Q of 2026-08-14), expected flag, Terra: NO FLAG. Terra proposed
  seven passages. Three recall the 2024 and early 2025 breaches and were rejected as history
  (HISTORICAL_REFERENCE), one quotes the waivers of 2026 whose wording ("borrowing base
  defaults", "Minimum Consolidated Adjusted EBITDA defaults") the frozen rules cannot read as
  a breach, one is the risk factor on future compliance read as a risk of breach where the
  gold says breached (the passage states the past instances of non-compliance). The decisive
  loss: the one current breach Terra found, "The Company was not in compliance with the
  Senior Leverage Ratio covenant as of September 30, 2025 and ... the borrowing base covenant
  ... through November 30, 2025", was quoted together with the following sentence on the
  Tenth Amendment and given the resolution "waived" taken from that context; the frozen
  validator rejects a resolution without its word in the passage and drops the whole
  statement with it. The document loses its only validated breach and the flag.
- CH2-AMS (American Shared Hospital Services, 10-Q of 2026-08-13), expected flag, Terra:
  FLAG. Two of the ten breach statements found (the December 2025 notice of an event of
  default tied to the Minimum Cash Covenant, the December 2025 and May 2026 lender notices),
  the September 2025 limited waiver correctly read as a mention; two proposals quote HoldCo's
  non-compliance with the DFC Loan, a subsidiary's covenant the frozen scope guard cannot
  reject because HoldCo was not a configured segment at selection.
- CH2-FTCI (FTC Solar, 10-Q of 2026-08-05), expected flag, Terra: FLAG. One validated breach
  (the minimum unrestricted cash covenant at June 30, 2026, waived on August 4, 2026) out of
  nineteen; the Second Amendment passage (breach of the purchase order covenant, waived) was
  rejected because it names a covenant test period in 2027 and the prospective exception
  covers non-breached statuses only; the "uncertainty as to our ability to fully meet all
  existing ... financial covenants" sentence was proposed as a risk of breach and rejected,
  the gold labels it mentioned for the same reason (no anticipation word the rules read).
- CH2-HON (Honeywell, 10-Q of 2026-07-23) and CH2-EMR (Emerson, 10-Q of 2026-08-04),
  no flag expected, Terra: no statement at all on either, no flag.

Sol, document by document (1.04 USD, 34 statements proposed, 13 valid):

- CH2-BOXL, expected flag, Sol: FLAG. Twenty passages proposed, five valid. Sol quotes the
  current breach sentence on its own (Senior Leverage Ratio at September 30, 2025, borrowing
  base through November 30, 2025) with the resolution none, which the validator accepts, and
  the document gets its flag. The rest is the same picture as Terra: the 2024 and early 2025
  breaches rejected as history, the 2026 waivers of "defaults" rejected for want of a breach
  word, the Tenth Amendment waiver of "Specified Events of Default" rejected for a resolution
  without its word, the two risk factors on future compliance read as a risk of breach. Sol
  quotes the breach sentence and the compliance sentence twice, from the note and from the
  MD&A: the gold labels identical repeats once, so the second quotes count against precision
  without being errors.
- CH2-AMS, expected flag, Sol: FLAG. Five of the ten breach statements found, two of them
  rejected because the quoted passage runs into the Standstill Period "until June 30, 2027"
  and the prospective exception covers non-breached statuses only; the payment default at
  maturity is quoted together with the covenant non-compliance it precedes (matched to the
  gold breach). No HoldCo passage proposed.
- CH2-FTCI, expected flag, Sol: FLAG. Two validated breaches (minimum unrestricted cash,
  direct tracker margin, both waived on August 4, 2026); the Second Amendment passage was
  rejected for the same 2027 test date as with Terra; the "uncertainty as to our ability to
  fully meet" sentences proposed as a risk of breach and rejected.
- CH2-HON and CH2-EMR, no flag expected, Sol: no statement, no flag.

What the blind holdout says, by level:

- Decision: Sol flags the three breach cases and sets no false flag on the two controls;
  Terra flags two of three and sets no false flag. Over the two covenant holdouts (V1 and
  V2, six documents without a breach, four with), Sol is at four of four and Terra at two of
  four, both at zero false flags. On covenant gold V1 and its two holdouts, Sol gives the
  better decision-level recall with no observed rise in false flags, at about twice the cost.
  A result on this gold set, not a claim that Sol reads covenants better in general.
- Qualification: on the matched statements Sol reads the status better (0.909 against 0.833)
  and the resolution better (0.545 against 0.333); both models over-read a resolution from
  the surrounding context ("waived" on a sentence that has no waiver word).
- Extraction: both models find a small share of the 91 labelled statements (Sol 0.12, Terra
  0.07), because the filings repeat each breach in the notes, the MD&A, the risk factors and
  the subsequent events, and the models quote each breach once or twice; the valid
  statements are precise (Sol 0.85, Terra 0.75).
- Cost: Terra 0.50 USD, Sol 1.04 USD for the five documents (902 thousand characters).

Limits recorded at the freeze, nothing tuned (the first items of a validator V2, to decide
before the going concern family):

1. A resolution without its word rejects the whole statement. Terra's only current breach
   on Boxlight carried "waived" taken from the next sentence and was dropped with its breach,
   which cost the flag. The resolution is context, like the agreement and the covenant label:
   it should be salvaged to none and the breach kept.
2. A covenant test date after the document inside a breach passage rejects it, because the
   prospective exception covers non-breached statuses only (FTC Solar's Second Amendment with
   a purchase order covenant from March 31, 2027, both models; the AMS notices quoted with the
   Standstill Period to June 30, 2027, Sol). A breach stated in the past tense with a future
   test period in the same sentence is still a breach.
3. Wordings the rules do not read as a breach: "limited waiver of the borrowing base and
   Minimum Consolidated Adjusted EBITDA defaults" (defaults of a named covenant without the
   word covenant), "had not maintained compliance", "have not complied with certain
   covenants", "failing to meet the required ... covenants". Labelled mentioned by convention,
   they never set the flag.
4. "there is currently uncertainty as to our ability to fully meet all existing ... financial
   covenants": an anticipation in substance that the rules do not read; both models propose it
   as a risk of breach and are rejected.
5. A subsidiary's covenants (HoldCo under the DFC Loan) cannot be rejected without a
   configured segment: two Terra proposals scored as ineligible; the scope guard depends on
   configuration.
6. "Although the Company has obtained waivers and amendments with respect to each of the
   foregoing instances of non-compliance, there can be no guarantee that the Company will not
   breach ...": both models read a risk of breach, the gold reads the stated past
   non-compliance (breached, waived). A defensible disagreement, kept as labelled.

Decision (validated 2026-10-07, ADR-020): for the covenant family, Sol is the routed default
and Terra the challenger; guidance and liquidity keep Terra as default with Sol as
challenger; going concern waits for its benchmark (settings.llm.routing, the reason recorded
in every run, statement and explanation). Phase 3.4b is frozen on this holdout: the next
benchmark of the family will be a new gold version and a new holdout.

Covenant validator V2 backlog (not now: tuning further would fit the system to its holdouts
more than improve it): the resolution salvaged like the agreement and the covenant label;
a future covenant test date inside a breach stated in the past tense; the wordings "had not
maintained compliance", "have not complied", "failing to meet", "defaults" of a named
covenant; "uncertainty as to our ability to fully meet" as an anticipation; subsidiary
borrowers (HoldCo) as configurable scope.

### Going concern V1: gold frozen, no call yet (2026-10-07)

The last family. Gold V1 (eval/gold/going_concern_v1, lock 92aca395) re-labels nineteen
documents already in hand on their own merits: seven issuers with a substantial doubt not
alleviated (GoPro, Hydrofarm, Microvast, Chicago Rivet, FTC Solar, Boxlight, American Shared
Hospital Services) and the OMV Q4 2024 "not impacted" control in the dev split, New Fortress
Energy and Sleep Number with the doubt in the holdout, nine documents without going concern
wording (Compass Diversified, Harley-Davidson, Caterpillar, Ford, Volkswagen, OMV Q4 2025,
Deere, TRATON, General Motors). 126 statements, 66 doubt, 5 ineligible "significant
uncertainty" passages outside the going concern. No real "alleviated" conclusion exists in
these filings: the status is covered by the offline tests only, a limit noted in the guide.

Dry runs on the frozen set, no call made: Terra 9.82 USD estimated, Sol 17.15 USD estimated;
on covenant V1 the real cost was 0.20 of the estimate, so about 2.0 USD for Terra and 3.4
USD for Sol. The 1.9 USD of credits left cover neither run in full; the benchmark waits for
a reload. Order kept: Terra, Sol, the blind holdout on filings never read, the routed
default for the kind, Phase 3 closed.

