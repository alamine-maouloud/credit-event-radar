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


def _select_model(kind: str, alternative: str | None, challenger: bool):
    """The routed model for the kind (ADR-020): default, challenger or an explicit
    alternative; refused before any call when the family has no benchmark yet."""
    from radar.llm.routing import RoutingNotBenchmarked, select_model

    settings = _settings()
    try:
        selection = select_model(settings.llm, kind, alternative=alternative, challenger=challenger)
    except KeyError as exc:
        raise typer.BadParameter(
            f"unknown alternative {alternative!r}, see settings.llm.benchmark_alternatives"
        ) from exc
    except RoutingNotBenchmarked as exc:
        raise typer.BadParameter(str(exc)) from exc
    return settings, selection


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
OUT_OPTION = typer.Option(
    None, "--out", help="Benchmark run directory, e.g. eval/runs/terra-2026-10-06"
)
EVENTS_OPTION = typer.Option(
    False, "--events", help="Extract on the documents behind stored earnings_release events"
)
ISSUER_OPTION = typer.Option(None, "--issuer", help="Restrict to one issuer id")
DOC_OPTION = typer.Option(None, "--doc", help="Restrict to one document id")
EVENT_OPTION = typer.Option(None, "--event", help="Restrict to one event id")
KIND_OPTION = typer.Option(
    "guidance", "--kind", help="Extraction kind: guidance | liquidity | covenant | going_concern"
)
GOLD_OPTION = typer.Option(GOLD_V1, "--gold", help="Frozen gold JSONL")
RUN_OPTION = typer.Option(..., "--run", help="Run directory written by llm-extract")


@app.command("alert")
def alert(
    event_id: Annotated[str | None, typer.Argument(help="Event id (omit with --all)")] = None,
    all_events: Annotated[
        bool, typer.Option("--all", help="Every decided event that carries a priority")
    ] = False,
    out: Annotated[Path, typer.Option("--out", help="Alert directory")] = ROOT
    / "outputs"
    / "alerts",
    send: Annotated[
        bool,
        typer.Option(
            "--send",
            help="Post the Teams card and the e-mail when configured in .env; never by default",
        ),
    ] = False,
    db: DbOption = None,
) -> None:
    """Build the Alert object of a decided event and render it locally (JSON, HTML, Adaptive
    Card). The local channel is always on; Teams and e-mail only with --send and .env."""
    import os
    from datetime import UTC, datetime

    from radar.alerts import build_alert, write_alert
    from radar.alerts.render import render_html, render_teams_message
    from radar.alerts.send import send_email, send_teams
    from radar.audit import AuditEntry

    if (event_id is None) == (not all_events):
        raise typer.BadParameter("give an event id or --all")
    database = _db(db)
    settings = _settings()
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    universe = load_universe(CONFIG_DIR / "universe.yaml")
    if send:
        load_dotenv()
    events = database.list_events() if all_events else [database.get_event(event_id or "")]
    day = datetime.now(UTC).date().isoformat()
    rendered = 0
    for ev in events:
        if ev is None:
            raise typer.BadParameter(f"unknown event {event_id}")
        decision = database.get_decision(ev.event_id)
        if decision is None or (all_events and decision.final_priority is None):
            if not all_events:
                raise typer.BadParameter(
                    f"no decision stored for {ev.event_id}; run `radar decide`"
                )
            continue
        documents = {d: doc for d in ev.source_doc_ids if (doc := database.get_document(d))}
        try:
            issuer = universe.by_id(ev.issuer_id)
        except KeyError:
            issuer = None
        statements = [r for d in ev.source_doc_ids for r in database.statements_for_document(d)]
        built = build_alert(
            ev, decision, documents, issuer, rules=rules, alerts=settings.alerts,
            statements=statements,
        )  # fmt: skip
        paths = write_alert(built, out, day=day, viewer_base_url=settings.alerts.viewer_base_url)
        outcomes = []
        if send and built.route is not None:
            if "teams" in built.route.channels:
                outcomes.append(
                    send_teams(
                        render_teams_message(built, settings.alerts.viewer_base_url),
                        os.environ.get(settings.alerts.teams_webhook_env),
                    )
                )
            if "email" in built.route.channels:
                subject = f"[{built.priority}] {built.issuer_name}: {built.title}"
                outcomes.append(send_email(subject, render_html(built), os.environ))
        route = f"{', '.join(built.route.channels)} ({built.route.mode})" if built.route else "none"
        sent = (
            "; ".join(
                f"{o['channel']} {'sent' if o['sent'] else 'not sent'}: {o['detail']}"
                for o in outcomes
            )
            or "nothing sent (local rendering only)"
        )
        database.audit(
            AuditEntry(
                step="alert",
                event_id=ev.event_id,
                doc_id=ev.source_doc_ids[0] if ev.source_doc_ids else None,
                rules_version=decision.rules_version,
                status="ok",
                message=(
                    f"{built.priority or 'NONE'} alert {built.alert_id[:12]}: {len(paths)} "
                    f"files in {paths[0].parent}; route {route}; {sent}"
                ),
            )
        )
        typer.echo(f"{built.priority or 'NONE':<5} {built.issuer_name}: {built.title}")
        typer.echo(f"      {built.summary}")
        for path in paths:
            typer.echo(f"      {path}")
        typer.echo(f"      route {route}; {sent}")
        rendered += 1
    typer.echo(f"{rendered} alert(s) rendered")


