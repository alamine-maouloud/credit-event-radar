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
    if source == "fixtures":
        from radar.connectors.fixture import FixtureAdapter

        adapter = FixtureAdapter(
            fixtures_dir or ROOT / "tests" / "fixtures" / "edgar", raw, SecHtmlNormalizer()
        )
    elif source == "edgar":
        from radar.connectors.edgar import EdgarAdapter

        settings = _settings()
        adapter = EdgarAdapter(
            raw, max_requests_per_second=settings.ingestion.sec.max_requests_per_second
        )
    else:
        raise typer.BadParameter("source must be edgar or fixtures")
    if url:
        summary = ingest_url(database, adapter, url)
    else:
        summary = run_ingest(database, adapter, since=date.fromisoformat(since), issuers=issuers)
    typer.echo(
        f"fetched {summary.fetched}, stored {summary.stored}, duplicates {summary.duplicates}"
    )
    for doc_id in summary.doc_ids:
        typer.echo(f"  {doc_id}")


@app.command()
def process(db: DbOption = None) -> None:
    """Resolve issuers, run deterministic extraction and deduplication on new documents."""
    from radar.pipeline import process as run_process

    database = _db(db)
    universe = load_universe(CONFIG_DIR / "universe.yaml")
    scales = load_rating_scales(CONFIG_DIR / "rating_scales.yaml")
    s = run_process(database, universe, scales)
    typer.echo(
        f"documents {s.documents}, resolved {s.resolved}, unresolved {s.unresolved}, "
        f"events new {s.events_new}, merged {s.events_merged}, "
        f"candidates rejected {s.candidates_skipped}"
    )
    for event_id in s.event_ids:
        typer.echo(f"  {event_id}")


@app.command()
def events(
    issuer: Annotated[str | None, typer.Option(help="Issuer id")] = None, db: DbOption = None
) -> None:
    """List stored events."""
    database = _db(db)
    for ev in database.list_events(issuer):
        f = ev.fields
        detail = (
            f"{f.get('agency')} {f.get('old_rating')} to {f.get('new_rating')}"
            if ev.family == "rating"
            else f.get("form", "")
        )
        kind = f"{ev.family}/{ev.event_type}"
        typer.echo(f"{ev.event_id}  {ev.issuer_id}  {kind}  {ev.effective_date}  {detail}")


@app.command("show-event")
def show_event(event_id: str, db: DbOption = None) -> None:
    """Trace one event back to its fields, source passages, documents and audit trail."""
    database = _db(db)
    ev = database.get_event(event_id)
    if ev is None:
        raise typer.BadParameter(f"unknown event {event_id}")
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
    typer.echo("Priority decided by: not yet computed (Phase 4). LLM used for: none.")


if __name__ == "__main__":
    app()
