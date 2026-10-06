"""Typer command-line interface (see CLAUDE.md, section Commandes).

Phase 2 commands: init-db, seed, ingest, process, events, show-event. Later phases add
alert, note, eval and live.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from radar import __version__
from radar.config import (
    CONFIG_DIR,
    ROOT,
    SEEDS_DIR,
    load_dotenv,
    load_rating_scales,
    load_ratings_seed,
    load_rules,
    load_settings,
    load_universe,
)
from radar.db import Database

app = typer.Typer(
    name="radar",
    help="Credit Event Radar: auditable AI-assisted credit monitoring.",
    no_args_is_help=True,
)

DbOption = Annotated[
    Path | None, typer.Option("--db", help="SQLite path (default: settings.paths.db)")
]
RawOption = Annotated[
    Path | None,
    typer.Option("--raw-dir", help="Raw snapshots directory (default: settings.paths.raw)"),
]


def _settings():
    load_dotenv()
    return load_settings(CONFIG_DIR / "settings.yaml")


def _db(path: Path | None) -> Database:
    settings = _settings()
    return Database(path or ROOT / settings.paths.db)


def _raw_dir(path: Path | None) -> Path:
    return path or ROOT / _settings().paths.raw


@app.callback()
def main() -> None:
    """Credit Event Radar command-line interface."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"credit-event-radar {__version__}")


@app.command("init-db")
def init_db(db: DbOption = None) -> None:
    """Create the SQLite schema (idempotent)."""
    database = _db(db)
    database.init_schema()
    typer.echo(f"schema {database.schema_version()} ready at {database.path}")


@app.command()
def seed(db: DbOption = None) -> None:
    """Load universe.yaml and the hand-verified ratings seed into the database."""
    database = _db(db)
    universe = load_universe(CONFIG_DIR / "universe.yaml")
    scales = load_rating_scales(CONFIG_DIR / "rating_scales.yaml")
    ratings = load_ratings_seed(SEEDS_DIR / "ratings_seed.csv", scales, universe)
    n_issuers = database.replace_universe(universe)
    n_ratings = database.replace_ratings(ratings)
    typer.echo(f"{n_issuers} issuers, {n_ratings} seed ratings loaded")


@app.command()
def ingest(
    since: Annotated[str, typer.Option(help="Earliest filing date, YYYY-MM-DD")],
    source: Annotated[str, typer.Option(help="edgar | fixtures")] = "edgar",
    fixtures_dir: Annotated[
        Path | None, typer.Option(help="Fixture root for --source fixtures")
    ] = None,
    issuer: Annotated[list[str] | None, typer.Option(help="Restrict to issuer ids")] = None,
    url: Annotated[
        str | None, typer.Option(help="Ingest one document by URL instead of a date range")
    ] = None,
    db: DbOption = None,
    raw_dir: RawOption = None,
) -> None:
    """Fetch documents, snapshot them with their hashes and store them."""
    from radar.normalize import SecHtmlNormalizer
    from radar.pipeline import ingest as run_ingest
    from radar.pipeline import ingest_url

    database = _db(db)
    universe = load_universe(CONFIG_DIR / "universe.yaml")
    issuers = [i for i in universe.issuers if not issuer or i.id in set(issuer)]
    raw = _raw_dir(raw_dir)
    skipped_sources: list = []
    if source == "fixtures":
        from radar.connectors.fixture import FixtureAdapter

        adapter = FixtureAdapter(
            fixtures_dir or ROOT / "tests" / "fixtures" / "edgar", raw, SecHtmlNormalizer()
        )
    elif source == "ir":
        from radar.connectors.ir import IRSourceAdapter

        settings = _settings()
        adapter = IRSourceAdapter(
            raw,
            min_interval_seconds=settings.ingestion.ir.min_interval_seconds,
            respect_robots=settings.ingestion.ir.respect_robots,
        )
        skipped_sources = adapter.skipped
    elif source == "edgar":
        from radar.connectors.edgar import EdgarAdapter

        settings = _settings()
        adapter = EdgarAdapter(
            raw, max_requests_per_second=settings.ingestion.sec.max_requests_per_second
        )
    else:
        raise typer.BadParameter("source must be edgar, ir or fixtures")
    if url:
        summary = ingest_url(database, adapter, url)
    else:
        summary = run_ingest(database, adapter, since=date.fromisoformat(since), issuers=issuers)
    typer.echo(
        f"fetched {summary.fetched}, stored {summary.stored}, duplicates {summary.duplicates}"
    )
    for doc_id in summary.doc_ids:
        typer.echo(f"  {doc_id}")
    from radar.audit import AuditEntry

    for item in skipped_sources:
        database.audit(
            AuditEntry(step="discover", status="skipped", message=f"{item.url}: {item.reason}")
        )
        typer.echo(f"  skipped {item.url}: {item.reason}")


