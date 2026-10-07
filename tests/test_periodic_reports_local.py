"""Local guard on the real periodic reports (private fixture bytes, skipped when absent):
the generic risk factors of a 10-Q must not raise a priority by themselves, and a stated
doubt must (ADR-024). Nothing here calls a model."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from radar.cli import app
from radar.connectors.fixture import fixture_bytes_available
from radar.db import Database

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "edgar"
QUIET = {
    "DEERE": "deere_10q_2026q3",
    "PACCAR": "paccar_10q_2026q2",
    "GENERAL_MOTORS": "general_motors_10q_2026q2",
    "COMPASS_DIVERSIFIED": "compass_diversified_10q_2026q2",
    "CATERPILLAR": "caterpillar_10q_2026q2",
}
DOUBT = {"HYDROFARM": "hydrofarm_10q_2026q2", "GOPRO_INC": "gopro_10q_2026q1"}
runner = CliRunner()

pytestmark = pytest.mark.skipif(
    not all(fixture_bytes_available(FIXTURES / d) for d in [*QUIET.values(), *DOUBT.values()]),
    reason="private fixture bytes not available locally",
)


@pytest.fixture(scope="module")
def db_path(tmp_path_factory) -> str:
    tmp = tmp_path_factory.mktemp("reports")
    db = str(tmp / "radar.db")
    common = ("--db", db)
    for args in (("init-db",), ("seed",)):
        assert runner.invoke(app, [*args, *common]).exit_code == 0
    result = runner.invoke(
        app,
        [
            "ingest",
            "--since",
            "2024-01-01",
            "--source",
            "fixtures",
            "--fixtures-dir",
            str(FIXTURES),
            "--raw-dir",
            str(tmp / "raw"),
            *common,
            *[x for issuer in [*QUIET, *DOUBT] for x in ("--issuer", issuer)],
        ],  # fmt: skip
    )
    assert result.exit_code == 0, result.output
    assert runner.invoke(app, ["process", *common]).exit_code == 0
    return db


def test_generic_risk_factors_raise_no_priority_on_quiet_reports(db_path):
    db = Database(db_path)
    try:
        for issuer in QUIET:
            events = [e for e in db.list_events(issuer) if e.family == "earnings"]
            assert len(events) == 1, issuer
            event = events[0]
            assert event.fields["flags"] == [], (issuer, event.fields["flags"])
            assert db.priority_of(event.event_id) == (None, "NO_APPLICABLE_RULE"), issuer
            deferred = [
                e
                for e in db.audit_entries(doc_id=event.source_doc_ids[0])
                if e["step"] == "extract"
            ]
            assert deferred, issuer
    finally:
        db.close()


def test_a_stated_doubt_is_a_p1_without_any_model(db_path):
    db = Database(db_path)
    try:
        for issuer in DOUBT:
            event = next(e for e in db.list_events(issuer) if e.family == "earnings")
            assert event.fields["flags"] == ["going_concern"], issuer
            assert db.priority_of(event.event_id) == ("P1", "DECIDED"), issuer
            doubt = next(s for s in event.evidence if s.field == "flag:going_concern")
            assert "going concern" in doubt.quote
    finally:
        db.close()
