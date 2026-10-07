"""The public demo bundle: consistent with the committed manifests, restorable with hash
checks and explicit failures, and the build it feeds yields the four featured cases."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from radar.config import ROOT, load_universe
from radar.connectors.fixture import fixture_bytes_available
from radar.db import Database
from radar.demo import FEATURED, load_bundle, restore_documents, verify_featured

HARLEY = "tests/fixtures/edgar/harley_davidson_inc_10q_2026q2"


def test_bundle_is_public_and_consistent_with_the_manifests():
    bundle = load_bundle()
    assert len(bundle.documents) == 5
    issuers = {d["issuer_id"] for d in bundle.documents}
    assert set(FEATURED) <= issuers
    for d in bundle.documents:
        manifest = json.loads((ROOT / d["fixture"] / "manifest.json").read_text())
        assert manifest["raw_sha256"] == d["raw_sha256"]
        assert manifest["normalized_sha256"] == d["normalized_sha256"]
        assert d["source_url"].startswith("https://")
    doc_ids = {d["normalized_sha256"] for d in bundle.documents}
    assert bundle.statements and all(s["doc_id"] in doc_ids for s in bundle.statements)
    for s in bundle.statements:
        assert s["validation_status"] == "VALID"
        assert "evidence_quote" not in s["statement_json"]  # passages are rebuilt, hashed
        assert len(s["quote_sha256"]) == 64 and s["quote_end"] > s["quote_start"]
        assert s["model_selection_json"]["reason"]
    kinds = {s["statement_kind"] for s in bundle.statements}
    assert {"guidance", "liquidity", "going_concern"} <= kinds
    assert any(
        s["issuer_id"] == "OMV" and s["statement_kind"] == "going_concern"
        and s["statement_json"]["status"] == "negated"
        for s in bundle.statements
    )  # fmt: skip


def _bundle_root(tmp_path: Path) -> Path:
    """A fixtures root with every manifest and only Harley's public bytes."""
    root = tmp_path / "root"
    for d in load_bundle().documents:
        src, dst = ROOT / d["fixture"], root / d["fixture"]
        dst.mkdir(parents=True)
        shutil.copy(src / "manifest.json", dst / "manifest.json")
    shutil.copy(ROOT / HARLEY / "document.htm.gz", root / HARLEY / "document.htm.gz")
    return root


def test_restore_keeps_valid_local_bytes_and_names_what_is_missing_offline(tmp_path: Path):
    root = _bundle_root(tmp_path)
    report = restore_documents(load_bundle(), root=root, universe=load_universe(), offline=True)
    assert len(report.kept) == 1 and "Harley" in report.kept[0]
    assert len(report.failed) == 4 and all("offline" in reason for _, reason in report.failed)
    assert not report.ok


def test_restore_accepts_changed_raw_bytes_when_the_normalised_text_is_identical(tmp_path: Path):
    root = _bundle_root(tmp_path)
    bundle = load_bundle()
    harley = next(d for d in bundle.documents if "harley" in d["fixture"])
    original = gzip.open(ROOT / HARLEY / "document.htm.gz").read()
    (root / HARLEY / "document.htm.gz").unlink()  # force a fetch of Harley
    altered = original.replace(b"</body>", b'<script src="/changing-token"></script></body>')
    assert hashlib.sha256(altered).hexdigest() != harley["raw_sha256"]
    calls = []

    def fetch(url, agent):
        calls.append((url, agent))
        if url == harley["source_url"]:
            return altered
        raise OSError("no network in tests")

    report = restore_documents(bundle, root=root, universe=load_universe(), fetch=fetch, agent="t")
    assert any("Harley" in r for r in report.restored) and any(
        "Harley" in r for r in report.raw_mismatch
    )
    assert (root / HARLEY / "document.htm.gz").exists()
    assert len(report.failed) == 4 and all("fetch failed" in reason for _, reason in report.failed)
    assert all(agent == "t" for _, agent in calls)


