"""The public demo bundle (demo/bundle): restore the public documents from their official
URLs with hash checks, load the validated model statements recorded for them, verify the
featured cases. No model call, no secret, no private file."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from radar.audit import AuditEntry
from radar.config import ROOT, Universe, load_universe
from radar.connectors.fixture import RESTORED_NAME, FixtureAdapter
from radar.db import Database
from radar.normalize import SecHtmlNormalizer

BUNDLE_DIR = ROOT / "demo" / "bundle"
DEFAULT_USER_AGENT = (
    "Credit Event Radar demo (https://github.com/alamine-maouloud/credit-event-radar)"
)
Fetcher = Callable[[str, str], bytes]  # (url, user_agent) -> raw bytes


@dataclass
class Bundle:
    documents: list[dict[str, Any]]
    statements: list[dict[str, Any]]

    def document(self, doc_id: str) -> dict[str, Any] | None:
        return next((d for d in self.documents if d["normalized_sha256"] == doc_id), None)


@dataclass
class RestoreReport:
    kept: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    raw_mismatch: list[str] = field(default_factory=list)  # bytes differ, normalised text equal
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


@dataclass
class LoadReport:
    loaded: int = 0
    failures: list[str] = field(default_factory=list)


def load_bundle(bundle_dir: Path = BUNDLE_DIR) -> Bundle:
    documents = json.loads((bundle_dir / "documents.json").read_text(encoding="utf-8"))["documents"]
    statements = [
        json.loads(line)
        for line in (bundle_dir / "statements.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return Bundle(documents, statements)


SEC_UA_HELP = (
    "sec.gov requires a declared User-Agent with a contact address: set SEC_USER_AGENT in "
    ".env (copy .env.example; no API key is needed for the demo)"
)


def sec_user_agent() -> str | None:
    """SEC_USER_AGENT from the environment when it names a real contact, else None: the SEC
    refuses anonymous or placeholder agents, and the project never ships a contact."""
    value = (os.environ.get("SEC_USER_AGENT") or "").strip()
    if value and "@" in value and "example.com" not in value:
        return value
    return None


def user_agent() -> str:
    """The agent for the issuers' own sites: the declared SEC one when set, the project's
    public identity otherwise (a handful of requests)."""
    return sec_user_agent() or DEFAULT_USER_AGENT


def http_fetch(url: str, agent: str) -> bytes:
    import httpx

    response = httpx.get(
        url, headers={"User-Agent": agent, "Accept-Encoding": "gzip, deflate"},
        follow_redirects=True, timeout=90.0,
    )  # fmt: skip
    response.raise_for_status()
    return response.content


def _normalized_sha(directory: Path, universe: Universe) -> str | None:
    with tempfile.TemporaryDirectory() as tmp:
        docs = FixtureAdapter(directory, Path(tmp), SecHtmlNormalizer()).fetch(
            date(2000, 1, 1), universe.issuers
        )
    return docs[0].doc_id if docs else None


def restore_documents(
    bundle: Bundle,
    *,
    root: Path = ROOT,
    universe: Universe | None = None,
    fetch: Fetcher = http_fetch,
    agent: str | None = None,
    offline: bool = False,
) -> RestoreReport:
    """Make every bundle document available as a fixture with bytes. Local bytes are kept
    when their normalised text matches the manifest; otherwise the document is fetched from
    its official URL and accepted only when the raw SHA-256 matches, or when the raw bytes
    differ but the normalised text is identical (EDGAR appends a changing anti-bot script
    tag). A document that cannot be fetched or that changed at the source is reported, never
    silently skipped."""
    universe = universe or load_universe()
    agent = agent or user_agent()
    report = RestoreReport()
    for item in bundle.documents:
        directory = root / item["fixture"]
        label = f"{item['label']} ({item['fixture']})"
        raw_path = directory / item["raw_file"]
        if raw_path.exists():
            try:
                if _normalized_sha(directory, universe) == item["normalized_sha256"]:
                    report.kept.append(label)
                    continue
            except Exception as exc:  # unreadable bytes: fetched again below
                report.failed.append((label, f"local bytes unreadable: {exc}"))
                continue
        if offline:
            report.failed.append((label, "bytes missing locally and offline mode requested"))
            continue
        if "sec.gov" in item["source_url"] and agent == DEFAULT_USER_AGENT:
            report.failed.append((label, SEC_UA_HELP))
            continue
        try:
            raw = fetch(item["source_url"], agent)
        except Exception as exc:
            report.failed.append((label, f"fetch failed from {item['source_url']}: {exc}"))
            continue
        raw_sha = hashlib.sha256(raw).hexdigest()
        directory.mkdir(parents=True, exist_ok=True)
        with open(raw_path, "wb") as raw_fh:
            with gzip.GzipFile(fileobj=raw_fh, mode="wb", mtime=0) as fh:
                fh.write(raw)
        sidecar = directory / RESTORED_NAME
        if raw_sha != item["raw_sha256"]:
            sidecar.write_text(
                json.dumps(
                    {
                        "raw_sha256": raw_sha,
                        "raw_size_bytes": len(raw),
                        "retrieved_at": datetime.now(UTC).isoformat(),
                        "source_url": item["source_url"],
                        "note": (
                            "bytes fetched today differ from the manifest (a changing tag); "
                            "the normalised text was verified identical to the manifest"
                        ),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        elif sidecar.exists():
            sidecar.unlink()
        try:
            normalized = _normalized_sha(directory, universe)
        except Exception as exc:
            normalized = None
            reason = f"normalisation failed: {exc}"
        else:
            reason = "document changed at the source: the normalised text differs from the manifest"
        if normalized != item["normalized_sha256"]:
            raw_path.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
            report.failed.append((label, reason))
            continue
        report.restored.append(label)
        if raw_sha != item["raw_sha256"]:
            report.raw_mismatch.append(label)
    return report


def load_statements(db: Database, bundle: Bundle) -> LoadReport:
    """Insert the validated statements of the bundle, the quoted passage rebuilt from the
    ingested document at the recorded offsets and checked against its SHA-256."""
    report = LoadReport()
    events_by_doc: dict[str, str] = {}
    for event in db.list_events():
        for doc_id in event.source_doc_ids:
            if event.family == "earnings":
                events_by_doc.setdefault(doc_id, event.event_id)
    rows = []
    for st in bundle.statements:
        doc = db.get_document(st["doc_id"])
        if doc is None:
            report.failures.append(
                f"{st['statement_id'][:12]}: document {st['doc_id'][:12]} not ingested"
            )
            continue
        quote = doc.text[st["quote_start"] : st["quote_end"]]
        if hashlib.sha256(quote.encode("utf-8")).hexdigest() != st["quote_sha256"]:
            report.failures.append(
                f"{st['statement_id'][:12]}: passage at {st['quote_start']}-{st['quote_end']} "
                "does not match its recorded hash"
            )
            continue
        rows.append(
            {
                "statement_id": st["statement_id"],
                "llm_call_id": None,
                "doc_id": st["doc_id"],
                "issuer_id": st["issuer_id"],
                "event_id": events_by_doc.get(st["doc_id"]),
                "model_id": st["model_id"],
                "resolved_model": st.get("resolved_model"),
                "prompt_version": st["prompt_version"],
                "schema_version": st["schema_version"],
                "statement_json": {**st["statement_json"], "evidence_quote": quote},
                "validation_json": st["validation_json"],
                "change_json": st.get("change_json") or {},
                "validation_status": st["validation_status"],
                "statement_kind": st["statement_kind"],
                "model_selection_json": st.get("model_selection_json"),
            }
        )
    if rows:
        db.insert_statements(rows)
        report.loaded = len(rows)
        db.audit(
            AuditEntry(
                step="demo_bundle",
                status="ok",
                message=(
                    f"{len(rows)} validated statement(s) loaded from the public demo bundle, "
                    "passages rebuilt from the documents and checked against their hashes"
                ),
            )
        )
    return report


FEATURED: dict[str, dict[str, Any]] = {
    "HARLEY_DAVIDSON_INC": {"family": "rating", "priority": "P1", "rules": {"RAT-01", "RAT-02"}},
    "HYDROFARM": {"family": "earnings", "priority": "P1", "rules": {"ERN-01"}},
    "OMV": {"family": "earnings", "priority": None, "recorded": ("going_concern", "negated")},
    "VOLKSWAGEN": {"family": "earnings", "priority": "P1", "rules": {"ERN-02"}},
}


def verify_featured(db: Database, bundle: Bundle) -> list[str]:
    """The four cases of docs/DEMO.md, as decided: problems listed, empty when all is well."""
    problems: list[str] = []
    bundle_docs = {d["normalized_sha256"] for d in bundle.documents}
    for issuer, want in FEATURED.items():
        events = [
            e
            for e in db.list_events(issuer)
            if e.family == want["family"] and set(e.source_doc_ids) & bundle_docs
        ]
        if not events:
            problems.append(f"{issuer}: no {want['family']} event on a bundle document")
            continue
        decided = [(e, db.get_decision(e.event_id)) for e in events]
        if want["priority"]:
            hits = [
                (e, d)
                for e, d in decided
                if d
                and d.final_priority == want["priority"]
                and want["rules"] <= set(d.triggered_ids())
            ]
            if not hits:
                found = [
                    (d.final_priority if d else None, d.triggered_ids() if d else [])
                    for _, d in decided
                ]
                problems.append(
                    f"{issuer}: expected {want['priority']} by {sorted(want['rules'])}, "
                    f"found {found}"
                )
            continue
        event, decision = decided[0]
        if decision is None or decision.final_priority is not None:
            found_priority = decision.final_priority if decision else "no decision"
            problems.append(f"{issuer}: expected no priority, found {found_priority}")
        kind, status = want["recorded"]
        rows = [r for d in event.source_doc_ids for r in db.statements_for_document(d)]
        if not any(
            r["statement_kind"] == kind
            and r["validation_status"] == "VALID"
            and r["statement_json"].get("status") == status
            for r in rows
        ):
            problems.append(f"{issuer}: expected a validated {kind} statement with status {status}")
    return problems
