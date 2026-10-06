"""Phase P2: the viewer's data layer and the static HTML export, on the real Harley P1
built through the CLI, offline. Streamlit screens are tested with its AppTest harness."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from radar.cli import app
from radar.config import CONFIG_DIR, load_rules, load_settings, load_universe
from radar.db import Database
from radar.viewer.data import (
    alert_rows,
    audit_rows,
    event_alert,
    passage_context,
    routing_rows,
    watchlist_rows,
)
from radar.viewer.export import export_site

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "edgar"
runner = CliRunner()


def run(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> dict:
    tmp = tmp_path_factory.mktemp("viewer")
    db = str(tmp / "radar.db")
    common = ("--db", db)
    run("init-db", *common)
    run("seed", *common)
    run(
        "ingest", "--since", "2026-01-01", "--source", "fixtures", "--fixtures-dir", str(FIXTURES),
        "--issuer", "HARLEY_DAVIDSON_INC", "--raw-dir", str(tmp / "raw"), *common,
    )  # fmt: skip
    run("process", *common)
    return {
        "db": db, "tmp": tmp, "universe": load_universe(CONFIG_DIR / "universe.yaml"),
        "rules": load_rules(CONFIG_DIR / "rules.yaml"),
        "settings": load_settings(CONFIG_DIR / "settings.yaml"),
    }  # fmt: skip


def test_watchlist_separates_the_live_universe_from_the_historical_cases(world, scales):
    db = Database(world["db"])
    rows = watchlist_rows(db, world["universe"], scales)
    db.close()
    live = [r for r in rows if r["universe"] == "live_watchlist"]
    hist = [r for r in rows if r["universe"] == "historical_stress_case"]
    assert {r["issuer_id"] for r in live} >= {"VOLKSWAGEN", "TRATON", "OMV"}
    assert not {r["issuer_id"] for r in live} & {r["issuer_id"] for r in hist}
    vw = next(r for r in live if r["issuer_id"] == "VOLKSWAGEN")
    assert vw["composite"] not in (None, "", "n/a") and vw["name"] == "Volkswagen"
    hog = next(r for r in hist if r["issuer_id"] == "HARLEY_DAVIDSON_INC")
    assert hog["events"] == 1 and hog["highest_priority"] == "P1" and hog["composite"] == "n/a"
    assert hog["last_event"] == "2026-07-08"


def test_alert_rows_put_the_p1_first_with_its_title_and_rules(world):
    db = Database(world["db"])
    rows = alert_rows(db, world["universe"])
    db.close()
    assert rows and rows[0]["priority"] == "P1" and rows[0]["issuer_name"] == "Harley-Davidson"
    assert rows[0]["title"].startswith(
        "S&P Global Ratings downgrade: BBB- → BB+, IG → HY (outlook stable"
    )
    assert rows[0]["rules"] == "RAT-01, RAT-02" and rows[0]["universe"] == "historical_stress_case"
    assert rows[0]["effective_date"] == "2026-07-08" and rows[0]["enrichment"] == "none"


def test_event_alert_and_passage_context_highlight_the_source(world):
    db = Database(world["db"])
    row = alert_rows(db, world["universe"])[0]
    alert, documents = event_alert(
        db, row["event_id"], world["universe"], world["rules"], world["settings"].alerts
    )
    fact = next(f for f in alert.facts if f.field is None)
    before, quote, after = passage_context(
        documents[fact.doc_id], fact.char_start, fact.char_end, window=120
    )
    entries = audit_rows(db, row["event_id"])
    db.close()
    assert (
        alert.priority == "P1"
        and quote == fact.text
        and 0 < len(before) <= 120
        and 0 < len(after) <= 120
    )
    assert "BBB- to BB+" in quote
    assert [e["step"] for e in entries] == ["extract", "materiality"]


def test_routing_rows_come_from_the_settings_with_their_reasons(world):
    rows = routing_rows(world["settings"])
    assert [r["family"] for r in rows] == ["guidance", "liquidity", "covenant", "going_concern"]
    assert rows[2]["default"] == "gpt-5.6-sol" and rows[3]["challenger"] == "none"
    assert all(r["reason"] for r in rows)


def test_export_site_writes_an_index_and_one_page_per_decided_event(world):
    db = Database(world["db"])
    out = world["tmp"] / "site"
    paths = export_site(db, world["universe"], world["rules"], world["settings"], out)
    db.close()
    index = (out / "index.html").read_text()
    assert "Live watchlist" in index and "Historical stress cases" in index
    assert "Harley-Davidson" in index and "P1" in index and 'href="alerts/' in index
    pages = list((out / "alerts").glob("*.html"))
    assert len(pages) == 1 and "Why this priority?" in pages[0].read_text()
    assert out / "index.html" in paths and "<script" not in index


def test_streamlit_screens_render_the_dashboard_and_the_event_detail(world):
    from streamlit.testing.v1 import AppTest

    app_path = Path(__file__).resolve().parents[1] / "src" / "radar" / "viewer" / "app.py"
    os.environ["RADAR_DB"] = world["db"]
    try:
        at = AppTest.from_file(str(app_path), default_timeout=60).run()
        assert not at.exception
        text = " ".join(t.value for t in at.title) + " ".join(m.value for m in at.markdown)
        assert "Credit Event Radar" in text
        assert any("Historical stress cases" in m.value for m in at.markdown)
        db = Database(world["db"])
        event_id = alert_rows(db, world["universe"])[0]["event_id"]
        db.close()
        at.query_params["event"] = event_id
        at.run()
        assert not at.exception
        text = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.code)
        assert "Why this priority?" in text and "RAT-01" in text and "RAT-02" in text
        assert "Composite rating used: NO" in text and "BBB- to BB+" in text
    finally:
        os.environ.pop("RADAR_DB", None)


def test_viewer_command_launches_streamlit_on_the_app(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda argv, **kw: calls.append((argv, kw)) or type("R", (), {"returncode": 0})(),
    )
    out = run("viewer", "--db", str(tmp_path / "x.db"), "--port", "8765")
    argv, kw = calls[0]
    assert "streamlit" in argv and "run" in argv and argv[-1].endswith("8765") or "8765" in argv
    assert any(str(a).endswith("viewer/app.py") for a in argv)
    assert kw["env"]["RADAR_DB"].endswith("x.db") and "streamlit" in out.lower()
