"""Typer command-line interface.

Phase 1 only exposes ``version``. Pipeline commands (init-db, seed, ingest, process,
alert, note, eval, live) are added in their respective phases.
"""

from __future__ import annotations

import typer

from radar import __version__

app = typer.Typer(
    name="radar",
    help="Credit Event Radar: auditable AI-assisted credit monitoring.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Credit Event Radar command-line interface."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"credit-event-radar {__version__}")


if __name__ == "__main__":
    app()