def test_restore_rejects_a_document_that_changed_at_the_source(tmp_path: Path):
    root = _bundle_root(tmp_path)
    bundle = load_bundle()
    harley = next(d for d in bundle.documents if "harley" in d["fixture"])
    (root / HARLEY / "document.htm.gz").unlink()
    report = restore_documents(
        bundle, root=root, universe=load_universe(), agent="t",
        fetch=lambda url, agent: b"<html><body>changed filing</body></html>",
    )  # fmt: skip
    assert any(
        "Harley" in label and "changed at the source" in reason for label, reason in report.failed
    )
    assert not (root / HARLEY / "document.htm.gz").exists()
    assert harley["normalized_sha256"] not in report.restored


def _bytes_present() -> bool:
    return all(fixture_bytes_available(ROOT / d["fixture"]) for d in load_bundle().documents)


@pytest.mark.skipif(
    not (os.environ.get("RADAR_DEMO_BUILD_TESTS") and _bytes_present()),
    reason="set RADAR_DEMO_BUILD_TESTS=1 with the public bytes in place (slow, offline build)",
)
def test_offline_build_from_the_bundle_yields_the_featured_cases(tmp_path: Path):
    db = tmp_path / "demo.db"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_demo_db.py"),
            "--fresh",
            "--offline",
            "--db",
            str(db),
            "--raw-dir",
            str(tmp_path / "raw"),
            "--alerts-out",
            str(tmp_path / "alerts"),
            "--site-out",
            str(tmp_path / "site"),
        ],  # fmt: skip
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "featured cases verified" in result.stdout
    database = Database(db)
    assert verify_featured(database, load_bundle()) == []
    database.close()
    assert (tmp_path / "site" / "index.html").exists()


@pytest.mark.skipif(
    not os.environ.get("RADAR_NETWORK_TESTS"),
    reason="set RADAR_NETWORK_TESTS=1 to fetch the five public documents from their official URLs",
)
def test_clean_room_build_fetches_the_public_documents_and_yields_the_featured_cases(
    tmp_path: Path,
):
    root = _bundle_root(tmp_path)
    db = tmp_path / "demo.db"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_demo_db.py"),
            "--fresh",
            "--fixtures-root",
            str(root),
            "--db",
            str(db),
            "--raw-dir",
            str(tmp_path / "raw"),
            "--alerts-out",
            str(tmp_path / "alerts"),
            "--site-out",
            str(tmp_path / "site"),
        ],  # fmt: skip
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert result.stdout.count("restored") >= 4 and "featured cases verified" in result.stdout
    database = Database(db)
    assert verify_featured(database, load_bundle()) == []
    database.close()


def test_edgar_documents_are_not_fetched_without_a_declared_contact(tmp_path: Path, monkeypatch):
    """The SEC refuses anonymous agents: the build names the missing setting instead of
    trying, while the issuers' own sites are fetched with the project's identity."""
    from radar.demo import DEFAULT_USER_AGENT, sec_user_agent

    monkeypatch.setenv("SEC_USER_AGENT", "Credit Event Radar contact@example.com")
    assert sec_user_agent() is None
    monkeypatch.setenv("SEC_USER_AGENT", "Credit Event Radar someone@real-domain.org")
    assert sec_user_agent() == "Credit Event Radar someone@real-domain.org"
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    root = _bundle_root(tmp_path)
    (root / HARLEY / "document.htm.gz").unlink()
    fetched = []

    def fetch(url, agent):
        fetched.append(url)
        raise OSError("no network in tests")

    report = restore_documents(load_bundle(), root=root, universe=load_universe(), fetch=fetch)
    sec = [(label, reason) for label, reason in report.failed if "SEC_USER_AGENT" in reason]
    assert len(sec) == 3 and all("sec.gov" in r for _, r in sec)
    assert not any("sec.gov" in url for url in fetched)
    assert any("omv.com" in url for url in fetched) and any("volkswagen" in url for url in fetched)
    assert DEFAULT_USER_AGENT.startswith("Credit Event Radar demo")
