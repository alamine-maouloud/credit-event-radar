# Demo script, two minutes

The spine of the presentation: one run of the viewer on the demo database, four featured
cases, each showing one behaviour. Everything on screen comes from public filings and the
deterministic rules; the model reads and quotes, the rules decide. No Rothschild branding,
no private data, no claim that is not on screen.

## Before recording

```bash
uv run python scripts/build_demo_db.py --fresh     # fixtures + cached model answers, zero cost
uv run radar viewer --db data/demo.db               # http://localhost:8501
```

Check `uv run radar events --db data/demo.db` once: the event ids below are stable hashes
of the issuer, the family, the type and the period, so they survive a rebuild. Open the
viewer in a window of at least 1280 x 800, light theme, sidebar visible. Keep `.env`,
terminals and file paths out of the frame.

| Case | Event id | What it shows |
|---|---|---|
| Harley-Davidson, S&P downgrade BBB- to BB+ (10-Q, July 2026) | `evt_9618eb5ad2d366339e69` | P1 by RAT-01 and RAT-02: fallen angel at agency level, split rating with Moody's Baa3 and Fitch BBB; decided without any model |
| Hydrofarm, 10-Q, August 2026 | `evt_cbac9027a8381e8ba80b` | P1 by ERN-01: a stated going concern doubt, flagged deterministically and confirmed by the validated model statements |
| OMV, Q4 2024 report, February 2025 | `evt_cac94ecbd819d0b2efc2` | No alert: "ability to continue as a going concern is not impacted" is read as negated by both passes; the first false P1 of the project, now the permanent control |
| Volkswagen, H1 2025 results, July 2025 (optional) | `evt_74a35936e1e4f7da4fd7` | P1 by ERN-02 on a real watchlist issuer: a guidance cut read by the model, validated against the text, decided by the rules |
| FTC Solar, 10-Q, August 2026 (reserve) | `evt_bc9f7d9a2c87443483b4` | Covenant breach waived plus a going concern doubt, two families on one filing |

## Timeline

Spoken lines are given in English; the French version follows each one for a French
recording. Speak over the clicks, do not wait for them.

**00:00 Dashboard.** Screen "Dashboard" in the sidebar.
Say: "Credit Event Radar monitors public information for credit-relevant events on a bond
issuer watchlist. It detects, prioritises with deterministic rules, explains, sources and
alerts. The analyst decides; the system never recommends."
FR : « Credit Event Radar surveille l'information publique à la recherche d'événements de
crédit sur une watchlist d'émetteurs obligataires. Il détecte, priorise avec des règles
déterministes, explique, source et alerte. L'analyste décide ; le système ne recommande
jamais. »
Point at the counters (P1, P2, P3), then at the two blocks: the live watchlist and the
historical stress cases, kept apart on every screen.

**00:15 Harley appears in P1.** In "Featured demo cases", the first row: P1, Harley-Davidson,
"S&P Global Ratings downgrade: BBB- → BB+, IG → HY".
Say: "On 8 July 2026 S&P lowered Harley-Davidson from BBB- to BB+: a fallen angel at agency
level, while Moody's and Fitch still rate the issuer investment grade."
FR : « Le 8 juillet 2026, S&P abaisse Harley-Davidson de BBB- à BB+ : un fallen angel au
niveau de l'agence, alors que Moody's et Fitch maintiennent l'émetteur en investment
grade. »

**00:30 Why P1?** Sidebar "Event detail", in the event selector type `BBB-` and press
Enter. The screen shows the red P1 badge, "Historical stress case", the title, then "Why
this priority?": RAT-01 and RAT-02 with their reasons, the ratings table (Fitch BBB IG,
Moody's Baa3 IG, S&P BB+ HY) and the provenance line.
Say: "Why P1? Two rules fired: RAT-01, an agency rating crosses from investment grade to
high yield; RAT-02, the split rating, Moody's and Fitch still IG. Priority decided by the
rules engine. Composite rating used: no. LLM used for the decision: no."
FR : « Pourquoi P1 ? Deux règles : RAT-01, une notation d'agence passe d'investment grade
à high yield ; RAT-02, le split rating, Moody's et Fitch restent IG. Priorité décidée par
le moteur de règles. Notation composite utilisée : non. LLM utilisé pour la décision :
non. »

**00:45 Evidence, the exact sentence of the 10-Q.** Scroll to "Evidence (verbatim
passages)", open the first expander (the sentence "S&P Global Ratings lowered the
Company's ... from BBB- to BB+"). The passage is highlighted inside its source context,
with offsets and the extractor version; below, "Sources": the SEC URL, the form, the
publication date, the retrieval timestamp and both SHA-256 hashes.
Say: "Every fact is the document's own sentence, located by character offsets in the
filing, with the hash of the bytes we retrieved. Nothing is paraphrased."
FR : « Chaque fait est la phrase même du document, localisée par ses offsets dans le
dépôt, avec le hash des octets récupérés. Rien n'est paraphrasé. »

