# Labeling guide, going concern gold set V1 (DRAFT, prepared before Phase 3.4c, no code yet)

Scope: the issuer's own statements about its ability to continue as a going concern in
results releases, quarterly or interim reports and SEC filings. The strictest family of
the three: the flag follows only an explicit, non-negated doubt stated for the issuer.
Agency reports are third parties (`ineligible`, `third_party`); a subsidiary's going
concern is `segment`.

No liquidity or covenant label is reused: a sentence labelled in another set is labelled
again here on its own merits, or not at all.

## 1. Document-level fields

| field | rule |
|---|---|
| `split` | `dev` or `holdout`. |
| `expected_flag` | `true` when at least one eligible statement has status `doubt`; `false` otherwise. The code sets the going concern flag from validated statements with the same rule. |

## 2. Statement-level fields

One statement per passage. `status` from the words of the passage only:

| status | wording |
|---|---|
| `doubt` | substantial doubt (or material uncertainty) about the issuer's ability to continue as a going concern exists, is raised by conditions and events, or is not alleviated by management's plans. |
| `alleviated` | a doubt that management's plans alleviate or mitigate, stated as such ("these plans alleviate the substantial doubt"). The doubt existed; the flag question is decided by the code (V1: `alleviated` sets no flag, the explanation names it). |
| `negated` | the passage denies the doubt or the impact: "ability to continue as a going concern is not impacted", "no substantial doubt", "does not cast significant doubt". Never a flag. |
| `mentioned` | going concern named without an assessment: the basis of preparation ("prepared on a going concern basis", "prepared assuming the Company will continue as a going concern"), the description of the accounting evaluation, a forward-looking list item, a generic risk factor. |

Rules:
- The label must be demonstrable from the span alone.
- The description of the evaluation ("U.S. GAAP requires an evaluation of whether there
  are conditions or events ... that raise substantial doubt", "management has evaluated
  whether ...") is `mentioned`, not `doubt`.
- A statement that plans "are intended to" or "may not" alleviate the doubt, with the
  doubt stated in the same span, is `doubt`.
- Identical repeats (note and MD&A) are labelled once; variants are distinct statements.
- A doubt dated more than a year before the document is a historical reference, never a
  flag.

## 3. Permanent control cases (from the real documents already in hand)

- OMV Q4 2024 report: "From today's perspective, we assume that based on the measures
  listed above, the Company's ability to continue as a going concern is not impacted."
  The negated sentence that produced the first false P1 of the project: `negated`, no flag.
- Compass Diversified, Microvast, GoPro, Hydrofarm, New Fortress: basis-of-preparation
  sentences ("prepared on a going concern basis", "prepared assuming the Company will
  continue as a going concern") are `mentioned`, even in a document whose doubt is real.
- GoPro, Microvast, Chicago Rivet, Hydrofarm, Sleep Number, New Fortress: explicit
  substantial doubt, not alleviated: `doubt`.
- Harley-Davidson, Caterpillar, Deere, Ford, General Motors, PACCAR, Cummins, OMV 2025 and
  2026 reports, Volkswagen, TRATON: no going concern wording; documents without statement.

## 4. Freezing

Guide, documents (hashes), labels and the split are frozen before the first call; this
draft becomes V1 when Phase 3.4c starts, after the covenant benchmark.
