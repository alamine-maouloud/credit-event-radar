# Labeling guide, going concern gold set V1

Scope: the issuer's own statements about its ability to continue as a going concern in
results releases, quarterly or interim reports and SEC filings. The strictest family of
the three: the flag follows only an explicit, non-negated doubt stated for the issuer.
Agency reports are third parties (`ineligible`, `third_party`); a subsidiary's going
concern is `segment`; a "significant uncertainty" about a tariff refund, a litigation or a
market, outside the going concern, is `not_going_concern`.

No liquidity or covenant label is reused: a sentence labelled in another set is labelled
again here on its own merits, or not at all.

## 1. Document-level fields

| field | rule |
|---|---|
| `split` | `dev` or `holdout`. Holdout documents are never read while a rule is adjusted. |
| `expected_flag` | `true` when at least one eligible statement has status `doubt`; `false` otherwise. The code sets the going concern flag from validated statements with the same rule. |

## 2. Statement-level fields

One statement per passage. `status` from the words of the passage only:

| status | wording |
|---|---|
| `doubt` | substantial doubt (or a material uncertainty) about the issuer's ability to continue as a going concern exists, is raised by conditions and events, remains, is not alleviated by management's plans, or is presupposed as existing ("the substantial doubt about our ability to continue as a going concern may adversely affect ..."). "Plans may not alleviate", "do not alleviate", "has not been alleviated" are `doubt`. |
| `alleviated` | management's plans alleviate or mitigate the doubt, stated as a conclusion ("these plans alleviate the substantial doubt", "the substantial doubt has been alleviated"). The doubt existed; the code records it without a flag (V1), the explanation names it. |
| `negated` | the passage denies the doubt or the impact: "ability to continue as a going concern is not impacted", "no substantial doubt", "does not cast significant doubt", "no longer raise substantial doubt". Never a flag. |
| `mentioned` | the going concern named without an assessment: the basis of preparation ("prepared on a going concern basis", "prepared assuming the Company will continue as a going concern"), the description of the evaluation management performs ("evaluated whether there are conditions ... that raise substantial doubt"), the standard's test ("when implemented, the plans will mitigate the conditions that raise substantial doubt"), a hypothetical ("if ..., there could be substantial doubt", "should we be unable to continue as a going concern", "would be materially and adversely impacted"), a dependency ("our ability to continue as a going concern is dependent upon ..."), a list item that names the going concern without the doubt. |

Rules:
- The label must be demonstrable from the span alone.
- A hypothetical risk is not a doubt stated (the liquidity lesson): a modal, a condition or
  the evaluation description before the doubt wording makes the passage `mentioned`, unless
  a conclusion word precedes ("management has concluded that the company may be unable to
  continue as a going concern" is `doubt`).
- A statement that plans "are intended to", "may not" or "do not" alleviate the doubt, with
  the doubt named in the same span, is `doubt`. "Plans to alleviate the doubt may not be
  successful" is `doubt`.
- Identical repeats (note and MD&A) are labelled once, on the first occurrence; variants
  ("the Company" and "we") are distinct statements. Headings, cross-references ("see
  Liquidity and going concern") and fragments split by a page header are not labelled.
- A doubt dated more than a year before the document is a historical reference, never a
  flag; the gold refuses it as `doubt`.
- Wrapped lines are quoted as printed (line breaks inside the quote).

## 3. Permanent control cases

- OMV Q4 2024 report: "From today’s perspective, we assume that based on the measures
  listed above, the Company’s ability to continue as a going concern is not impacted."
  The negated sentence that produced the first false P1 of the project: `negated`, no flag,
  in the dev split for ever.
- GoPro, Microvast, Hydrofarm, Chicago Rivet, FTC Solar, Boxlight, American Shared Hospital
  Services (dev), New Fortress Energy and Sleep Number (holdout): explicit substantial doubt,
  not alleviated: `doubt`. Their basis-of-preparation and evaluation sentences are
  `mentioned`.
- Compass Diversified: a going concern basis and a "significant uncertainty" about tariff
  refunds: no doubt, no flag.
- Harley-Davidson, Caterpillar, Ford, Volkswagen, OMV 2025 reports (dev), Deere, General
  Motors, TRATON (holdout): no going concern wording; documents without statement.
- No real `alleviated` conclusion exists in these documents: the status is covered by the
  offline tests only, a limit of V1.

## 4. Freezing

Guide, documents (hashes), labels and the split are frozen before the first call. A
correction after model outputs have been seen creates going_concern_v2.
