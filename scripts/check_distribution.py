"""Fail if restricted data could enter a distributable artifact.

Checks the files Git would track (tracked plus untracked-but-not-ignored) and,
with --docker, the real Docker build context, against the datasets registry:
nothing from a dataset that is not redistributable may appear, and neither may
any database file or local-only material (environment files, session notes,
agent settings, the v0 archive, personal spreadsheets).

    uv run python scripts/check_distribution.py            # Git only
    uv run python scripts/check_distribution.py --docker   # Git + Docker build context
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from epl.data.datasets import DATASETS

ROOT = Path(__file__).resolve().parents[1]

# A throwaway image that copies the build context exactly as `docker build .` sees it, then lists it.
CONTEXT_PROBE = """FROM busybox:1.36
COPY . /ctx
RUN cd /ctx && find . -type f > /files.txt
CMD ["cat", "/files.txt"]
"""

# Local working material that is never distributed, whatever the ignore files say.
LOCAL_ONLY_FILES = {"MEMORY.md", "SESSION_HANDOFF.md"}
LOCAL_ONLY_DIRS = (".claude/", "archive/", "venv/", ".venv/", "docs/review/")


def forbidden(path: str) -> str | None:
    """The reason a path may not be distributed, or None if it may."""
    normalised = path.replace("\\", "/").removeprefix("./")
    name = normalised.rsplit("/", 1)[-1]
    if normalised.endswith((".db", ".db-wal", ".db-shm", ".db-journal")):
        return "database file"
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "environment file (may hold secrets)"
    if normalised in LOCAL_ONLY_FILES or normalised.startswith(LOCAL_ONLY_DIRS) or name.endswith(".xlsx"):
        return "local-only material"
    for info in DATASETS.values():
        if info.redistributable:
            continue
        if normalised.startswith((f"data/{info.name}/", f"data/artifacts/{info.name}/")):
            return f"restricted dataset '{info.name}'"
    return None


def git_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.splitlines()


def docker_context_files() -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        dockerfile = Path(tmp) / "Dockerfile.probe"
        dockerfile.write_text(CONTEXT_PROBE, encoding="utf-8", newline="\n")
        tag = "epl-context-probe:latest"
        subprocess.run(
            ["docker", "build", "-q", "-f", str(dockerfile), "-t", tag, "."],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        out = subprocess.run(["docker", "run", "--rm", tag], capture_output=True, text=True, check=True)
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, check=False)
    return out.stdout.splitlines()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docker", action="store_true", help="also inspect the Docker build context")
    args = parser.parse_args()

    sources = {"git": git_files()}
    if args.docker:
        sources["docker build context"] = docker_context_files()

    problems = [
        f"{where}: {path} ({reason})"
        for where, files in sources.items()
        for path in files
        if (reason := forbidden(path))
    ]
    for where, files in sources.items():
        print(f"{where}: {len(files)} files checked")
    if problems:
        print("Restricted material would be distributed:", *problems, sep="\n  ")
        return 1
    print("OK: no restricted data or database files in distributable artifacts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