@app.command("note")
def note(
    event_id: str,
    lang: Annotated[str, typer.Option("--lang", help="fr | en")] = "fr",
    no_llm: Annotated[
        bool, typer.Option("--no-llm", help="Deterministic note only, no key, no call")
    ] = False,
    out: Annotated[Path, typer.Option("--out", help="Notes directory")] = ROOT
    / "outputs"
    / "notes",
    db: DbOption = None,
) -> None:
    """Committee note FR or EN (SPEC 12): deterministic and complete without any model. With
    a key and DEMO_GENERATION_BUDGET_USD in .env, the notes model writes "Why it matters"
    and "Points to verify"; every sentence is tied to the verified facts or excluded."""
    import os
    from datetime import UTC, datetime

    from radar.alerts import build_alert
    from radar.audit import AuditEntry
    from radar.notes import build_note, render_html, render_markdown

    if lang not in ("fr", "en"):
        raise typer.BadParameter("--lang must be fr or en")
    database = _db(db)
    settings = _settings()
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    ev = database.get_event(event_id)
    if ev is None:
        raise typer.BadParameter(f"unknown event {event_id}")
    decision = database.get_decision(event_id)
    if decision is None:
        raise typer.BadParameter(f"no decision stored for {event_id}; run `radar decide`")
    documents = {d: doc for d in ev.source_doc_ids if (doc := database.get_document(d))}
    try:
        issuer = load_universe(CONFIG_DIR / "universe.yaml").by_id(ev.issuer_id)
    except KeyError:
        issuer = None
    statements = [r for d in ev.source_doc_ids for r in database.statements_for_document(d)]
    alert = build_alert(
        ev, decision, documents, issuer, rules=rules, alerts=settings.alerts,
        statements=statements,
    )  # fmt: skip
    prose = provenance = notice = None
    mode = "deterministic"
    if not no_llm:
        from radar.llm.pricing import load_pricing
        from radar.notes.llm import GenerationLedger, generate_prose

        load_dotenv()
        raw_cap = (os.environ.get(settings.demo.generation_budget_env) or "").strip()
        if not raw_cap:
            notice = (
                f"prose sections not generated: {settings.demo.generation_budget_env} is not "
                "set in .env"
            )
        else:
            role = settings.llm.roles["notes"]
            ledger = GenerationLedger(out / "generation_ledger.json", float(raw_cap))
            result = generate_prose(
                database, alert, lang, provider=_provider_for(role.provider),
                pricing=load_pricing(ROOT / settings.llm.pricing_file), ledger=ledger,
                model_id=role.model, reasoning_effort=role.reasoning_effort,
                temperature=role.temperature,
            )  # fmt: skip
            prose, provenance, notice = result.prose, result.provenance, result.reason
            if prose is not None:
                how = "cached" if result.cached else "generated"
                mode = f"with {role.model} prose ({how}, {result.cost_usd or 0:.4f} USD)"
    built = build_note(alert, lang, prose=prose, provenance=provenance, notice=notice)
    folder = out / datetime.now(UTC).date().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    md_path = folder / f"{event_id}.{lang}.md"
    html_path = folder / f"{event_id}.{lang}.html"
    md_path.write_text(render_markdown(built), encoding="utf-8")
    html_path.write_text(render_html(built), encoding="utf-8")
    v = built.verification
    database.audit(
        AuditEntry(
            step="note",
            event_id=event_id,
            doc_id=ev.source_doc_ids[0] if ev.source_doc_ids else None,
            model_id=provenance.get("model_id") if provenance else None,
            prompt_version=provenance.get("prompt_version") if provenance else None,
            cost_usd=provenance.get("cost_usd") if provenance else None,
            rules_version=decision.rules_version,
            status="ok",
            message=(
                f"{lang} note {built.note_id[:12]} ({mode}): {v['verified']} verified, "
                f"{v['unsupported']} unsupported excluded; {md_path}"
            ),
        )
    )
    typer.echo(f"{lang} note for {built.issuer_name} ({built.priority or 'NONE'}): {mode}")
    typer.echo(f"      verified {v['verified']}, unsupported excluded {v['unsupported']}")
    if notice:
        typer.echo(f"      {notice}")
    typer.echo(f"      {md_path}")
    typer.echo(f"      {html_path}")


