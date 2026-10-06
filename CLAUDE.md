# CLAUDE.md · Credit Event Radar

> Auditable AI-assisted credit monitoring.
> Detect → Prioritise → Explain → Source → Alert → **the human analyst decides.**

## Le projet en une phrase

Credit Event Radar détecte les événements crédit (notations, résultats, nouvelles émissions) sur une watchlist d'émetteurs obligataires IG/HY. Il les priorise avec un **moteur de règles déterministe** (P1/P2/P3), produit une synthèse **sourcée phrase par phrase** en FR et en EN, puis envoie une alerte (Teams, email ou rendu local). Il n'émet aucune recommandation d'investissement.

## Contexte

Projet personnel d'Alamine (M2 Data & AI, ECE Paris) pour candidater au stage « Gestion de projet / Intelligence Artificielle » (janvier 2027) du pôle Fixed Income de Rothschild & Co Asset Management. Le prototype reflète les missions publiées de ce stage : identifier des cas d'usage IA dans le workflow des analystes crédit et des gérants, construire une bibliothèque de prompts FR/EN normalisés, mettre en place des alertes intelligentes Teams/Email sur les événements de crédit, évaluer des solutions et documenter.

**La spécification complète est dans `docs/SPEC.md`. Lis-la avant toute tâche structurante.**

## Règles non négociables

1. **Le LLM ne décide jamais de la priorité.** La priorité vient uniquement de `config/rules.yaml`, appliqué par `src/radar/materiality/`. Le LLM peut extraire des champs, toujours validés de façon déterministe, et rédiger des synthèses.
2. **Chaque affirmation générée est reliée à un passage source** : id du document, URL, offsets, timestamp. Une affirmation non vérifiée est exclue de la note et journalisée.
3. **Aucun chiffre inventé.** Les résultats affichés dans le README et la case study sont produits uniquement par `eval/`, à partir d'exécutions réelles. Aucune valeur de remplissage ne doit être présentée comme un résultat.
4. **Aucun score de « confidence » auto-déclaré par un LLM.** Les seuls statuts de vérification sont `VERIFIED`, `PARTIAL` et `UNSUPPORTED`.
5. **Le panneau « Why this priority? » décrit honnêtement le rôle du LLM**, par exemple : `LLM used for: field extraction (validated against source text)`. N'écris jamais `None` si un LLM a extrait un champ.
6. **Traçabilité complète.** Chaque document brut est stocké avec son hash SHA-256, son URL, `published_at` et `retrieved_at`. Chaque appel LLM est journalisé : modèle, version de prompt, hash d'entrée et de sortie, coût, latence.
7. **Aucune affiliation suggérée avec Rothschild & Co.** Pas de logo ni de charte graphique, et le disclaimer est obligatoire. L'univers de démo s'appelle « Demo watchlist ». Il est construit à partir d'informations publiques datées et n'est jamais présenté comme leur portefeuille.
8. **Respect des sources.** Conditions d'utilisation, robots.txt et limites de débit sont respectés. Pour la SEC, envoie un User-Agent déclaré avec contact et reste sous 10 requêtes par seconde. Aucun contournement de login ou de paywall.
9. **Secrets dans `.env`**, jamais commités. `.env.example` reste à jour.
10. **Périmètre MVP strict** (voir `docs/SPEC.md` §4). Toute idée hors périmètre va dans `docs/roadmap_180d.md`, pas dans le code.
11. **Les notations par agence font foi.** La notation composite est une métadonnée analytique : elle peut être affichée, jamais utilisée pour déclencher ou modifier une règle P1/P2/P3 (ADR-001 dans `docs/ARCHITECTURE.md`). Un P1 « fallen angel » vient d'une agence qui passe de BBB-/Baa3 à HY, pas d'un composite maison.

## Stack