@app.command()
def process(db: DbOption = None) -> None:
    """Resolve issuers, run deterministic extraction and deduplication on new documents."""
    from radar.pipeline import process as run_process

    database = _db(db)
    universe = load_universe(CONFIG_DIR / "universe.yaml")
    scales = load_rating_scales(CONFIG_DIR / "rating_scales.yaml")
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    s = run_process(database, universe, scales, rules)
    typer.echo(
        f"documents {s.documents}, resolved {s.resolved}, unresolved {s.unresolved}, "
        f"events new {s.events_new}, merged {s.events_merged}, "
        f"candidates rejected {s.candidates_skipped}, observations {s.observations_new}, "
        f"no_event {s.no_event}"
    )
    decided = ", ".join(f"{k} {v}" for k, v in sorted(s.decisions.items())) or "none"
    typer.echo(f"decisions: {decided}")
    for event_id in s.event_ids:
        typer.echo(f"  {event_id}")


@app.command()
def documents(
    issuer: Annotated[str | None, typer.Option(help="Issuer id")] = None, db: DbOption = None
) -> None:
    """List stored documents with resolution and outcome.

    Outcomes: EVENTS, OBSERVATIONS_ONLY, NO_EVENT, UNRESOLVED.
    """
    database = _db(db)
    for d in database.list_document_status(issuer):
        kind = d["extra"].get("document_type") or d["source_type"]
        outcome = d["outcome"] or "pending"
        issuer_id = d["issuer_id"] or "-"
        published = (d["published_at"] or "")[:10]
        typer.echo(
            f"{d['doc_id'][:16]}  {outcome:<18}  {issuer_id:<20}  {kind:<14}  {published:<10}  "
            f"{d['title'] or d['url']}"
        )


@app.command()
def decide(
    issuer: Annotated[str | None, typer.Option(help="Issuer id")] = None, db: DbOption = None
) -> None:
    """Recompute materiality decisions for stored events (after a rules.yaml change)."""
    from radar.pipeline import decide_all

    database = _db(db)
    scales = load_rating_scales(CONFIG_DIR / "rating_scales.yaml")
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    counts = decide_all(database, rules, scales, issuer)
    typer.echo("decisions: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"))


@app.command()
def events(
    issuer: Annotated[str | None, typer.Option(help="Issuer id")] = None, db: DbOption = None
) -> None:
    """List stored events."""
    database = _db(db)
    for ev in database.list_events(issuer):
        f = ev.fields
        decided = database.priority_of(ev.event_id)
        priority = (decided[0] or "NONE") if decided else "undecided"
        if ev.family == "rating" and not f.get("old_rating"):
            parts = [
                f.get("agency"),
                f.get("rating"),
                f.get("new_outlook") and f"outlook {f['new_outlook']}",
                f.get("watch") and f"watch {f['watch']}",
            ]
            detail = " ".join(str(x) for x in parts if x)
            kind = f"{ev.family}/{ev.event_type}"
            head = f"{ev.event_id}  {priority:<9}  {ev.issuer_id}  {kind}"
            typer.echo(f"{head}  {ev.effective_date}  {detail}")
            continue
        detail = (
            f"{f.get('agency')} {f.get('old_rating')} to {f.get('new_rating')}"
            if ev.family == "rating"
            else f.get("form", "")
        )
        kind = f"{ev.family}/{ev.event_type}"
        typer.echo(
            f"{ev.event_id}  {priority:<9}  {ev.issuer_id}  {kind}  {ev.effective_date}  {detail}"
        )