@app.command("viewer")
def viewer(
    port: Annotated[int, typer.Option("--port", help="Streamlit port")] = 8501,
    db: DbOption = None,
) -> None:
    """Start the Streamlit viewer (four screens: dashboard, watchlist, alerts, event detail
    with "Why this priority?"). Reads the database only."""
    import os
    import subprocess
    import sys

    app_path = Path(__file__).resolve().parent / "viewer" / "app.py"
    db_path = db if db is not None else ROOT / _settings().paths.db
    env = {**os.environ, "RADAR_DB": str(db_path)}
    typer.echo(f"Starting the Streamlit viewer on port {port} (database {db_path})")
    argv = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.headless", "true", "--server.port", str(port),
    ]  # fmt: skip
    subprocess.run(argv, env=env, check=False)


@app.command("export-html")
def export_html(
    out: Annotated[Path, typer.Option("--out", help="Site directory")] = ROOT / "outputs" / "site",
    db: DbOption = None,
) -> None:
    """Static HTML export of the viewer (index with the two universes, the alerts and the
    routing, one page per decided event): the portable fallback, no server needed."""
    from radar.viewer.export import export_site

    database = _db(db)
    paths = export_site(
        database,
        load_universe(CONFIG_DIR / "universe.yaml"),
        load_rules(CONFIG_DIR / "rules.yaml"),
        _settings(),
        out,
    )
    typer.echo(f"{len(paths)} file(s) written under {out}")
    typer.echo(f"open {paths[0]}")


