# Seed validation ladder

`data/seeds/ratings_seed.csv` is hand-collected from public issuer pages. Each row climbs
the ladder below. Levels are cumulative: a row at `DATE_VERIFIED` has passed the three
previous checks. Only `GOLDEN` rows feed the composite rating by default (ADR-003).

| Level | Check | Who |
|---|---|---|
| `SOURCE_VERIFIED` | The `source_url` is public, reachable without login, and states the rating and outlook written in the row. | Collector |
| `ENTITY_VERIFIED` | The rated legal entity on the page is exactly `legal_entity` (not a parent, a finance subsidiary or a sister company). | Reviewer |
| `TYPE_VERIFIED` | The page labels the rating with the same type as `rating_type` (issuer, issuer default, senior preferred, senior unsecured, instrument). | Reviewer |
| `DATE_VERIFIED` | `rating_date` matches the date shown by the source, with the same precision; an absent date stays empty. | Reviewer |
| `GOLDEN` | A human signs off the row as immutable reference data for the demo. | Owner |

Rules:

- A row is never promoted without the check being redone on the live page at the time of
  the pass; the pass date and findings are logged below.
- A failed check downgrades the row to the last level it passed and the finding is logged.
  Nothing is corrected silently: the fix is a new row or a corrected cell, with a log line.
- No rating, entity, type or date is ever completed from memory or by an LLM.

## Validation log

Pass 1, 2026-10-06, reviewer: project owner, browser check of every `source_url`.
Results are appended by the pass itself (see the table below once filled).