@app.command("show-event")
def show_event(
    event_id: str,
    explain: Annotated[
        bool, typer.Option("--explain", help="Why this priority? Full rule-by-rule explanation")
    ] = False,
    db: DbOption = None,
) -> None:
    """Trace one event back to its fields, source passages, documents and audit trail."""
    database = _db(db)
    ev = database.get_event(event_id)
    if ev is None:
        raise typer.BadParameter(f"unknown event {event_id}")
    if explain:
        from radar.materiality.explain import render_explanation

        decision = database.get_decision(event_id)
        if decision is None:
            raise typer.BadParameter(f"no decision stored for {event_id}; run `radar decide`")
        documents = {d: doc for d in ev.source_doc_ids if (doc := database.get_document(d))}
        typer.echo(render_explanation(decision, ev, documents), nl=False)
        return
    typer.echo(f"Event {ev.event_id}")
    typer.echo(f"Issuer: {ev.issuer_id}")
    typer.echo(f"Type: {ev.family}/{ev.event_type}  Effective: {ev.effective_date}")
    typer.echo(f"Method: {ev.extraction_method}")
    typer.echo("Fields:")
    for key, value in ev.fields.items():
        typer.echo(f"  {key}: {value}")
    typer.echo("Evidence:")
    for span in ev.evidence:
        where = f"{span.evidence_type} {span.char_start}-{span.char_end}"
        typer.echo(
            f"  [{span.field or 'sentence'}] {where} ({span.extractor_version}): {span.quote}"
        )
    typer.echo("Sources:")
    for doc_id in ev.source_doc_ids:
        doc = database.get_document(doc_id)
        if doc:
            typer.echo(f"  {doc.url}")
            typer.echo(f"    raw sha256 {doc.content_hash} ({doc.raw_size_bytes} bytes)")
            typer.echo(f"    normalised sha256 {doc.doc_id} ({doc.normalizer_version})")
            typer.echo(f"    published {doc.published_at}, retrieved {doc.retrieved_at}")
            typer.echo(f"    snapshot {doc.raw_path}")
            typer.echo(f"    connector metadata {json.dumps(doc.extra, sort_keys=True)}")
    typer.echo("Audit trail:")
    for entry in database.audit_entries(event_id=event_id):
        typer.echo(f"  {entry['timestamp']} {entry['step']} {entry['status']}: {entry['message']}")
    decided = database.priority_of(event_id)
    if decided:
        priority, status = decided
        typer.echo(f"Priority: {priority or 'NONE'} ({status}), deterministic rules engine.")
        typer.echo("Use --explain for the rule-by-rule view.")
    else:
        typer.echo("Priority: not decided yet (run `radar decide`).")


# ---------------------------------------------------------------- llm --- #


def _llm_role(alternative: str | None):
    settings = _settings()
    if alternative is None:
        return settings, settings.llm.roles["extraction"]
    try:
        return settings, settings.llm.benchmark_alternatives[alternative]["extraction"]
    except KeyError as exc:
        raise typer.BadParameter(
            f"unknown alternative {alternative!r}, see settings.llm.benchmark_alternatives"
        ) from exc


def _provider_for(name: str):
    from radar.llm.provider import refuse_placeholder_model

    if name == "openai":
        from radar.llm.openai_client import OpenAIProvider

        return OpenAIProvider()
    if name == "anthropic":
        from radar.llm.anthropic_client import AnthropicProvider

        return AnthropicProvider()
    refuse_placeholder_model("TO_CONFIRM")
    raise typer.BadParameter(f"no provider implemented for {name!r}")


GOLD_V1 = ROOT / "eval" / "gold" / "guidance_v1.jsonl"
OUT_OPTION = typer.Option(..., "--out", help="Run directory, e.g. eval/runs/terra-2026-10-06")
GOLD_OPTION = typer.Option(GOLD_V1, "--gold", help="Frozen gold JSONL")
RUN_OPTION = typer.Option(..., "--run", help="Run directory written by llm-extract")