- Python 3.11+, environnement géré avec `uv`
- pydantic v2 (schémas), pandas, SQLite (sqlite3 ou SQLAlchemy Core), httpx, feedparser, trafilatura ou BeautifulSoup, PyMuPDF, rapidfuzz, Jinja2
- Typer pour la CLI, Streamlit pour un viewer minimal, pytest et ruff
- LLM via la couche `src/radar/llm/provider.py`, avec deux backends : Anthropic et OpenAI/Azure OpenAI. Les modèles sont définis dans `config/settings.yaml`, jamais en dur. Température 0 pour l'extraction et l'évaluation. Les réponses sont mises en cache par (modèle, version de prompt, hash d'entrée), ce qui garantit la reproductibilité et limite les coûts.

## Arborescence cible

```
credit-event-radar/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── settings.yaml          # modèles, seuils, intervalles, chemins
│   ├── universe.yaml          # demo watchlist + alias + secteurs + identifiants
│   ├── rating_scales.yaml     # échelles S&P / Moody's / Fitch → notches
│   └── rules.yaml             # matrice de matérialité (IDs de règles)
├── data/
│   ├── seeds/ratings_seed.csv # notations initiales + URL source + date
│   ├── raw/                   # snapshots bruts (gitignored)
│   └── radar.db               # SQLite (gitignored)
├── prompts/
│   ├── STYLE_GUIDE.md
│   ├── extraction/            # rating_action, issuance, earnings_signals
│   ├── notes/                 # event_summary.{fr,en}, committee_note.{fr,en}
│   └── verification/          # claim_support
├── src/radar/
│   ├── models.py              # schémas pydantic
│   ├── config.py              # chargement validé des YAML et du seed (seul I/O de la Phase 1)
│   ├── normalize.py           # HTML SEC → texte, normaliseur versionné (offsets des spans)
│   ├── snapshot.py            # brut sur disque, hash des octets et du texte, RawDocument
│   ├── pipeline.py            # ingest / process, journal d'audit à chaque étape
│   ├── dedup.py               # clé métier + fenêtre de dates
│   ├── db.py                  # schéma SQLite + accès
│   ├── ratings.py             # échelles, notches, notation composite (pur)
│   ├── connectors/            # base.py, edgar.py, ir.py (rss, sitemap, liens, page), robots.py, fixture.py
│   ├── resolve.py             # rattachement document → émetteur
│   ├── extract/               # structured.py (phrases), ratings_table.py, issuance.py, earnings.py, dates.py, spans.py ; llm_extract.py en Phase 3
│   ├── materiality/           # engine.py, state.py, explain.py (pur, sans I/O ni LLM)
│   ├── llm/                   # provider.py, openai_client.py, anthropic_client.py, schemas.py, validate.py, compute.py, budget.py, pricing.py, cache.py, prompts.py, runner.py
│   ├── context/               # fundamentals.py, summarize.py
│   ├── verify/                # claims.py
│   ├── notes/                 # committee.py + templates Jinja2
│   ├── alerts/                # teams.py, email.py, local.py
│   ├── audit.py
│   └── cli.py
├── app/viewer.py              # Streamlit : alertes, « Why? », citations cliquables
├── eval/
│   ├── LABELING_GUIDE.md
│   ├── gold/events.jsonl
│   ├── gold/noise.jsonl
│   ├── run_eval.py
│   └── reports/
├── docs/
│   ├── SPEC.md
│   ├── ARCHITECTURE.md
│   ├── MATERIALITY_MATRIX.md
│   ├── roadmap_180d.md
│   └── case_study/
└── tests/
```

## Commandes

```bash
uv sync
cp .env.example .env
uv run radar init-db
uv run radar seed                          # universe.yaml + ratings_seed.csv
uv run radar ingest --since 2026-09-01     # EDGAR, User-Agent déclaré obligatoire dans .env
uv run radar ingest --since 2026-01-01 --source fixtures   # rejoue les fixtures golden hors ligne
uv run radar ingest --since 2026-09-01 --source ir         # sources IR de universe.yaml (robots.txt, cadence, User-Agent)
uv run radar documents                     # documents stockés avec leur issue : EVENTS, OBSERVATIONS_ONLY, NO_EVENT, UNREADABLE_TEXT, UNRESOLVED
uv run radar process                       # rattachement → extraction → dédup → audit (matérialité en Phase 4)
uv run radar events                        # liste des événements stockés
uv run radar show-event <id>               # champs, passages sources, hashes, trace d'audit
uv run radar show-event <id> --explain     # panneau « Why this priority? » : règles évaluées, état des notations, provenance
uv run radar decide                        # recalcule les décisions après un changement de rules.yaml
uv run radar alert --dry-run               # rendu local HTML/JSON, aucun envoi
uv run radar note --event-id <id> --lang fr
uv run radar eval --models extract_a,extract_b
uv run radar live --interval 15m
uv run streamlit run app/viewer.py
uv run pytest -q && uv run ruff check .
```

## Conventions

- Code, identifiants, commentaires, docstrings et README en anglais. Prompts et notes de comité en FR et en EN.
- Type hints partout. `ratings.py` et `materiality/` sont des fonctions pures, sans I/O ni appel LLM.
- Tests obligatoires pour : les échelles de notation, la notation composite, chaque règle de `rules.yaml`, chaque modificateur, la validation des spans, la vérification numérique des claims et la déduplication.
- Les prompts vivent dans `prompts/` en YAML versionné. Toute modification incrémente la version et complète le changelog du fichier.
- Petits commits, un par étape, au format `feat(scope): ...`, `fix(scope): ...`, `test(scope): ...`.

## Façon de travailler

- Commence chaque phase en mode plan : propose le plan, attends la validation, puis code.
- Écris les tests du moteur de matérialité **avant** son implémentation.
- En cas de doute sur une source (accès, conditions d'utilisation, format), arrête-toi et demande plutôt que contourner.
- N'invente jamais de notation, de date ou de montant pour remplir un seed ou un test réaliste. Utilise des fixtures explicitement fictives (`ISSUER_TEST_A`) ou demande la donnée.
- Mets à jour la section « Statut » à la fin de chaque phase.
- Ordre d'exécution : Phase 2 puis Phase 4 avant la Phase 3. Un P1 déterministe de bout en bout (source, événement, règle, priorité, audit) doit exister avant le premier appel LLM (ADR-004).
- Le seed de notations suit l'échelle de validation de `docs/SEED_VALIDATION.md`. Seules les lignes GOLDEN entrent dans un composite, et la promotion en GOLDEN est une décision humaine.

## Statut

- [x] Phase 1 · Fondations : repo, schémas, échelles de notation, notation composite, config, seeds (2026-10-06)
- [x] Phase 2 · Ingestion : EDGAR, snapshots, hash, déduplication, extraction structurée déterministe, audit, P1 Harley de bout en bout sans LLM (2026-10-06). Flux IR et news RSS reportés en Phase 2b, après le moteur de matérialité (ADR-004)
- [x] Phase 4 · Moteur de matérialité : rules.yaml v1.2, 19 règles et 3 modificateurs testés avant implémentation, état des notations à D anti look-ahead, observations de tableaux, `show-event --explain`, Harley P1 par RAT-01 et RAT-02 (2026-10-06)
- [x] Phase 2b · Sources IR réelles (Volkswagen, TRATON, OMV) : adaptateur générique configuré par émetteur (rss, sitemap, liens, page), robots.txt et cadence, PDF, profils de tableaux, extracteurs émissions et résultats, NO_EVENT et UNREADABLE_TEXT, fixtures privées à manifeste public (2026-10-06) ; 2b.1 : garde d'entité et références historiques (ADR-013), phase gelée
- [~] Phase 3 · Extraction LLM : 3.1 socle fait (provider à deux backends, schéma strict guidance, validateur à deux niveaux, budget à arrêt dur, cache complet, bibliothèque de prompts, ADR-014, 2026-10-06, aucun appel réel) ; 3.2 guidance sur jeu gold figé, 3.3 benchmark, 3.4 autres champs ; 3.2 : 30 documents gold (10 VW, 8 TRATON, 12 OMV) et 57 occurrences annotés dans eval/gold/guidance_v1.yaml, guide eval/LABELING_GUIDE.md, gold V1 gelé le 2026-10-06 (lock ee2670bd), `radar llm-extract` / `radar llm-eval` câblés et testés hors ligne, prompt 1.1.0, dry-run 30 documents estimé 2.19 USD ; premier run Terra réel fait le 2026-10-06 (1,19 USD, rappel 0,965, précision 0,833, comportement 29/30, docs/BENCHMARK.md), 3.3 : Sol fait le 2026-10-06 (1,96 USD, rappel 0,982, précision 1,000 sur validés, 0 figure segmentaire contre 11 pour Terra) ; garde déterministe de périmètre segment fait (ADR-016, OUT_OF_SCOPE_SEGMENT), revalidation depuis le cache : Terra et Sol à précision 1,000 sur énoncés validés, rappel 0,965 / 0,982, docs/BENCHMARK.md ; 3.3b fait : `radar llm-extract --events` + `radar llm-apply` (ADR-017, rules 1.5, schéma 7, 14 tests de bout en bout) ; 3.4a liquidity : schéma liquidity-1.0, prompt dédié, validateur de polarité, flag posé par le code seul (ADR-018, 21 tests), gold liquidity V1 gelé (lock 051341a0, 20 docs dont 5 holdout, 3 stress cases GoPro/Microvast/Chicago Rivet), runs Terra 0,96 USD et Sol 1,82 USD plus revalidations depuis le cache (flag document 3/3 pour les deux, 0 faux flag Terra, 1 faux flag Sol sur le holdout), docs/BENCHMARK.md ; puis 3.4b covenants, 3.4c going concern
- [ ] Phase 5 · Contexte, vérification des claims, notes de comité FR/EN
- [ ] Phase 6 · Alertes (Teams, email, local) et viewer Streamlit
- [ ] Phase 7 · Évaluation : jeu gold, métriques, comparaison de LLM, rapport auto-généré
- [ ] Phase 8 · Run live (5 à 7 jours), README, vidéo de démo

## Disclaimer (à reprendre dans le README, le viewer et chaque note)

> Independent student project. Not affiliated with, endorsed by, or using any proprietary data from Rothschild & Co. The demo watchlist is built exclusively from publicly available information retrieved as of [DATE]. Inspired by publicly available Rothschild & Co Asset Management Fixed Income research and the January 2027 AI Project Management internship description. AI-generated drafts must be validated by an analyst. Not investment advice.
