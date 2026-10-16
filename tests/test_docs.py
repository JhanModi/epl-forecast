"""Documentation must not cite evidence that does not exist."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
REFERENCE = re.compile(r"(tests/[\w/]+\.py)::(\w+)")
PATH = re.compile(r"`((?:src|scripts|tests|frontend|docs|data)/[\w./-]+\.\w+)`")


def test_cited_tests_exist() -> None:
    missing = [
        f"{doc.name}: {path}::{name}"
        for doc in DOCS
        for path, name in REFERENCE.findall(doc.read_text(encoding="utf-8"))
        if not (ROOT / path).exists() or f"def {name}(" not in (ROOT / path).read_text(encoding="utf-8")
    ]
    assert not missing, missing


def test_cited_files_exist() -> None:
    current = [doc for doc in DOCS if "review" not in doc.parts]  # local-only notes with historical paths
    missing = [
        f"{doc.name}: {path}"
        for doc in current
        for path in PATH.findall(doc.read_text(encoding="utf-8"))
        if "<" not in path and not (ROOT / path).exists()
    ]
    assert not missing, missing
