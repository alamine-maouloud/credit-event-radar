# Credit Event Radar

**Auditable AI-assisted credit monitoring.**
Detect, prioritise, explain, source, alert. The human analyst decides.

Credit Event Radar watches a demo watchlist of investment-grade and high-yield bond issuers
for credit events (rating actions, earnings releases, new issuance). It prioritises them with a
deterministic rules engine (P1/P2/P3), writes a sentence-by-sentence sourced summary in French
and English, and alerts the analyst (Teams, email or local rendering). It never produces an
investment recommendation.

## Status

Phases 1, 2, 4 and 2b done: schemas, rating scales, hand-verified seed, EDGAR and issuer
investor-relations ingestion (RSS, sitemap, page links, pages; robots.txt honoured) with raw
snapshots and hashes, deterministic extraction of rating actions, ratings tables, issuance and
results statements, a pure materiality engine on agency ratings with a rule-by-rule
explanation, and an audit trail. Documents that match nothing end as NO_EVENT, never as a
guessed event.
The historical control case (a real 10-Q where S&P moved an issuer from BBB- to BB+ while
Moody's and Fitch stayed investment grade) is scored P1 by RAT-01 and RAT-02 end to end,
offline, without any LLM. See `CLAUDE.md` for the phase checklist, `docs/SPEC.md` for the
specification and `docs/ARCHITECTURE.md` for the decisions.

## Quick start

```bash
uv sync
cp .env.example .env
uv run radar init-db
uv run radar seed
uv run radar ingest --since 2026-01-01 --source fixtures
uv run radar process
uv run radar events
uv run radar show-event <event_id> --explain
uv run pytest -q && uv run ruff check .
```

## Résumé (FR)

Prototype de surveillance crédit pour une équipe de gestion obligataire : détection des
événements crédit sur une watchlist de démonstration, priorisation par un moteur de règles
déterministe, synthèse FR/EN sourcée phrase par phrase, alerte à l'analyste. L'analyste décide.
Aucune recommandation d'investissement.

## Disclaimer

> Independent student project. Not affiliated with, endorsed by, or using any proprietary data
> from Rothschild & Co. The demo watchlist is built exclusively from publicly available
> information retrieved as of 2026-10-06. Inspired by publicly available Rothschild & Co Asset
> Management Fixed Income research and the January 2027 AI Project Management internship
> description. AI-generated drafts must be validated by an analyst. Not investment advice.

## LLM benchmark (Phase 3.2)

```bash
radar llm-extract --dry-run --out eval/runs/dry-terra      # prompts and cost estimate, no call
radar llm-extract --out eval/runs/terra-2026-10-06         # real run, needs OPENAI_API_KEY and LLM_RUN_BUDGET_USD in .env
radar llm-extract --alternative openai_sol --out eval/runs/sol-2026-10-06
radar llm-eval --run eval/runs/terra-2026-10-06            # re-score against the frozen gold set
```

The gold set is frozen (eval/gold/guidance_v1.lock.json); see docs/ARCHITECTURE.md ADR-015.
