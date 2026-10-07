"""Build the demo database from the public bundle, reproducibly, without any model call.

Default (public): restore the five public documents of demo/bundle from their official URLs
(local bytes kept when their normalised text matches), check the hashes, ingest, process,
load the validated statements of the bundle (passages rebuilt from the documents and checked
against their hashes), apply the enrichments, decide, verify the four featured cases, then
render the alerts and the static site. Missing or changed documents stop the build with the
list of what is missing; nothing is built silently.

uv run python scripts/build_demo_db.py --fresh
uv run python scripts/build_demo_db.py --fresh --offline     # fail instead of fetching

--full is the local variant used during development: every fixture in hand and the model
answers replayed from a local cache database, zero cost (requires the private bytes).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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
    conn = sqlite3.connect(db)
    conn.execute("ATTACH DATABASE ? AS src", (str(cache_from),))
    before = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO llm_cache SELECT * FROM src.llm_cache")
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    conn.close()
    return after - before


def build_public(args: argparse.Namespace, common: list[str]) -> int:
    from radar.config import load_dotenv
    from radar.db import Database
    from radar.demo import load_bundle, load_statements, restore_documents, verify_featured

    load_dotenv()  # SEC_USER_AGENT for sec.gov; never a key, never required for the demo
    bundle = load_bundle()
    print(
        f"bundle: {len(bundle.documents)} public documents, {len(bundle.statements)} validated statements"
    )
    report = restore_documents(bundle, root=args.fixtures_root, offline=args.offline)
    for label in report.kept:
        print(f"  kept      {label}")
    for label in report.restored:
        note = (
            " (raw bytes differ at the source, normalised text identical)"
            if label in report.raw_mismatch
            else ""
        )
        print(f"  restored  {label}{note}")
    if not report.ok:
        print("\nDemo not built. The following documents could not be restored:", file=sys.stderr)
        for label, reason in report.failed:
            print(f"  missing   {label}: {reason}", file=sys.stderr)
        print(
            "Check the network access to sec.gov and the issuers' sites, then retry.",
            file=sys.stderr,
        )
        return 2
    radar("init-db", *common)
    radar("seed", *common)
    for item in bundle.documents:
        fixture_root = (args.fixtures_root / item["fixture"]).parent
        radar(
            "ingest", "--since", "2024-01-01", "--source", "fixtures", "--fixtures-dir", str(fixture_root),
            "--issuer", item["issuer_id"], "--url", item["source_url"], "--raw-dir", str(args.raw_dir), *common,
        )  # fmt: skip
    radar("process", *common)
    db = Database(args.db)
    loaded = load_statements(db, bundle)
    print(f"statements: {loaded.loaded} loaded from the bundle")
    for failure in loaded.failures:
        print(f"  failed    {failure}", file=sys.stderr)
    db.close()
    if loaded.failures:
        print(
            "Demo not built: validated statements could not be tied to the documents.",
            file=sys.stderr,
        )
        return 2
    radar("llm-apply", *common)
    radar("decide", *common)
    db = Database(args.db)
    problems = verify_featured(db, bundle)
    db.close()
    if problems:
        print("\nDemo not built as expected:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    print("featured cases verified: Harley-Davidson P1, Hydrofarm P1, OMV no alert, Volkswagen P1")
    radar("alert", "--all", "--out", str(args.alerts_out), *common)
    radar("export-html", "--out", str(args.site_out), *common)
    print(f"demo database ready: {args.db}")
    return 0


def build_full(args: argparse.Namespace, common: list[str]) -> int:
    radar("init-db", *common)
    radar("seed", *common)
    for root in FIXTURE_ROOTS:
        radar(
            "ingest", "--since", "2024-01-01", "--source", "fixtures", "--fixtures-dir", str(root),
            "--raw-dir", str(args.raw_dir), *common,
        )  # fmt: skip
    radar("process", *common)
    if args.cache_from.exists():
        print(
            f"cache: {copy_cache(args.db, args.cache_from)} answer(s) copied from {args.cache_from}"
        )
    else:
        print(f"cache: {args.cache_from} not found, no LLM statement will be replayed")
    for kind in KINDS:
        radar("llm-extract", "--events", "--kind", kind, "--cache-only", *common)
    radar("llm-apply", *common)
    radar("decide", *common)
    radar("alert", "--all", "--out", str(args.alerts_out), *common)
    radar("export-html", "--out", str(args.site_out), *common)
    print(f"demo database ready: {args.db}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "demo.db")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw-demo")
    parser.add_argument("--alerts-out", type=Path, default=ROOT / "outputs" / "alerts")
    parser.add_argument("--site-out", type=Path, default=ROOT / "outputs" / "site")
    parser.add_argument(
        "--fixtures-root", type=Path, default=ROOT, help="where the fixture directories live"
    )
    parser.add_argument("--fresh", action="store_true", help="delete the demo database first")
    parser.add_argument(
        "--offline", action="store_true", help="never fetch: fail when bytes are missing"
    )
    parser.add_argument(
        "--full", action="store_true", help="local variant: every fixture plus a cache replay"
    )
    parser.add_argument("--cache-from", type=Path, default=ROOT / "data" / "radar.db")
    args = parser.parse_args()
    if args.fresh and args.db.exists():
        args.db.unlink()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    common = ["--db", str(args.db)]
    return build_full(args, common) if args.full else build_public(args, common)


if __name__ == "__main__":
    sys.exit(main())
