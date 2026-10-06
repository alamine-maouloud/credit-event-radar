"""Build the demo database at zero cost: the fixtures in hand (live watchlist documents and
the historical stress cases), the deterministic pipeline, then the LLM statements replayed
from the cache of the benchmark runs (no provider call), applied and decided, with the
alerts and the static site rendered.

uv run python scripts/build_demo_db.py --db data/demo.db --cache-from data/radar.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOTS = [
    ROOT / "tests" / "fixtures" / "edgar",
    ROOT / "tests" / "fixtures" / "ir",
    ROOT / "tests" / "fixtures" / "gold" / "guidance",
]
KINDS = ["guidance", "liquidity", "covenant", "going_concern"]


def radar(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = ["uv", "run", "radar", *args]
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True, env={**os.environ, **(env or {})})


def copy_cache(db: Path, cache_from: Path) -> int:
    """Copy the cached model answers of the benchmark runs into the demo database."""
    conn = sqlite3.connect(db)
    conn.execute("ATTACH DATABASE ? AS src", (str(cache_from),))
    before = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO llm_cache SELECT * FROM src.llm_cache")
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    conn.close()
    return after - before


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "demo.db")
    parser.add_argument("--cache-from", type=Path, default=ROOT / "data" / "radar.db")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw-demo")
    parser.add_argument("--alerts-out", type=Path, default=ROOT / "outputs" / "alerts")
    parser.add_argument("--site-out", type=Path, default=ROOT / "outputs" / "site")
    parser.add_argument("--fresh", action="store_true", help="delete the demo database first")
    args = parser.parse_args()
    if args.fresh and args.db.exists():
        args.db.unlink()
    common = ["--db", str(args.db)]
    radar("init-db", *common)
    radar("seed", *common)
    for root in FIXTURE_ROOTS:
        radar(
            "ingest", "--since", "2024-01-01", "--source", "fixtures", "--fixtures-dir", str(root),
            "--raw-dir", str(args.raw_dir), *common,
        )  # fmt: skip
    radar("process", *common)
    if args.cache_from.exists():
        print(f"cache: {copy_cache(args.db, args.cache_from)} answer(s) copied from {args.cache_from}")
    else:
        print(f"cache: {args.cache_from} not found, no LLM statement will be replayed")
    for kind in KINDS:
        radar("llm-extract", "--events", "--kind", kind, "--cache-only", *common)
    radar("llm-apply", *common)
    radar("decide", *common)
    radar("alert", "--all", "--out", str(args.alerts_out), *common)
    radar("export-html", "--out", str(args.site_out), *common)
    print(f"demo database ready: {args.db}")


if __name__ == "__main__":
    sys.exit(main())
