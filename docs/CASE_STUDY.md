# Credit Event Radar: case study

An independent prototype built on public information only. No affiliation with any asset
manager; the demo universe and the stress cases come from public filings and investor
relations pages. Nothing here is an investment recommendation.

## Synthèse (FR)

Un analyste crédit obligataire suit des dizaines d'émetteurs dont les événements
significatifs (action de notation, résultats, émission) arrivent en ordre dispersé dans
des documents longs. Credit Event Radar les détecte dans l'information publique, les
priorise en P1, P2, P3 avec un moteur de règles déterministe, les explique règle par
règle, les relie à la phrase exacte du document source avec son hash, et les restitue en
alerte, en carte Teams et en note de comité FR/EN. Les modèles de langage y lisent et
citent ; ils n'attribuent jamais une priorité : une validation déterministe contrôle
chaque énoncé contre le texte, et les règles de crédit décident. Quatre familles
d'extraction ont été évaluées sur des jeux gold figés et des holdouts aveugles (guidance,
liquidité, covenants, going concern), avec un modèle routé par famille et la raison du
choix enregistrée dans chaque alerte. Résultat sur les cas de stress publics : les vrais
cas sont détectés, les contrôles négatifs ne lèvent pas d'alerte, et chaque limite connue
est documentée plutôt que corrigée après coup.

## 1. Problem

Credit-relevant events on a bond portfolio arrive scattered: an agency action in a
quarterly report's subsequent-events note, a going concern doubt in the notes of a 10-Q, a
covenant waiver in a credit agreement amendment, a guidance cut in a results release.
Reading everything is not possible; missing the one filing that matters is costly; and a
tool that cries wolf is switched off within a week. The analyst needs to know what
happened, how much it matters, why, and where it is written, with a draft they can take to
a committee.

The project asks a narrow question: can a small system monitor public information for a
watchlist, prioritise events with rules an analyst can read and contest, use language
models where they help (reading long text) and nowhere they hurt (deciding), and prove
every claim it makes?

## 2. The analyst's workflow, as assumed

The workflow below is a hypothesis built from public descriptions of fixed-income research
desks; it was not observed inside any firm.

1. Watch a list of issuers (investment grade, high yield, the boundary between them).
2. When something happens, assess it fast: does it change the credit view, is it
   priority one for today's meeting or a note for the weekly review?
3. Explain the assessment to the team with the source at hand.
4. Draft a committee note in the house language, then validate and sign it.

The prototype mirrors those four steps: detect, prioritise, explain and source, draft.

## 3. Architecture

```
public sources          deterministic layer                 LLM layer (reads only)
EDGAR 10-Q / 8-K  ──>  normalise, hash, store  ──>  rating actions, results, issuances
IR pages, PDFs          dedup, issuer resolution           flags (going concern, ...)
                                │                                    │
                                v                                    v
                      rules engine (rules.yaml)  <──  validated statements (code sets flags)
                      P1 / P2 / P3 + reasons                 cache, budget, audit
                                │
                                v
                 Alert object ──> HTML, Teams card, viewer, committee note FR/EN
```

Every document is snapshotted with its URL, retrieval time and two hashes (raw bytes,
normalised text). Deterministic extractors read rating actions (with the agency ratings
table of the filing), results releases and periodic reports (with their flags), and
issuances. A rules engine (`config/rules.yaml`, 19 rules and 3 modifiers) assigns P1, P2
or P3 with a reason per rule, from agency-level ratings only: the composite rating is
analytical metadata, never a decision input. The explanation panel lists every rule,
triggered or not, the rating state at the decision date (no look-ahead), the provenance
and the evidence offsets. An audit log records each step.

## 4. The hybrid approach: deterministic decisions, model reading