**01:00 Hydrofarm, going concern.** In the event selector type `Hydrofarm`, Enter. P1,
"Quarterly report ... going concern doubt, liquidity concern", ERN-01. Scroll to
"Statements read by the model (validated)" and "Model provenance".
Say: "Hydrofarm's quarterly report states substantial doubt about its ability to continue
as a going concern. The deterministic extractor flags the sentence; the model's own
statements, validated word by word against the text, confirm it; rule ERN-01 decides the
P1. The provenance names the model, the prompt version and why that model was chosen."
FR : « Le rapport trimestriel d'Hydrofarm énonce un doute substantiel sur la continuité
d'exploitation. L'extracteur déterministe repère la phrase ; les énoncés du modèle,
validés mot à mot contre le texte, la confirment ; la règle ERN-01 décide le P1. La
provenance nomme le modèle, la version du prompt et la raison de ce choix. »

**01:15 OMV, no false P1.** In the event selector type `cac94` (the OMV Q4 2024 report),
Enter. Badge NONE, "No priority (NO_APPLICABLE_RULE)". Scroll to "Statements read by the
model": going_concern, negated, no flag: "From today's perspective, we assume that ... the
Company's ability to continue as a going concern is not impacted."
Say: "OMV writes that its ability to continue as a going concern is not impacted. An early
version raised a P1 on that sentence. Today both the deterministic pass and the validated
model statement read it as a denial: recorded, no flag, no alert. Knowing when not to
alert is half of the product."
FR : « OMV écrit que sa continuité d'exploitation n'est pas affectée. Une première version
levait un P1 sur cette phrase. Aujourd'hui, le passage déterministe et l'énoncé validé du
modèle la lisent comme une négation : enregistrée, sans flag, sans alerte. Savoir ne pas
alerter est la moitié du produit. »

**01:30 Teams card.** Back on the Harley event (type `BBB-`, Enter), scroll to the bottom:
"Teams card JSON" download, or show the rendered card from `outputs/alerts/<date>/
evt_9618eb5ad2d366339e69.card.json` in the Adaptive Cards designer, or the HTML alert
`outputs/alerts/<date>/evt_9618eb5ad2d366339e69.html` in a browser tab prepared before
recording.
Say: "The same object becomes a Teams card: badge, three sourced facts, the rules, links to
the sources and to the notes. Teams and e-mail are sent only when configured; the local
rendering is always there."
FR : « Le même objet devient une carte Teams : badge, trois faits sourcés, les règles, les
liens vers les sources et vers les notes. Teams et e-mail ne partent que s'ils sont
configurés ; le rendu local est toujours là. »

**01:40 Committee note, French.** Show `outputs/notes/<date>/evt_9618eb5ad2d366339e69.fr.md`
(or the HTML twin) prepared in a tab: header, key facts with [1], "Pourquoi c'est important",
"Points à vérifier", sources with SHA-256, "Unsupported claims: 0".
Say: "The committee note is complete without any model. When the model writes the two
prose sections, every sentence must cite the facts it rests on; a sentence that cannot be
tied to them is excluded and listed. Draft to be validated by the analyst, no
recommendation."
FR : « La note de comité est complète sans aucun modèle. Quand le modèle rédige les deux
sections de prose, chaque phrase doit citer les faits sur lesquels elle repose ; une
phrase qu'on ne peut pas rattacher est exclue et listée. Brouillon à valider par
l'analyste, aucune recommandation. »

**01:50 Benchmark and architecture.** Back to "Dashboard", scroll to "Model routing, one
benchmark per family" (guidance Terra, liquidity Terra, covenant Sol, going concern Terra,
each with its reason).
Say: "Each extraction family was benchmarked on a frozen gold set and blind holdouts; the
best model per task is routed, with the reason recorded in every alert. The models never
assign a priority: they extract candidate facts, deterministic validation and credit rules
make the decision."
FR : « Chaque famille d'extraction a été évaluée sur un jeu gold figé et des holdouts
aveugles ; le meilleur modèle par tâche est routé, la raison enregistrée dans chaque
alerte. Les modèles n'attribuent jamais de priorité : ils extraient des faits candidats, la
validation déterministe et les règles de crédit décident. »

**02:00 End.** Last frame on the dashboard. Say: "Credit Event Radar. Public data,
deterministic decisions, every claim sourced." FR : « Credit Event Radar. Données publiques,
décisions déterministes, chaque affirmation sourcée. »

## Guard rails while recording

- Never show `.env`, API keys, local absolute paths or the terminal that started the viewer.
- Do not open General Motors: a known false liquidity flag from a boilerplate sentence,
  documented in docs/BENCHMARK.md and kept as it is; it stays reachable, not featured.
- Do not read figures that are not on screen; the note and the alert carry the sources.
- If the viewer shows an error, the demo database was being rebuilt: wait and reload.

## Reserve material

- FTC Solar `evt_bc9f7d9a2c87443483b4`: covenant breach waived on 4 August 2026 and a going
  concern doubt on one filing; "Statements read by the model" shows the resolution.
- Volkswagen `evt_74a35936e1e4f7da4fd7`: a P1 from the live watchlist, guidance cut read by
  the model and validated against the release (ERN-02).
- The static export `outputs/site/index.html` is the fallback if Streamlit fails: the same
  sections, one page per decided event.
