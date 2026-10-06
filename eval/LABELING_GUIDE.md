# Labeling guide, guidance gold set V1

Status: DRAFT until frozen. Freezing writes `eval/gold/guidance_v1.lock.json` with the hash
of this guide, of every document and of the label file. After the freeze, any change is a
new version with a written justification in `eval/gold/CHANGELOG.md`, never a silent edit
made after seeing a model's output (docs/SPEC.md 15.2).

Scope of V1: explicit financial guidance given by the issuer about its own future results,
in results releases and trading updates of Volkswagen AG, TRATON SE and OMV AG. Agency
reports, bond releases and other press releases are out of scope for this gold set.

## 1. Document-level behaviour

Every document receives exactly one `behaviour`:

| behaviour | definition |
|---|---|
| `guidance_changed` | At least one occurrence whose status is `raised`, `cut` or `withdrawn`, or a `new` guidance replacing a stated earlier range. |
| `guidance_maintained` | At least one occurrence with status `reaffirmed` or `new` (first guidance of the year, no earlier range stated), and none with `raised`, `cut` or `withdrawn`. |
| `guidance_mentioned_not_actionable` | The text talks about outlook, guidance or expectations for the issuer but states no quantity that can be captured as a range or a point for a metric in scope (qualitative only, "at the lower end", "slightly below", "in line with the market"). No occurrence is labelled. |
| `no_guidance` | No forward-looking statement about the issuer's own results (deliveries or unit sales releases, dividend notices, project news). |

Statements about a subsidiary, a brand, a segment, a competitor or the market are not the
issuer's guidance: they never create an occurrence and never turn a document into
`guidance_mentioned_not_actionable` on their own.

## 2. Occurrence-level fields

One occurrence per metric and per period stated. A document may hold several occurrences
(revenue growth and operating margin and net cash flow, for instance). Fields:

| field | rule |
|---|---|
| `metric` | one of `revenue`, `ebitda`, `ebit`, `fcf`, `margin`, `capex`, `other`. `margin` covers operating return on sales and operating margin; `ebit` covers operating result, operating profit, clean CCS operating result; `fcf` covers free cash flow and net cash flow; `capex` covers investment ratio and organic capex. |
| `metric_label` | the metric exactly as written in the evidence, lower or upper case as in the text. |
| `basis` | `absolute` (an amount), `yoy_change_pct` (growth versus previous year in percent), `margin_pct` (a ratio in percent). |
| `unit` | `EUR_BN`, `EUR_MN`, `USD_BN`, `USD_MN`, `PCT`. A range given in billions stays in billions. |
| `period` | the fiscal period as written, normalised to the year (`2026`) or the year and half (`H2 2026`). |
| `current_lower`, `current_upper` | the range now stated, both equal for a point estimate; `null` when the text gives no number (then the occurrence does not exist, see section 1). |
| `previous_lower`, `previous_upper` | the earlier range only when the same document states it; otherwise `null`. Never filled from memory or from another document. |
| `status` | `raised`, `cut`, `reaffirmed`, `new`, `withdrawn`, `mentioned`. `new` when the text gives a range without saying it changed or was maintained. `reaffirmed` requires words such as confirms, maintains, reaffirms, unchanged, still expected, continues to expect, in line with the previous forecast; the wording may sit in an adjacent sentence of the same outlook paragraph. `raised`/`cut` require either explicit words or a stated earlier range that differs. |
| `evidence_quote` | verbatim sentence or sentences from the normalised text that contain the metric label, every number of the occurrence and the unit. Prefer the shortest span that holds them all. Copy characters exactly, including typographic quotes and signs such as `+3`. |
| `start_offset`, `end_offset` | character offsets of `evidence_quote` in the normalised text of the document (the text stored by the pipeline, `normalizer_version` recorded). Computed by `scripts/gold_guidance.py`, never by hand. |

Numbers: `0 and +3 percent` gives `current_lower 0`, `current_upper 3`, `basis yoy_change_pct`,
`unit PCT`. Signs come from the text (a dash before 10% gives `-10`, `+ 0%` gives `0`); the build script compares
absolute values against the quote. `in line with the previous year` for revenue is an occurrence
with both current bounds `null`: the statement is explicit but carries no written number. `between EUR 3 billion and EUR 6 billion` gives `3` and `6`, `EUR_BN`. `around
EUR 1 bn` gives `1` and `1`. `at least 5 percent` gives lower `5` and upper `null`.
`approximately`, `around`, `about` do not change the numbers. Percent signs and the words
percent, per cent are equivalent.

## 3. What is not an occurrence

- Expectations about markets, industry volumes, prices, exchange rates or raw materials.
- Statements about past periods, including the quarter being reported.
- Targets beyond a fiscal year horizon (a 2030 ambition) unless stated as guidance for a
  fiscal year.
- Dividend proposals, share buybacks, investment programmes without a result metric.
- Qualitative statements without a number: these make the document
  `guidance_mentioned_not_actionable` when nothing else applies.

## 4. Annotation procedure

1. Read the whole normalised text, not the title.
2. List every sentence with a forward-looking statement about the issuer's own results.
3. For each, decide whether a metric in scope and a number are stated. If yes, create an
   occurrence; if no, note it as qualitative.
4. Fill status from the words of the text and from an earlier range stated in the same
   document. When in doubt between `new` and `reaffirmed`, choose `new`.
5. Choose the evidence span, run the script to compute offsets and check that every number
   is inside the span.
6. Set the document behaviour from the occurrences.
7. Record open questions in the `notes` field rather than guessing.

## 5. Metrics for the evaluation (docs/SPEC.md 15.3)

Document level: behaviour accuracy, false positive rate on `no_guidance` and
`guidance_mentioned_not_actionable` documents. Occurrence level: precision and recall of
occurrences matched on (metric, period), exact match per field, span validity rate (the
model's quote found and validated), schema failure rate, latency, cost per document. End to
end: ERN-02/ERN-03/ERN-04 decisions compared with the decisions implied by the gold
occurrences, and P1 recall where applicable.
