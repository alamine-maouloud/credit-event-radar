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

Method: each `source_url` was opened on 2026-10-06 from a clean browser session (no login),
or fetched with curl for PDF files. A level is granted only when the page shows the item
explicitly. "Type not labelled" means the page gives a long-term rating without saying
whether it is an issuer credit rating, an issuer default rating, a senior rating or an
insurer financial strength rating.

| Issuer | Agency | Level reached | Finding |
|---|---|---|---|
| VOLKSWAGEN | Fitch, Moody's, S&P, DBRS | ENTITY_VERIFIED | Entity "Volkswagen AG" and all four ratings and outlooks shown. Type not labelled (column "Long-Term" only). Moody's date 2025-03 not on the page (latest Moody's document is 2026-05-20); S&P 2025-12 consistent with the 2025-12-18 RatingsDirect release. Promotion needs the agency release PDFs listed on the page. |
| TRATON | S&P, Moody's | DATE_VERIFIED | "External issuer credit ratings" of TRATON SE, BBB negative and Baa2 stable; report dates 2026-07-30 and 2025-12-08 match. |
| MAGNA | S&P, Moody's | ENTITY_VERIFIED | Both ratings quoted in management webcast transcripts filed on EDGAR ("A- credit rating", "A3 credit rating", outlook improved to stable). Secondary source: no rating type, no action date. Replace with Magna's ratings page or the agency releases. |
| MAGNA | DBRS | DATE_VERIFIED | DBRS issuer page: "Issuer Rating, LT, Confirmed, A, Stable, Apr 17, 2026". |
| BNP_PARIBAS_CARDIF | S&P | ENTITY_VERIFIED | Business report 2025 shows "Standard & Poor's Global Ratings A / stable, October 2025" in Cardif key figures. Type not labelled: for an insurer this is likely the financial strength rating, not an issuer credit rating. The same document also quotes the BNP Paribas group's A+. Type must be confirmed before promotion. |
| DEUTSCHE_BANK | S&P, Fitch | SOURCE_VERIFIED | investorrelations.db.com refused the connection from this environment (browser and curl). Not re-checked, no promotion. |
| COMMERZBANK | S&P | TYPE_VERIFIED | "Long-term Issuer Credit Rating / Preferred senior unsecured debt: A stable". Report list did not load, date 2026-08-11 not visible. |
| COMMERZBANK | Moody's | DATE_VERIFIED | "Long-term Issuer Credit Rating: A2 stable"; "Rating Action German Banks, 21 Apr 2026" matches. |
| INTESA_SANPAOLO | Fitch, Moody's, DBRS, S&P | DATE_VERIFIED | All four labelled "Long-term senior preferred (unsecured)" with the outlooks in the seed; no date shown, seed dates empty. Correctly typed `senior_preferred`, therefore not composite-eligible. An issuer-level rating still has to be sourced. |
| EFG_INTERNATIONAL | Moody's | ENTITY_VERIFIED | Page distinguishes EFG International (A3, negative) from EFG Bank (Aa3). Type not labelled ("Long term"). |
| EFG_INTERNATIONAL | Fitch | removed | No Fitch rating on the cited page. Row removed from the seed; re-add only with a public source. |
| DASSAULT_SYSTEMES | S&P (issuer) | DATE_VERIFIED | "Corporate Rating": A, Stable, November 27th 2025, "for Dassault Systèmes SE and its long-term credit". |
| DASSAULT_SYSTEMES | S&P (bond) | DATE_VERIFIED | Press release of 2026-06-15: 5-year senior unsecured bond rated A. Outlook cell emptied: an instrument rating carries no outlook (the release's "stable" refers to the company). |
| ROQUETTE | S&P | removed | The download URL returns a login page. Not a public source. Row removed; issuer marked `unverified` in the universe. |
| OMV | Moody's | DATE_VERIFIED | "Senior Unsecured Issuer Rating" A3 Stable, June 1 2026. Kept as `senior_unsecured` (not composite-eligible). |
| OMV | Fitch | DATE_VERIFIED | "Issuer Default Rating" A- Stable, July 9 2026. |
| VAR_ENERGI | Moody's | DATE_VERIFIED | "Baa3 long-term issuer rating with stable outlook" for Vår Energi ASA; no date, seed date empty. |
| VAR_ENERGI | S&P, Fitch | ENTITY_VERIFIED | "BBB Stable Outlook rating" for both; type not labelled. Fitch date 2026-07-02 not on the page. |
| MOEVE | Moody's, Fitch | ENTITY_VERIFIED | Baa3 Stable and BBB- Stable, "Last update April 2026". Page names "Moeve" without legal form; type not labelled. |

Open items before any row becomes GOLDEN (owner decision):

1. Confirm the rating type of the Cardif row (financial strength vs issuer credit rating).
2. Source an issuer-level rating for Intesa Sanpaolo, or accept that it has none in the demo.
3. Replace the Magna transcript sources with the ratings page or agency releases.
4. Re-check Deutsche Bank from a network that reaches investorrelations.db.com.
5. For Volkswagen, EFG, Vår Energi and Moeve, open the agency release PDFs to label the type.
6. Find a public source for Roquette and for the removed EFG Fitch rating, or leave them out.
