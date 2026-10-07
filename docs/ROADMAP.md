# From prototype to production: an illustrative 180-day roadmap

Illustrative production roadmap for an institutional fixed-income environment, based
solely on public information. Priorities would be defined with the team that uses the
tool; nothing here presumes what any firm should deploy.

## 0 to 30 days: integration and feedback

- Run the prototype live on the team's own watchlist, local rendering only, and measure:
  publication-to-alert delay, alerts per issuer per day, confirmed against contested.
- Sit with two or three analysts: which alerts helped, which were noise, what a committee
  note must contain in the house format.
- Source adapters behind the same `SourceAdapter` interface: a market-data terminal feed
  for rating actions and issuances, internal document stores, with the same snapshots and
  hashes.
- Sign off the ratings seed (the GOLDEN level of the verification ladder) so the composite
  becomes analytical metadata the team trusts; it stays outside the rules.

## 30 to 90 days: universe, workflow, governance

- Widen the universe to the full coverage list; measure the rules per sector and adjust
  thresholds in `rules.yaml` with tests, never in code.
- Alert workflow: acknowledge, assign, close, with the reason; a daily digest for P2 and
  P3; Teams through the Workflows webhook, e-mail where Teams is not available.
- Governance: who may change a rule, a prompt or a model; every change versioned,
  re-benchmarked on the frozen gold sets, recorded in the audit log with the run id.
- Monitoring: cost per day under a hard budget, cache hit rate, validator rejection rates
  by reason (a rising HYPOTHETICAL_RISK or OUT_OF_SCOPE_SEGMENT rate is a signal).

## 90 to 180 days: hardening and scale

- Production hardening: permissions on documents and notes, retention, encrypted
  secrets, deployment with health checks, a replay command for any past alert.
- Human feedback loop: analysts' confirmations and contestations become a second gold
  set, used to re-benchmark models and to propose rule changes, never to retrain silently.
- Provider evaluation: the same benchmark protocol on a second provider, so that the
  routing per family stays a measured choice with an exit path.
- Model monitoring: drift of statement precision on a monthly sample, prompt and schema
  version pinned per call, alerts when a provider changes a model behind an alias.
- Committee note integration: the house template, the signature workflow, the archive
  with the sources and hashes of every claim.

What does not change at any step: the models read and quote, deterministic validation
checks every statement against the text, and the credit rules make the decision.
