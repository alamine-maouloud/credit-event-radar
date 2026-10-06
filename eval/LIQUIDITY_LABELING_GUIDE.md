# Labeling guide, liquidity gold set V1

Scope: the issuer's own statements about its liquidity in results releases, quarterly or
interim reports and SEC filings. Agency reports (S&P, DBRS) are third parties: what they
say about the issuer's liquidity is never an eligible statement and is listed under
`ineligible` with the reason `third_party`. The same holds for a subsidiary's or a segment's
liquidity (`segment`) and for sentences that name liquidity without speaking of it
(`not_liquidity`: a heading, an investment policy objective, a covenant definition).

## 1. Document-level fields

| field | rule |
|---|---|
| `split` | `dev` or `holdout`. Holdout documents are never read while a prompt is being adjusted; results are reported separately. |
| `expected_flag` | `true` when at least one eligible statement has status `deteriorated` or `concern`; `false` otherwise. The code sets `liquidity_flag` from validated statements with the same rule. |

## 2. Statement-level fields

One statement per passage, status from the words of the passage only:

| status | wording |
|---|---|
| `deteriorated` | liquidity worsened: tightened, constrained, strained, reduced, continued reduction in liquidity, further reducing liquidity |
| `concern` | a risk, a doubt or a pressure on liquidity: may not have sufficient liquidity, does not expect to have sufficient liquidity, may strain liquidity, could face liquidity constraints, going concern language tied to liquidity |
| `stable` | solid, strong, comfortable, sufficient, ample, unchanged, "no liquidity concerns" |
| `improved` | strengthened, increased, improving |
| `mentioned` | liquidity named without an assessment of its state: a policy objective ("continues to pursue its objective of maintaining a solid financing and liquidity policy"), a monitoring statement, a forecast range for net liquidity, a plan "intended to improve liquidity" that asserts nothing about the current state, a generic risk factor |

Rules:
- The label must be demonstrable from the span alone. A negated worry is `stable`
  (or `mentioned`), never `concern`.
- A plan or an initiative "to improve liquidity" is `mentioned` unless the same passage
  states the current deterioration or worry (then that status).
- Forward-looking risk factors ("may adversely affect our liquidity") are `concern` only
  when the passage ties the risk to the issuer's present situation (covenant breach
  expected, insufficient liquidity projected). A generic risk factor about a possible
  future event is `mentioned`.
- `metric_label`, `value`, `unit` are filled only when the span gives a figure for a
  liquidity metric (net liquidity, cash and cash equivalents) in a schema unit.
- `evidence_quote` is verbatim, the shortest span that holds the status wording;
  offsets are computed by `scripts/gold_liquidity.py`, never by hand.
- A sentence repeated identically (a note and the MD&A of a 10-Q) is labelled once, on its
  first occurrence; a prediction quoting the identical later occurrence still matches it.
  Variants with different pronouns ("the Company" and "we") are distinct statements.

## 3. Known limits of V1

Three documents carry real negative statements (GoPro, Microvast, Chicago Rivet, 10-Q
filings of 2026). The set measures false positives, polarity, scope and the detection of
these stress cases; it does not measure recall on a wide variety of stress wordings.

## 4. Freezing

Guide, documents (hashes), labels and the split are frozen before the first call. A
correction after model outputs have been seen creates liquidity_v2.
