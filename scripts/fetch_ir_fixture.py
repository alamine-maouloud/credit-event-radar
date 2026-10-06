"""Fetch one issuer document as a private golden fixture (ADR-010).

The raw bytes are stored gzipped next to a manifest; only the manifest is committed. The
manifest records provenance (URL, hashes, dates, size) and the expected deterministic
extraction, replayed by tests/test_ir_integration_local.py when the bytes are present.

uv run python scripts/fetch_ir_fixture.py --issuer-id VOLKSWAGEN --source-id vw_ratings_page \\
  --url https://www.volkswagen-group.com/en/ratings-15756 --out tests/fixtures/ir/vw_ratings_page
"""

from __future__ import annotations

import argparse
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from radar.config import (
    CONFIG_DIR,
    SEEDS_DIR,
    load_dotenv,
    load_rating_scales,
    load_ratings_seed,
    load_rules,
    load_settings,
    load_universe,
)
from radar.connectors.fixture import write_fixture
from radar.connectors.ir import IRSourceAdapter
from radar.db import Database
from radar.pipeline import process, run_extractors
from radar.snapshot import FetchedBytes


def expected_extraction(doc, issuer, scales, universe, seed, rules) -> dict:
    """Expected outcome, observations, stored events and decisions after a full process run."""
    _, _, _, profile = run_extractors(doc, issuer, scales)
    out: dict = {"table_profile": profile, "observations": [], "events": [], "decisions": []}
    keys = ("agency", "old_rating", "new_rating", "rating", "new_outlook", "old_outlook", "watch",
            "amount", "currency", "amount_eur_equiv", "coupon", "maturity", "seniority",
            "guidance_status", "flags", "period")  # fmt: skip
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "r.db")
        db.init_schema()
        db.replace_universe(universe)
        db.replace_ratings(seed)
        db.insert_document(doc)
        process(db, universe, scales, rules)
        for o in db.observations_for(issuer.id):
            out["observations"].append(
                {
                    "agency": o.agency,
                    "rating": o.rating,
                    "outlook": o.outlook,
                    "watch": o.watch,
                    "rating_date": o.rating_date.isoformat() if o.rating_date else None,
                    "as_of_basis": o.as_of_basis,
                }  # fmt: skip
            )
        for ev in db.list_events(issuer.id):
            out["events"].append(
                {
                    "family": ev.family,
                    "event_type": ev.event_type,
                    "effective_date": ev.effective_date.isoformat() if ev.effective_date else None,
                    "fields": {k: ev.fields.get(k) for k in keys if k in ev.fields},
                    "n_spans": len(ev.evidence),
                }
            )
            d = db.get_decision(ev.event_id)
            out["decisions"].append(
                {
                    "event_type": ev.event_type,
                    "final_priority": d.final_priority if d else None,
                    "triggered": d.triggered_ids() if d else [],
                }
            )
        out["outcome"] = db.document_status(doc.doc_id)["outcome"]
        db.close()
    return out


def recompute(directory: Path) -> None:
    """Recompute the expected block of an existing fixture from its bytes, no network."""
    from radar.connectors.fixture import FixtureAdapter, load_manifest, write_fixture
    from radar.normalize import SecHtmlNormalizer

    manifest = load_manifest(directory)
    universe = load_universe(CONFIG_DIR / "universe.yaml")
    scales = load_rating_scales(CONFIG_DIR / "rating_scales.yaml")
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    seed = load_ratings_seed(SEEDS_DIR / "ratings_seed.csv", scales, universe)
    issuer = universe.by_id(manifest["issuer_id"])
    with tempfile.TemporaryDirectory() as tmp:
        adapter = FixtureAdapter(directory, Path(tmp), SecHtmlNormalizer())
        doc = adapter.fetch(date(2000, 1, 1), [issuer])[0]
        raw = Path(doc.raw_path).read_bytes()
    expected = expected_extraction(doc, issuer, scales, universe, seed, rules)
    fetched = FetchedBytes(
        url=manifest["source_url"], content=raw,
        retrieved_at=datetime.fromisoformat(manifest["retrieved_at"]), content_type=manifest.get("content_type"),
    )  # fmt: skip
    doc = doc.model_copy(update={"retrieved_at": fetched.retrieved_at})
    write_fixture(
        directory, fetched, doc, role=manifest["role"], issuer_id=issuer.id, expected=expected
    )
    print(
        f"{directory.name}: outcome {expected['outcome']}, {len(expected['observations'])} observation(s), {len(expected['events'])} stored event(s), decisions {[(d['event_type'], d['final_priority']) for d in expected['decisions']]}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
    )
    parser.add_argument(
        "--issuer-id",
    )
    parser.add_argument("--source-id", help="id of the ir_sources entry in universe.yaml")
    parser.add_argument(
        "--published", default=None, help="YYYY-MM-DD publication date of the document"
    )
    parser.add_argument("--title", default=None)
    parser.add_argument("--role", default="demo_watchlist")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--recompute-expected",
        action="store_true",
        help="No network: recompute expected from the stored bytes",
    )
    args = parser.parse_args()
    if args.recompute_expected:
        recompute(args.out)
        return

    load_dotenv()
    settings = load_settings(CONFIG_DIR / "settings.yaml")
    universe = load_universe(CONFIG_DIR / "universe.yaml")
    scales = load_rating_scales(CONFIG_DIR / "rating_scales.yaml")
    rules = load_rules(CONFIG_DIR / "rules.yaml")
    seed = load_ratings_seed(SEEDS_DIR / "ratings_seed.csv", scales, universe)
    issuer = universe.by_id(args.issuer_id)
    source = next(s for s in issuer.ir_sources if s.id == args.source_id)

    with tempfile.TemporaryDirectory() as tmp:
        adapter = IRSourceAdapter(
            Path(tmp),
            min_interval_seconds=settings.ingestion.ir.min_interval_seconds,
            respect_robots=settings.ingestion.ir.respect_robots,
        )
        doc = adapter.fetch_document(args.url)
        raw = Path(doc.raw_path).read_bytes()
        content_type = doc.extra.get("content_type")
    extra = {
        **doc.extra,
        "source_id": source.id,
        "kind": source.kind,
        "document_type": source.document_type,
        "table_profile": source.table_profile,
        "expected_content": source.content,
        "published": args.published,
    }
    published_at = None
    if args.published:
        d = date.fromisoformat(args.published)
        published_at = datetime(d.year, d.month, d.day, tzinfo=UTC)
    doc = doc.model_copy(
        update={
            "extra": extra,
            "issuer_hint": issuer.id,
            "published_at": published_at,
            "title": args.title or source.id,
        }
    )
    expected = expected_extraction(doc, issuer, scales, universe, seed, rules)
    fetched = FetchedBytes(
        url=args.url, content=raw, retrieved_at=doc.retrieved_at, content_type=content_type
    )
    path = write_fixture(
        args.out, fetched, doc, role=args.role, issuer_id=issuer.id, expected=expected
    )
    print(f"wrote {path}")
    print(
        f"raw sha256 {doc.content_hash}, {doc.raw_size_bytes} bytes, normalised {len(doc.text)} chars ({doc.normalizer_version})"
    )
    print(
        f"expected: outcome {expected['outcome']}, {len(expected['observations'])} observation(s), {len(expected['events'])} event(s), decisions {[(d['event_type'], d['final_priority']) for d in expected['decisions']]}"
    )


if __name__ == "__main__":
    main()