@app.command("llm-extract")
def llm_extract(
    out: Path | None = OUT_OPTION,
    events: bool = EVENTS_OPTION,
    kind: str = KIND_OPTION,
    issuer: str | None = ISSUER_OPTION,
    doc: str | None = DOC_OPTION,
    gold: Path = GOLD_OPTION,
    alternative: str | None = typer.Option(
        None, "--alternative", help="Benchmark alternative from settings (openai_sol, anthropic)"
    ),
    challenger: bool = typer.Option(
        False, "--challenger", help="Use the routed challenger of this kind instead of its default"
    ),
    limit: int | None = typer.Option(None, "--limit", help="Only the first N gold documents"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Render the prompts and estimate the cost, call nothing"
    ),
    cache_only: bool = typer.Option(
        False,
        "--cache-only",
        help="Replay cached answers only: a document without a cached answer is skipped, "
        "no provider call is made, zero cost (demo and re-validation runs)",
    ),
    db: DbOption = None,
) -> None:
    """Guidance extraction with cache, budget hard stop and two level validation. With --out:
    benchmark on the frozen gold set (run directory). With --events: the documents behind
    the stored earnings_release events, statements stored, events never touched (apply them
    with `radar llm-apply`)."""
    from radar.eval.benchmark import BenchmarkConfig, run_benchmark
    from radar.eval.gold import load_gold
    from radar.llm.budget import BudgetExceeded, RunBudget
    from radar.llm.cache import LLMCache
    from radar.llm.pricing import load_pricing
    from radar.llm.prompts import load_prompt
    from radar.llm.provider import FakeProvider

    load_dotenv()
    settings, selection = _select_model(kind, alternative, challenger)
    role = selection.model
    pricing = load_pricing(ROOT / settings.llm.pricing_file)
    if kind not in ("guidance", "liquidity", "covenant", "going_concern"):
        raise typer.BadParameter(
            f"unknown kind {kind!r}, expected guidance, liquidity, covenant or going_concern"
        )
    if dry_run:
        budget = RunBudget(limit_usd=1.0, pricing=pricing)
        provider = FakeProvider(name=role.provider, raw_json="{}")
    elif cache_only:
        from radar.llm.provider import RefusingProvider

        # a budget below any call's estimate: every document without a cached answer is
        # refused before the provider is reached; the provider refuses as well
        budget = RunBudget(limit_usd=1e-9, pricing=pricing)
        provider = RefusingProvider(name=role.provider)
    else:
        try:
            budget = RunBudget.from_env(pricing)
        except BudgetExceeded as exc:
            typer.echo(f"refused: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        provider = _provider_for(role.provider)
    database = _db(db)
    database.init_schema()
    if events == (out is not None):
        raise typer.BadParameter("give exactly one of --out (benchmark) or --events (pipeline)")
    if events:
        from radar.enrich.extract import extract_events

        if dry_run:
            raise typer.BadParameter("--dry-run applies to the benchmark mode only")
        from radar.enrich.extract import KINDS

        if kind not in KINDS:
            raise typer.BadParameter(f"unknown kind {kind!r}, expected one of {sorted(KINDS)}")
        result = extract_events(
            database,
            load_universe(),
            provider=provider,
            cache=LLMCache(database),
            budget=budget,
            prompt=load_prompt(ROOT / "prompts" / "extraction" / f"{kind}.v1.yaml"),
            model_id=role.model,
            reasoning_effort=role.reasoning_effort,
            temperature=role.temperature,
            issuer_id=issuer,
            doc_id=doc,
            kind=kind,
            selection=selection.as_record(),
            cache_only=cache_only,
        )
        typer.echo(f"model {role.model} as {selection.role} for {kind}: {selection.reason}")
        typer.echo(
            f"{result.documents} document(s): {result.calls} call(s), {result.cached} cached, "
            f"{result.cost_usd:.4f} USD, {result.statements} statement(s) stored, "
            f"{result.valid} valid; events untouched (run `radar llm-apply`)"
        )
        for source, status, error in result.skipped:
            typer.echo(f"  skipped {source[:12]}: {status} {error or ''}")
        return
    assert out is not None
    config = BenchmarkConfig(
        model_id=role.model, reasoning_effort=role.reasoning_effort, temperature=role.temperature
    )
    if kind == "liquidity":
        from radar.eval.liquidity import load_liquidity_gold

        gold_rows = load_liquidity_gold(gold)
    elif kind == "covenant":
        from radar.eval.covenant import load_covenant_gold

        gold_rows = load_covenant_gold(gold)
    elif kind == "going_concern":
        from radar.eval.going_concern import load_going_concern_gold

        gold_rows = load_going_concern_gold(gold)
    else:
        gold_rows = load_gold(gold)
    summary = run_benchmark(
        gold_rows,
        db=database,
        universe=load_universe(),
        provider=provider,
        cache=LLMCache(database),
        budget=budget,
        prompt=load_prompt(ROOT / "prompts" / "extraction" / f"{kind}.v1.yaml"),
        config=config,
        out_dir=out,
        limit=limit,
        dry_run=dry_run,
        gold_path=gold,
        kind=kind,
        selection=selection.as_record(),
        cache_only=cache_only,
    )
    typer.echo(
        f"run {summary.run_id}: {summary.n_documents} documents, statuses {summary.statuses}, "
        f"model {config.model_id} (effort {config.reasoning_effort}), provider {provider.name}"
    )
    typer.echo(f"model chosen as {selection.role} for {kind}: {selection.reason}")
    typer.echo(
        f"cost {summary.total_cost_usd:.4f} USD (estimated before the calls "
        f"{summary.estimated_cost_usd:.4f}), budget {summary.budget_limit_usd:.2f}, "
        f"remaining {summary.budget_remaining_usd:.4f}"
    )
    if kind == "guidance":
        _echo_metrics(summary.metrics)
    else:
        typer.echo(json.dumps(summary.metrics, indent=2))
    typer.echo(f"written: {out / 'outputs.jsonl'}, {out / 'run.json'}, {out / 'metrics.json'}")


@app.command("llm-apply")
def llm_apply(
    event: str | None = EVENT_OPTION,
    issuer: str | None = ISSUER_OPTION,
    db: DbOption = None,
) -> None:
    """Apply the stored VALID statements to the earnings_release events: fills only empty
    deterministic fields, keeps the provenance, decides again and audits before/after.
    Idempotent: a second run changes nothing."""
    from radar.enrich.apply import apply_all, apply_event

    database = _db(db)
    database.init_schema()
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    scales = load_rating_scales(CONFIG_DIR / "rating_scales.yaml")
    results = (
        [apply_event(database, event, rules, scales)]
        if event
        else apply_all(database, rules, scales, issuer)
    )
    for r in results:
        line = f"{r.event_id[:12]} {r.status}: priority {r.priority_before} -> {r.priority_after}"
        if r.conflicts:
            line += f", deterministic conflicts {r.conflicts}"
        typer.echo(line)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    typer.echo(f"{len(results)} event(s): {counts}")


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

    kind = json.loads((runs[0] / "run.json").read_text(encoding="utf-8")).get("kind", "guidance")
    if kind in ("liquidity", "covenant", "going_concern"):
        from radar.eval.flagfamily import compare_family_runs, render_family_markdown

        extra: tuple[str, ...] = ()
        if kind == "liquidity":
            from radar.eval.liquidity import NEGATIVE as negative
            from radar.eval.liquidity import load_liquidity_gold as loader
        elif kind == "going_concern":
            from radar.eval.going_concern import NEGATIVE as negative
            from radar.eval.going_concern import load_going_concern_gold as loader
        else:
            from radar.eval.covenant import NEGATIVE as negative
            from radar.eval.covenant import load_covenant_gold as loader

            extra = ("resolution",)
        family_table = compare_family_runs(
            loader(gold), runs, negative=frozenset(negative), extra_fields=extra
        )
        markdown = render_family_markdown(family_table)
        if out is not None:
            out.write_text(markdown, encoding="utf-8")
        typer.echo(markdown)
        return
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
