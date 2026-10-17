#!/usr/bin/env python3
"""
Classify the files changed by a PR to an Alire index.

Usage:
    check-changes.py [ignored]

Must run in an index checkout whose HEAD is the PR merge commit and HEAD~1
the base commit, as in CI. Accepted changes are:

- Added manifests at index/<shelf>/<crate>/<crate>-<version>.toml
- Withdrawals: modified manifests that are identical to the base version
  once parsed, except for the top-level `available = false`
- Formatting/comment-only modifications of libhello manifests, which are used
  as a trigger for index CI tests

Several crates may be changed at once (e.g., releases from a monorepo); who
may change each crate is decided by the authorship check. Anything else
(deletions, renames, other modifications, symlinks, files outside index/,
index/index.toml, ...) requires manual review and makes the check fail,
listing every problem.

Environment:
    ALL_CRATES, DRY_RUN: see "Mirror workflows" in README.md

Exit status:
    0  only accepted changes (or DRY_RUN set)
    1  some change requires manual review

Dependencies: tomllib (Python >= 3.11)
"""

import os
import sys
from typing import Any

from common import BASE, HEAD, MANIFEST_RE, changed_files, git, load, report

# Crate whose manifests may receive formatting-only changes to test index CI
TEST_CRATE = "libhello"

SYMLINK_MODE = "120000"


def is_symlink(path: str) -> bool:
    entry = git("ls-tree", HEAD, "--", path).decode()
    return entry.split(" ", 1)[0] == SYMLINK_MODE


def without_available(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of `manifest` without its top-level `available`
    key, so that a withdrawal can be detected by comparing the remainder of
    the base and PR versions. Nested `available` keys (e.g. inside a table)
    are kept, as they are not a withdrawal of the release."""
    return {k: v for k, v in manifest.items() if k != "available"}


def classify_modification(path: str, crate: str) -> str | None:
    """Return a description of an accepted modification, or None if it
    requires review."""
    old = load(BASE, path)
    new = load(HEAD, path)

    if (new.get("available") is False
            and old.get("available") is not False
            and without_available(new) == without_available(old)):
        return "withdrawal (available = false)"

    if crate == TEST_CRATE and new == old:
        return f"formatting-only {TEST_CRATE} modification"

    return None


def check() -> list[str]:
    """Classify every change and return the problems found."""
    problems = []

    for status, path in changed_files():
        print(f"{status} {path}")

        if path == "index/index.toml":
            problems.append(f"index metadata changed: {path}")
            continue
        if not path.startswith("index/"):
            problems.append(f"file outside index/ changed: {path}")
            continue

        match = MANIFEST_RE.match(path)
        if not match:
            problems.append(f"not a release manifest path: {path}")
            continue

        crate = match["crate"]
        if (match["file_crate"] != crate
                or match["shelf"] != crate[:2]):
            problems.append(f"manifest in wrong location: {path}")
            continue

        try:
            if status == "A":
                if is_symlink(path):
                    problems.append(f"added manifest is a symlink: {path}")
                else:
                    load(HEAD, path)
            elif status == "M":
                accepted = classify_modification(path, crate)
                if accepted:
                    print(f"   {accepted}: {path}")
                else:
                    problems.append(f"existing manifest modified: {path}")
            elif status == "D":
                problems.append(f"manifest deleted: {path}")
            else:
                problems.append(f"unexpected change ({status}): {path}")
        except ValueError as e:
            problems.append(str(e))

    return problems


def main() -> int:
    return report(
        check(),
        success="PR only adds or withdraws releases",
        failure="PR contains changes other than new releases or withdrawals")


if __name__ == "__main__":
    sys.exit(main())
