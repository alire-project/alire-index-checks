"""
Functions that can be reused by the Python checks.

All of them operate on an index checkout whose HEAD is the PR merge commit
and HEAD~1 the base commit, as in CI.
"""

import os
import re
import subprocess
import tomllib
from typing import Any

BASE = "HEAD~1"
HEAD = "HEAD"

MANIFEST_RE = re.compile(
    r"^index/(?P<shelf>[a-z0-9_]{2})/(?P<crate>[a-z0-9_]+)/"
    r"(?P<file_crate>[a-z0-9_]+)-(?P<version>[0-9A-Za-z.+-]+)\.toml$")

# Version part of the file name of external manifests
EXTERNAL = "external"


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True,
                          capture_output=True).stdout


def changed_files() -> list[tuple[str, str]]:
    """Return (status, path) pairs changed by the PR. Renames are reported
    as a deletion plus an addition."""
    fields = git("diff", "--name-status", "--no-renames", "-z",
                 BASE, HEAD).decode().split("\0")
    fields = [f for f in fields if f]
    return list(zip(fields[0::2], fields[1::2]))


def load(rev: str, path: str) -> dict[str, Any]:
    """Parse the manifest at `path` in revision `rev`. Raises ValueError on
    invalid TOML."""
    try:
        return tomllib.loads(git("show", f"{rev}:{path}").decode())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"invalid TOML in {rev}:{path}: {e}") from e


def report(problems: list[str], success: str, failure: str) -> int:
    """Print the outcome of a check and return its exit status. With DRY_RUN
    set, problems are reported but the check passes."""
    if not problems:
        print(f"SUCCESS: {success}")
        return 0

    print()
    print("Problems found:")
    for problem in problems:
        print(f"   - {problem}")
    print(f"FAILED: {failure}")

    if "DRY_RUN" in os.environ:
        print("DRY_RUN set, continuing")
        return 0
    return 1