@app.command("llm-extract")
def llm_extract(
    out: Path = OUT_OPTION,
    gold: Path = GOLD_OPTION,
    alternative: str | None = typer.Option(
        None, "--alternative", help="Benchmark alternative from settings (openai_sol, anthropic)"
    ),
    limit: int | None = typer.Option(None, "--limit", help="Only the first N gold documents"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Render the prompts and estimate the cost, call nothing"
    ),
    db: DbOption = None,
) -> None:
    """Run the guidance extraction on the frozen gold set: cache, budget hard stop, two level
    validation, derived figures and metrics. Separate from `process`, never touches events."""
    from radar.eval.benchmark import BenchmarkConfig, run_benchmark
    from radar.eval.gold import load_gold
    from radar.llm.budget import BudgetExceeded, RunBudget
    from radar.llm.cache import LLMCache
    from radar.llm.pricing import load_pricing
    from radar.llm.prompts import load_prompt
    from radar.llm.provider import FakeProvider

    load_dotenv()
    settings, role = _llm_role(alternative)
    pricing = load_pricing(ROOT / settings.llm.pricing_file)
    if dry_run:
        budget = RunBudget(limit_usd=1.0, pricing=pricing)
        provider = FakeProvider(name=role.provider, raw_json="{}")
    else:
        try:
            budget = RunBudget.from_env(pricing)
        except BudgetExceeded as exc:
            typer.echo(f"refused: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        provider = _provider_for(role.provider)
    database = _db(db)
    database.init_schema()
    config = BenchmarkConfig(
        model_id=role.model, reasoning_effort=role.reasoning_effort, temperature=role.temperature
    )
    summary = run_benchmark(
        load_gold(gold),
        db=database,
        universe=load_universe(),
        provider=provider,
        cache=LLMCache(database),
        budget=budget,
        prompt=load_prompt(ROOT / "prompts" / "extraction" / "guidance.v1.yaml"),
        config=config,
        out_dir=out,
        limit=limit,
        dry_run=dry_run,
        gold_path=gold,
    )
    typer.echo(
        f"run {summary.run_id}: {summary.n_documents} documents, statuses {summary.statuses}, "
        f"model {config.model_id} (effort {config.reasoning_effort}), provider {provider.name}"
    )
    typer.echo(
        f"cost {summary.total_cost_usd:.4f} USD (estimated before the calls "
        f"{summary.estimated_cost_usd:.4f}), budget {summary.budget_limit_usd:.2f}, "
        f"remaining {summary.budget_remaining_usd:.4f}"
    )
    _echo_metrics(summary.metrics)
    typer.echo(f"written: {out / 'outputs.jsonl'}, {out / 'run.json'}, {out / 'metrics.json'}")


@app.command("llm-eval")
def llm_eval(run: Path = RUN_OPTION, gold: Path = GOLD_OPTION) -> None:
    """Re-score a run directory against the gold set (metrics.json is rewritten)."""
    from radar.eval.benchmark import write_results
    from radar.eval.gold import load_gold
    from radar.eval.metrics import score

    rows = [
        json.loads(line)
        for line in (run / "outputs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    metrics = score(load_gold(gold), rows)
    (run / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    write_results(run, rows)
    _echo_metrics(metrics)


RUNS_OPTION = typer.Option(..., "--run", help="Run directory, repeat for each run to compare")
COMPARE_OUT_OPTION = typer.Option(None, "--out", help="Write the markdown table to this file")


@app.command("llm-compare")
def llm_compare(
    runs: list[Path] = RUNS_OPTION,
    gold: Path = GOLD_OPTION,
    out: Path | None = COMPARE_OUT_OPTION,
) -> None:
    """Side by side table of benchmark runs with every statement in one category
    (supported, scope violation, field inconsistency, span failure, ungrounded)."""
    from radar.eval.compare import compare_runs, render_markdown
    from radar.eval.gold import load_gold

    table = compare_runs(load_gold(gold), runs)
    markdown = render_markdown(table)
    if out is not None:
        out.write_text(markdown, encoding="utf-8")
    typer.echo(markdown)
    for run in table["runs"]:
        if run["scope_violation_labels"]:
            typer.echo(
                f"{run['run_id']} scope violations: {sorted(set(run['scope_violation_labels']))}"
            )
        if run["missed"]:
            typer.echo(f"{run['run_id']} missed: {run['missed']}")


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _echo_metrics(m: dict) -> None:
    d, b, o, c = m["documents"], m["behaviour"], m["occurrences"], m["claims"]
    typer.echo(
        f"documents: {d['n']} (ok {d['ok']}, cached {d['cached']}, json failures "
        f"{d['json_failures']}, truncated {d.get('truncated', 0)}, budget refused "
        f"{d['budget_refused']}, provider errors {d['provider_errors']}, dry run {d['dry_run']})"
    )
    typer.echo(f"behaviour accuracy: {_fmt(b['accuracy'])} on {b['n_scored']} documents")
    typer.echo(
        f"occurrences: gold {o['gold']}, predicted {o['predicted']} "
        f"(valid {o['predicted_valid']}), matched {o['matched']}; "
        f"precision {_fmt(o['precision_valid'])} (all statements "
        f"{_fmt(o['precision_all'])}), recall {_fmt(o['recall'])}, F1 {_fmt(o['f1'])}"
    )
    typer.echo(
        "field agreement: " + ", ".join(f"{k} {_fmt(v)}" for k, v in o["field_agreement"].items())
    )
    typer.echo(
        f"spans: exact {_fmt(o['span_exact_rate'])}, overlap {_fmt(o['span_overlap_rate'])}; "
        f"invalid span rate {_fmt(c['invalid_span_rate'])}, unsupported claim rate "
        f"{_fmt(c['unsupported_claim_rate'])}, statements on no_guidance documents "
        f"{c['on_no_guidance_documents']}"
    )
    typer.echo(
        f"cost {m['cost']['total_usd']:.4f} USD, "
        f"per document {_fmt(m['cost']['per_document_usd'])}, "
        f"latency ms mean {_fmt(m['latency_ms']['mean'])} median {_fmt(m['latency_ms']['median'])}"
    )


if __name__ == "__main__":
    app()
