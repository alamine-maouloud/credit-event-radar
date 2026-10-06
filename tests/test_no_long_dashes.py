"""Project rule: no em dash or en dash anywhere in tracked text files."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".csv", ".toml", ".txt", ".jsonl", ".json", ".html"}
# eval/gold holds verbatim quotes of source documents (gold labels): their punctuation is the
# source's, not ours, so the style rule does not apply there.
SKIP_DIRS = {
    ".git",
    ".venv",
    "data/raw",
    "outputs",
    ".pytest_cache",
    ".ruff_cache",
    "eval/gold",
    "tests/fixtures",
    "eval/runs",  # model outputs quote the documents verbatim
}
FORBIDDEN = {chr(0x2014): "em dash", chr(0x2013): "en dash"}


def _tracked_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in EXTENSIONS:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS):
            continue
        yield path


def test_no_long_dashes():
    offenders = []
    for path in _tracked_files():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for char, name in FORBIDDEN.items():
                if char in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{n}: {name}")
    assert not offenders, "\n".join(offenders)
