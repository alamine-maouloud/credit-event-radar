"""SQLite storage (docs/SPEC.md section 7.3), standard library ``sqlite3`` only.

Phase 2 creates the tables needed by ingestion and deterministic extraction. Tables for
priority decisions, claims, notes, alerts, LLM calls and evaluation runs are added by
their phases. JSON columns hold lists and dicts; timestamps are ISO 8601 strings.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from radar.audit import AuditEntry
from radar.config import Universe
from radar.materiality.engine import Decision
from radar.models import AgencyRating, CreditEvent, EvidenceSpan, RatingObservation, RawDocument

SCHEMA_VERSION = "7"
TABLES = frozenset(
    {
        "issuers", "issuer_aliases", "ratings", "documents", "events", "event_evidence",
        "audit_log", "rating_observations", "priority_decisions", "llm_calls", "llm_cache",
        "llm_statements", "event_enrichments",
    }
)  # fmt: skip

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issuers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    legal_entity TEXT,
    sector TEXT NOT NULL,
    country TEXT NOT NULL,
    sec_cik TEXT,
    rating_status TEXT NOT NULL,
    tags TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issuer_aliases (
    alias_key TEXT PRIMARY KEY,
    alias TEXT NOT NULL,
    issuer_id TEXT NOT NULL REFERENCES issuers(id)
);
CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issuer_id TEXT NOT NULL REFERENCES issuers(id),
    legal_entity TEXT,
    scope TEXT NOT NULL,
    agency TEXT NOT NULL,
    rating_type TEXT NOT NULL,
    rating TEXT NOT NULL,
    outlook TEXT,
    watch TEXT NOT NULL,
    as_of TEXT,
    as_of_raw TEXT,
    source_url TEXT NOT NULL,
    source_title TEXT,
    retrieved_at TEXT NOT NULL,
    verification_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    published_at TEXT,
    retrieved_at TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    raw_size_bytes INTEGER NOT NULL,
    normalizer_version TEXT NOT NULL,
    text TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    issuer_hint TEXT,
    extra TEXT NOT NULL,
    issuer_id TEXT,
    resolution TEXT,
    processed_at TEXT,
    outcome TEXT
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL,
    family TEXT NOT NULL,
    event_type TEXT NOT NULL,
    effective_date TEXT,
    fields TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    source_doc_ids TEXT NOT NULL,
    created_at TEXT NOT NULL,
    enrichment_method TEXT
);
CREATE TABLE IF NOT EXISTS event_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES events(event_id),
    doc_id TEXT NOT NULL,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    quote TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    match_score REAL NOT NULL,
    field TEXT
);
CREATE TABLE IF NOT EXISTS rating_observations (
    observation_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL,
    agency TEXT NOT NULL,
    rating TEXT NOT NULL,
    outlook TEXT,
    watch TEXT NOT NULL,
    rating_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    rating_date TEXT,
    observed_at TEXT NOT NULL,
    as_of_basis TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    evidence_span_id TEXT NOT NULL,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    quote TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    verification_method TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS priority_decisions (
    event_id TEXT PRIMARY KEY,
    issuer_id TEXT NOT NULL,
    effective_date TEXT,
    base_priority TEXT,
    final_priority TEXT,
    decision_status TEXT NOT NULL,
    negative INTEGER NOT NULL,
    triggered_rules TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    llm_role TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT,
    event_id TEXT,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    resolved_model TEXT,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    reasoning_effort TEXT,
    inputs_hash TEXT NOT NULL,
    outputs_hash TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    cached INTEGER NOT NULL,
    status TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_statements (
    statement_id TEXT PRIMARY KEY,
    llm_call_id INTEGER,
    doc_id TEXT NOT NULL,
    issuer_id TEXT NOT NULL,
    event_id TEXT,
    model_id TEXT NOT NULL,
    resolved_model TEXT,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    statement_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    change_json TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_enrichments (
    enrichment_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(event_id),
    enrichment_version TEXT NOT NULL,
    statement_ids TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    fields_before TEXT NOT NULL,
    fields_after TEXT NOT NULL,
    priority_before TEXT,
    priority_after TEXT,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    step TEXT NOT NULL,
    event_id TEXT,
    doc_id TEXT,
    inputs_hash TEXT,
    outputs_hash TEXT,
    model_id TEXT,
    prompt_version TEXT,
    rules_version TEXT,
    latency_ms INTEGER,
    cost_usd REAL,
    status TEXT NOT NULL,
    message TEXT
);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class Database:
    """Thin repository over one SQLite file. Every write runs in a transaction."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ----------------------------------------------------------- schema --- #

    def init_schema(self) -> None:
        with self.transaction() as c:
            c.executescript(SCHEMA)
            columns = {r["name"] for r in c.execute("PRAGMA table_info(events)")}
            if "enrichment_method" not in columns:  # schema 6 to 7
                c.execute("ALTER TABLE events ADD COLUMN enrichment_method TEXT")
            c.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )

    def schema_version(self) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        return row["value"] if row else None

    # ----------------------------------------------------------- issuers --- #

    def replace_universe(self, universe: Universe) -> int:
        """Load issuers and aliases from universe.yaml (full replacement, idempotent)."""
        with self.transaction() as c:
            c.execute("DELETE FROM issuer_aliases")
            c.execute("DELETE FROM issuers")
            for issuer in universe.issuers:
                c.execute(
                    "INSERT INTO issuers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        issuer.id,
                        issuer.name,
                        issuer.legal_entity,
                        issuer.sector,
                        issuer.country,
                        issuer.sec_cik,
                        issuer.rating_status,
                        _json(issuer.tags),
                    ),
                )
                for alias in [issuer.name, *issuer.aliases]:
                    c.execute(
                        "INSERT OR REPLACE INTO issuer_aliases VALUES (?, ?, ?)",
                        (alias.casefold(), alias, issuer.id),
                    )
        return len(universe.issuers)

    def issuer_ids(self) -> set[str]:
        return {r["id"] for r in self.conn.execute("SELECT id FROM issuers")}

    # ----------------------------------------------------------- ratings --- #

    def replace_ratings(self, ratings: Iterable[AgencyRating]) -> int:
        rows = list(ratings)
        with self.transaction() as c:
            c.execute("DELETE FROM ratings")
            for r in rows:
                c.execute(
                    """INSERT INTO ratings (issuer_id, legal_entity, scope, agency, rating_type,
                       rating, outlook, watch, as_of, as_of_raw, source_url, source_title,
                       retrieved_at, verification_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        r.issuer_id,
                        r.legal_entity,
                        r.scope,
                        r.agency,
                        r.rating_type,
                        r.rating,
                        r.outlook,
                        r.watch,
                        r.as_of.isoformat() if r.as_of else None,
                        r.as_of_raw,
                        str(r.source_url),
                        r.source_title,
                        r.retrieved_at.isoformat(),
                        r.verification_status,
                    ),
                )
        return len(rows)

    def ratings_for(self, issuer_id: str) -> list[AgencyRating]:
        rows = self.conn.execute("SELECT * FROM ratings WHERE issuer_id = ?", (issuer_id,))
        return [AgencyRating(**{k: r[k] for k in r.keys() if k != "id"}) for r in rows]

    # --------------------------------------------------------- documents --- #

    def insert_document(self, doc: RawDocument) -> bool:
        """Store a document. Returns False when the same raw bytes are already stored."""
        exists = self.conn.execute(
            "SELECT 1 FROM documents WHERE content_hash = ?", (doc.content_hash,)
        ).fetchone()
        if exists:
            return False
        with self.transaction() as c:
            c.execute(
                """INSERT INTO documents (doc_id, source_type, url, title, published_at,
                   retrieved_at, content_hash, raw_size_bytes, normalizer_version, text,
                   raw_path, issuer_hint, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc.doc_id,
                    doc.source_type,
                    str(doc.url),
                    doc.title,
                    _iso(doc.published_at),
                    doc.retrieved_at.isoformat(),
                    doc.content_hash,
                    doc.raw_size_bytes,
                    doc.normalizer_version,
                    doc.text,
                    doc.raw_path,
                    doc.issuer_hint,
                    _json(doc.extra),
                ),
            )
        return True

    def _row_to_document(self, r: sqlite3.Row) -> RawDocument:
        return RawDocument(
            doc_id=r["doc_id"],
            source_type=r["source_type"],
            url=r["url"],
            title=r["title"],
            published_at=r["published_at"],
            retrieved_at=r["retrieved_at"],
            content_hash=r["content_hash"],
            raw_size_bytes=r["raw_size_bytes"],
            normalizer_version=r["normalizer_version"],
            text=r["text"],
            raw_path=r["raw_path"],
            issuer_hint=r["issuer_hint"],
            extra=json.loads(r["extra"]),
        )

    def get_document(self, doc_id: str) -> RawDocument | None:
        row = self.conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        return self._row_to_document(row) if row else None

    def document_status(self, doc_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT doc_id, issuer_id, resolution, processed_at, outcome "
            "FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        return dict(row) if row else None

    def unprocessed_documents(self) -> list[RawDocument]:
        rows = self.conn.execute(
            "SELECT * FROM documents WHERE processed_at IS NULL ORDER BY retrieved_at, doc_id"
        )
        return [self._row_to_document(r) for r in rows]

    def mark_resolved(self, doc_id: str, issuer_id: str | None, resolution: str) -> None:
        with self.transaction() as c:
            c.execute(
                "UPDATE documents SET issuer_id = ?, resolution = ? WHERE doc_id = ?",
                (issuer_id, resolution, doc_id),
            )

    def mark_processed(self, doc_id: str, outcome: str | None = None) -> None:
        with self.transaction() as c:
            c.execute(
                "UPDATE documents SET processed_at = ?, outcome = ? WHERE doc_id = ?",
                (datetime.now(UTC).isoformat(), outcome, doc_id),
            )

    def list_document_status(self, issuer_id: str | None = None) -> list[dict[str, Any]]:
        where, params = "", []
        if issuer_id:
            where, params = "WHERE issuer_id = ?", [issuer_id]
        rows = self.conn.execute(
            "SELECT doc_id, source_type, title, url, published_at, retrieved_at, issuer_id, "
            f"resolution, outcome, extra FROM documents {where} ORDER BY retrieved_at, doc_id",
            params,
        )
        out = []
        for r in rows:
            d = dict(r)
            d["extra"] = json.loads(d["extra"])
            out.append(d)
        return out

    def count(self, table: str) -> int:
        if table not in TABLES:
            raise ValueError(table)
        return self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

    # ------------------------------------------------------------ events --- #

    def insert_event(self, event: CreditEvent) -> None:
        with self.transaction() as c:
            c.execute(
                """INSERT INTO events (event_id, issuer_id, family, event_type, effective_date,
                   fields, extraction_method, source_doc_ids, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id,
                    event.issuer_id,
                    event.family,
                    event.event_type,
                    event.effective_date.isoformat() if event.effective_date else None,
                    _json(event.fields),
                    event.extraction_method,
                    _json(event.source_doc_ids),
                    datetime.now(UTC).isoformat(),
                ),
            )
            for span in event.evidence:
                c.execute(
                    """INSERT INTO event_evidence (event_id, doc_id, char_start, char_end, quote,
                       evidence_type, extractor_version, match_score, field)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.event_id,
                        span.doc_id,
                        span.char_start,
                        span.char_end,
                        span.quote,
                        span.evidence_type,
                        span.extractor_version,
                        span.match_score,
                        span.field,
                    ),
                )

    def merge_event_sources(self, event_id: str, doc_ids: Iterable[str]) -> None:
        row = self.conn.execute(
            "SELECT source_doc_ids FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise KeyError(event_id)
        merged = sorted(set(json.loads(row["source_doc_ids"])) | set(doc_ids))
        with self.transaction() as c:
            c.execute(
                "UPDATE events SET source_doc_ids = ? WHERE event_id = ?", (_json(merged), event_id)
            )

    def _row_to_event(self, r: sqlite3.Row) -> CreditEvent:
        spans = self.conn.execute(
            "SELECT * FROM event_evidence WHERE event_id = ? ORDER BY id", (r["event_id"],)
        )
        return CreditEvent(
            event_id=r["event_id"],
            issuer_id=r["issuer_id"],
            family=r["family"],
            event_type=r["event_type"],
            effective_date=r["effective_date"],
            fields=json.loads(r["fields"]),
            evidence=[
                EvidenceSpan(**{k: s[k] for k in s.keys() if k not in ("id", "event_id")})
                for s in spans
            ],
            extraction_method=r["extraction_method"],
            source_doc_ids=json.loads(r["source_doc_ids"]),
            enrichment_method=r["enrichment_method"] if "enrichment_method" in r.keys() else None,
        )

    def list_events(self, issuer_id: str | None = None) -> list[CreditEvent]:
        if issuer_id:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE issuer_id = ? ORDER BY created_at, event_id",
                (issuer_id,),
            )
        else:
            rows = self.conn.execute("SELECT * FROM events ORDER BY created_at, event_id")
        return [self._row_to_event(r) for r in rows]

    def get_event(self, event_id: str) -> CreditEvent | None:
        row = self.conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    # ------------------------------------------------------ observations --- #

    def insert_observation(self, obs: RatingObservation) -> bool:
        exists = self.conn.execute(
            "SELECT 1 FROM rating_observations WHERE observation_id = ?", (obs.observation_id,)
        ).fetchone()
        if exists:
            return False
        with self.transaction() as c:
            c.execute(
                """INSERT INTO rating_observations VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    obs.observation_id,
                    obs.issuer_id,
                    obs.agency,
                    obs.rating,
                    obs.outlook,
                    obs.watch,
                    obs.rating_type,
                    obs.scope,
                    obs.rating_date.isoformat() if obs.rating_date else None,
                    obs.observed_at.isoformat(),
                    obs.as_of_basis,
                    obs.doc_id,
                    obs.evidence_span_id,
                    obs.evidence.char_start,
                    obs.evidence.char_end,
                    obs.evidence.quote,
                    obs.evidence.evidence_type,
                    obs.extractor_version,
                    obs.verification_method,
                ),
            )
        return True

    def observations_for(self, issuer_id: str) -> list[RatingObservation]:
        rows = self.conn.execute(
            "SELECT * FROM rating_observations WHERE issuer_id = ? "
            "ORDER BY observed_at, agency, observation_id",
            (issuer_id,),
        )
        out = []
        for r in rows:
            span = EvidenceSpan(
                doc_id=r["doc_id"],
                char_start=r["char_start"],
                char_end=r["char_end"],
                quote=r["quote"],
                evidence_type=r["evidence_type"],
                extractor_version=r["extractor_version"],
                match_score=100.0,
                field="rating_row",
            )
            out.append(
                RatingObservation(
                    observation_id=r["observation_id"],
                    issuer_id=r["issuer_id"],
                    agency=r["agency"],
                    rating=r["rating"],
                    outlook=r["outlook"],
                    watch=r["watch"],
                    rating_type=r["rating_type"],
                    scope=r["scope"],
                    rating_date=r["rating_date"],
                    observed_at=r["observed_at"],
                    as_of_basis=r["as_of_basis"],
                    doc_id=r["doc_id"],
                    evidence_span_id=r["evidence_span_id"],
                    evidence=span,
                    extractor_version=r["extractor_version"],
                    verification_method=r["verification_method"],
                )
            )
        return out

    # --------------------------------------------------------- decisions --- #

    def upsert_decision(self, decision: Decision) -> None:
        with self.transaction() as c:
            c.execute(
                """INSERT OR REPLACE INTO priority_decisions VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision.event_id,
                    decision.issuer_id,
                    decision.effective_date.isoformat() if decision.effective_date else None,
                    decision.base_priority,
                    decision.final_priority,
                    decision.decision_status,
                    int(decision.negative),
                    _json(decision.triggered_ids()),
                    decision.rules_version,
                    decision.provenance.llm_used,
                    _json(decision.model_dump(mode="json")),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get_decision(self, event_id: str) -> Decision | None:
        row = self.conn.execute(
            "SELECT decision_json FROM priority_decisions WHERE event_id = ?", (event_id,)
        ).fetchone()
        return Decision.model_validate(json.loads(row["decision_json"])) if row else None

    def decisions_for(self, issuer_id: str) -> list[Decision]:
        rows = self.conn.execute(
            "SELECT decision_json FROM priority_decisions WHERE issuer_id = ? "
            "ORDER BY effective_date, event_id",
            (issuer_id,),
        )
        return [Decision.model_validate(json.loads(r["decision_json"])) for r in rows]

    def priority_of(self, event_id: str) -> tuple[str | None, str] | None:
        row = self.conn.execute(
            "SELECT final_priority, decision_status FROM priority_decisions WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return (row["final_priority"], row["decision_status"]) if row else None

    # --------------------------------------------------------------- llm --- #

    def insert_llm_call(self, row: dict[str, Any]) -> int:
        columns = [
            "doc_id", "event_id", "provider", "model_id", "resolved_model", "prompt_version",
            "schema_version", "reasoning_effort", "inputs_hash", "outputs_hash", "input_tokens",
            "output_tokens", "cost_usd", "latency_ms", "cached", "status", "cache_key",
        ]  # fmt: skip
        values = [row.get(c) for c in columns] + [datetime.now(UTC).isoformat()]
        with self.transaction() as c:
            cursor = c.execute(
                f"INSERT INTO llm_calls ({', '.join(columns)}, created_at) VALUES "
                f"({', '.join('?' for _ in columns)}, ?)",
                values,
            )
            return int(cursor.lastrowid)

    # ------------------------------------------------------ enrichment --- #

    STATEMENT_COLUMNS = (
        "statement_id", "llm_call_id", "doc_id", "issuer_id", "event_id", "model_id",
        "resolved_model", "prompt_version", "schema_version", "statement_json",
        "validation_json", "change_json", "validation_status",
    )  # fmt: skip

    def insert_statements(self, rows: Iterable[dict[str, Any]]) -> int:
        """Validated or rejected LLM statements, keyed by a deterministic statement id.
        A replay with the same id keeps the first row (its real call is the provenance)."""
        n = 0
        with self.transaction() as c:
            for row in rows:
                values = [
                    _json(row[k])
                    if k.endswith("_json") and not isinstance(row[k], str)
                    else row.get(k)
                    for k in self.STATEMENT_COLUMNS
                ]
                cursor = c.execute(
                    f"INSERT OR IGNORE INTO llm_statements ({', '.join(self.STATEMENT_COLUMNS)}, "
                    f"created_at) VALUES ({', '.join('?' for _ in self.STATEMENT_COLUMNS)}, ?)",
                    values + [datetime.now(UTC).isoformat()],
                )
                n += cursor.rowcount
        return n

    def statements_for_document(self, doc_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM llm_statements WHERE doc_id = ? ORDER BY created_at, statement_id",
            (doc_id,),
        )
        out = []
        for r in rows:
            d = dict(r)
            for k in ("statement_json", "validation_json", "change_json"):
                d[k] = json.loads(d[k])
            out.append(d)
        return out

    def update_event_enrichment(
        self,
        event_id: str,
        fields: dict[str, Any],
        spans: Iterable[EvidenceSpan],
        enrichment_method: str | None,
    ) -> None:
        with self.transaction() as c:
            c.execute(
                "UPDATE events SET fields = ?, enrichment_method = ? WHERE event_id = ?",
                (_json(fields), enrichment_method, event_id),
            )
            c.execute(
                "DELETE FROM event_evidence WHERE event_id = ? AND evidence_type = 'llm_statement'",
                (event_id,),
            )
            for span in spans:
                c.execute(
                    """INSERT INTO event_evidence (event_id, doc_id, char_start, char_end, quote,
                       evidence_type, extractor_version, match_score, field)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_id,
                        span.doc_id,
                        span.char_start,
                        span.char_end,
                        span.quote,
                        span.evidence_type,
                        span.extractor_version,
                        span.match_score,
                        span.field,
                    ),  # fmt: skip
                )

    def insert_enrichment(self, row: dict[str, Any]) -> None:
        with self.transaction() as c:
            c.execute(
                """INSERT INTO event_enrichments (enrichment_id, event_id, enrichment_version,
                   statement_ids, payload_hash, fields_before, fields_after, priority_before,
                   priority_after, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["enrichment_id"],
                    row["event_id"],
                    row["enrichment_version"],
                    _json(row["statement_ids"]),
                    row["payload_hash"],
                    _json(row["fields_before"]),
                    _json(row["fields_after"]),
                    row.get("priority_before"),
                    row.get("priority_after"),
                    datetime.now(UTC).isoformat(),
                ),  # fmt: skip
            )

    def enrichment_exists(self, enrichment_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM event_enrichments WHERE enrichment_id = ?", (enrichment_id,)
        ).fetchone()
        return row is not None

    def enrichment_with_payload(self, event_id: str, payload_hash: str) -> str | None:
        row = self.conn.execute(
            "SELECT enrichment_id FROM event_enrichments WHERE event_id = ? AND payload_hash = ?",
            (event_id, payload_hash),
        ).fetchone()
        return row["enrichment_id"] if row else None

    def llm_cache_get(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT response_json FROM llm_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        return row["response_json"] if row else None

    def llm_cache_put(self, key: str, response_json: str, created_at: str) -> None:
        with self.transaction() as c:
            c.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?)",
                (key, response_json, created_at),
            )

    # ------------------------------------------------------------- audit --- #

    def audit(self, entry: AuditEntry) -> None:
        with self.transaction() as c:
            c.execute(
                """INSERT INTO audit_log (timestamp, step, event_id, doc_id, inputs_hash,
                   outputs_hash, model_id, prompt_version, rules_version, latency_ms, cost_usd,
                   status, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.timestamp.isoformat(),
                    entry.step,
                    entry.event_id,
                    entry.doc_id,
                    entry.inputs_hash,
                    entry.outputs_hash,
                    entry.model_id,
                    entry.prompt_version,
                    entry.rules_version,
                    entry.latency_ms,
                    entry.cost_usd,
                    entry.status,
                    entry.message,
                ),
            )

    def audit_entries(
        self, *, doc_id: str | None = None, event_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if doc_id:
            clauses.append("doc_id = ?")
            params.append(doc_id)
        if event_id:
            clauses.append("event_id = ?")
            params.append(event_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM audit_log {where} ORDER BY id", params)
        return [dict(r) for r in rows]