Language models read long documents well and decide badly under audit. The prototype
therefore gives them one job: propose statements with a verbatim quote and character
offsets, in a strict JSON schema, one schema per family (guidance, liquidity, covenant,
going concern). Code then validates each statement: the quote must exist at the offsets,
the numbers of the statement must be in the quote, the status must match the words of the
passage (a negated doubt is not a doubt, a hypothetical breach is not a breach, a
third-party report is not the issuer's voice, a segment is not the group), and the date
must be plausible. A validated negative status sets a flag; the rules engine does the
rest. A deterministic flag is never removed by a model, a disagreement is recorded, the
model's role is written in the provenance of every alert, with the benchmark reason for
choosing that model.

Three safeguards that mattered in practice:

- Polarity. "Liquidity may be adversely affected" is a risk factor, not a situation; the
  validator rejects a negative status on a hypothetical passage.
- Scope. A segment's or a subsidiary's figure is not the issuer's; configured segments are
  rejected as subject or holder of the passage.
- History. A breach or a doubt dated more than a year before the document is a mention of
  history, not a current event.

## 5. Evaluation

Each family was evaluated on a hand-labelled gold set frozen with hashes before the first
call, split into a development part and a holdout, then on blind holdouts chosen from
EDGAR metadata only and never read before their labels. Corrections came from the
development split only, with tests, and were re-applied from the cache at zero cost;
every run is kept as evidence. Two exact models were compared (Terra and Sol, OpenAI
gpt-5.6 family, strict structured outputs), at three levels: extraction (passages found),
qualification (status read), decision (flag per document, which is what fires P1).

## 6. Results

| Family | Gold and holdouts | Decision level | Model routed, reason |
|---|---|---|---|
| Guidance | 30 documents, 57 occurrences | precision 1.000, recall 0.965 (Terra with the scope guard); Sol 0.982 at 1.6 times the cost | Terra |
| Liquidity | 20 documents, two blind holdouts of 5 | every stress case found by both models; one false flag on a boilerplate "risks and uncertainties" sentence, documented | Terra |
| Covenants | 18 documents, one holdout of 5 then a blind holdout of 5 | Sol finds 4 of 4 breach cases, Terra 2 of 4, no false flag for either on 6 clean filings; Sol about twice the cost | Sol |
| Going concern | 19 documents | 7 of 7 doubt cases on the dev split, 2 of 2 on the holdout, 0 false flags on 10 filings without a doubt, 1.84 USD | Terra |

Total model spend for the whole evaluation, every kept run included: about 19 USD. The demo database is built
from the fixtures and the cached answers at zero cost.

## 7. Failure cases and limitations

- Statement recall is low where it does not matter: the models quote a breach or a doubt
  once where a filing repeats it ten times; the decision level is unaffected.
- Known false flag: General Motors, a liquidity "risks and uncertainties" sentence read as
  a concern. Kept as it is, documented, not featured in the demo.
- Known misses: a covenant breach whose resolution word sits in the next sentence was
  rejected with its resolution; a breach stated with a future covenant test date was
  rejected as inconsistent; wordings such as "had not maintained compliance" are not read.
  These form a documented validator backlog, deliberately not tuned after the holdouts.
- Periodic reports: the simple deterministic patterns for liquidity and covenants matched
  the generic risk factors of 10-Q filings; on reports those families now rely on the
  validated model statements only, the going concern keeps its strict deterministic path.
- No real "alleviated" going concern conclusion exists in the corpus; the status is
  covered by offline tests only.
- The seed ratings are sourced but not signed off: the composite is shown as structural
  and never used.
- The rule descriptions quoted in the French committee note are the English text of the
  rules file.

## 8. From prototype to production

What a production version would need, in order: issuer coverage and sources beyond
EDGAR and investor relations pages (market data and internal data through adapters), a
live run with measured latency and false-alert rates per issuer and per day, a review
loop where analysts confirm or contest each alert (the audit log already carries the
before and after of every enrichment), access control and retention for the stored
documents, model monitoring (prompt and schema versions are already pinned per call), and
a periodic re-benchmark when a model or a prompt changes, on the same frozen gold sets.
The roadmap in `docs/ROADMAP.md` lays this out over 180 days, as an illustration based on
public information only.
