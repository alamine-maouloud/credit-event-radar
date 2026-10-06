# Labeling guide, covenant gold set V1

Scope: the issuer's own statements about the covenants of its debt agreements in results
releases, quarterly or interim reports and SEC filings. Agency reports are third parties
(`ineligible`, reason `third_party`); a subsidiary's or a segment's covenants are
`segment`; a sentence that names covenants of another agreement than the issuer's debt
(a purchase agreement's representations and covenants, a lease) is `not_covenant`.

No liquidity label is reused: a sentence labelled in the liquidity set is labelled again
here on its own merits, or not at all.

## 1. Document-level fields

| field | rule |
|---|---|
| `split` | `dev` or `holdout`. Holdout documents are never read while a prompt is adjusted. |
| `expected_flag` | `true` when at least one eligible statement has status `breached`, whatever its resolution. The code sets the covenant flag from validated statements with the same rule. |

## 2. Statement-level fields

One statement per passage. `status` from the words of the passage only:

| status | wording |
|---|---|
| `breached` | the issuer was or is not in compliance with a covenant, breached or violated it, failed to meet it, or an event of default is tied in the passage to a covenant or a non-compliance. A breach stays `breached` when the same passage says it was later waived, cured or amended. |
| `risk_of_breach` | a future non-compliance or breach is anticipated: will not remain in compliance, does not expect to be able to meet, may breach, risk of non-compliance. |
| `compliant` | the issuer was or is in compliance with its covenants, or no default or breach occurred. |
| `mentioned` | covenants named without a compliance statement: a definition or description of an agreement, covenant levels, an amendment that provides headroom without any breach, a generic risk factor. |

`resolution`, one of `none`, `waived`, `cured`, `amended`: what the passage says happened
to the breach or to the agreement. `waived` needs a waiver word, `cured` a cure or remedy
word, `amended` an amendment word; `none` otherwise.

Rules:
- The label must be demonstrable from the span alone. A negated breach ("no breach of any
  covenant") is `compliant`, never `breached`.
- An event of default is `breached` only when the passage ties it to a covenant or a
  non-compliance; a payment default alone is not a covenant statement (`not_covenant`).
- A passage that states both a current compliance and a stated breach of another covenant
  is `breached` (the breach dominates); a passage with a stated breach and an anticipated
  one is `breached`.
- A preventive amendment or a waiver without any breach demonstrated is `mentioned`
  with `resolution amended` or `waived`; it never sets the flag.
- `covenant_label` and `agreement` are filled only when the passage names them exactly.
- `evidence_quote` is verbatim, the shortest span that holds the status wording and, when
  the breach and its resolution sit in adjacent sentences, both; offsets are computed by
  `scripts/gold_covenant.py`, never by hand. Identical repeats are labelled once.

## 3. Freezing

Guide, documents (hashes), labels and the split are frozen before the first call. A
correction after model outputs have been seen creates covenant_v2.
