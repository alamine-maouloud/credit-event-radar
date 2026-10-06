# Credit Event Radar

**Auditable AI-assisted credit monitoring.**
Detect, prioritise, explain, source, alert. The human analyst decides.

Credit Event Radar watches a demo watchlist of investment-grade and high-yield bond issuers
for credit events (rating actions, earnings releases, new issuance). It prioritises them with a
deterministic rules engine (P1/P2/P3), writes a sentence-by-sentence sourced summary in French
and English, and alerts the analyst (Teams, email or local rendering). It never produces an
investment recommendation.

## Status

Phase 1 (foundations) in progress. See `CLAUDE.md` for the phase checklist and `docs/SPEC.md`
for the full specification.

## Quick start

```bash
uv sync
cp .env.example .env
uv run radar version
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
