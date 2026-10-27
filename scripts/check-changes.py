#!/usr/bin/env python3
"""
Classify the files changed by a PR to an Alire index.

Usage:
    check-changes.py [ignored]

Must run in an index checkout whose HEAD is the PR merge commit and HEAD~1
the base commit, as in CI. Accepted changes are:

- Added manifests at index/<shelf>/<crate>/<crate>-<version>.toml, of at
  most MAX_MANIFEST_SIZE bytes, nested at most MAX_NESTING_DEPTH levels,
  without pins, with http(s) or git origins (ORIGIN_URL_RE), and with at
  most MAX_VERSION_SUFFIX_LENGTH characters after X.Y.Z in their version
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
import re
import sys
from typing import Any

from common import (BASE, EXTERNAL, HEAD, MANIFEST_RE, changed_files, git,
                    is_withdrawal, load, report, version_suffix_problems)

# Crate whose manifests may receive formatting-only changes to test index CI
TEST_CRATE = "libhello"

SYMLINK_MODE = "120000"

# Maximum size in bytes of an added manifest, to keep the index lean (the
# largest one is under 7 KiB). alr's own limit (setting
# index.max_manifest_size) is more generous, so that this one can change
# without breaking released alr versions.
MAX_MANIFEST_SIZE = 16 * 1024

# Maximum nesting of tables and arrays in an added manifest. alr's TOML
# parser and the code walking the result are recursive, and exhaust the
# stack at a few thousand levels (Python's tomllib, at a few hundred). Real
# manifests have at most 4 levels plus 2 per nested case(...), 7 so far; the
# limit leaves room for casing on many variables (e.g., environment ones).
MAX_NESTING_DEPTH = 128


def is_symlink(path: str) -> bool:
    entry = git("ls-tree", HEAD, "--", path).decode()
    return entry.split(" ", 1)[0] == SYMLINK_MODE


def classify_modification(path: str, crate: str) -> str | None:
    """Return a description of an accepted modification, or None if it
    requires review."""
    if is_withdrawal(path):
        return "withdrawal (available = false)"

    if crate == TEST_CRATE and load(BASE, path) == load(HEAD, path):
        return f"formatting-only {TEST_CRATE} modification"

    return None


def manifest_size_problems(path: str) -> list[str]:
    """Return the problems with the size of the manifest at `path`."""
    size = int(git("cat-file", "-s", f"{HEAD}:{path}"))
    if size > MAX_MANIFEST_SIZE:
        return [f"manifest {path} is larger than {MAX_MANIFEST_SIZE} bytes "
                f"({size} bytes)"]
    return []


def nesting_depth(value: Any) -> int:
    """Return the levels of tables and arrays in `value`, without recursion
    (dotted keys build arbitrarily deep tables without upsetting tomllib)."""
    deepest = 0
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        deepest = max(deepest, depth)
        if isinstance(item, dict):
            pending += [(child, depth + 1) for child in item.values()]
        elif isinstance(item, list):
            pending += [(child, depth + 1) for child in item]
    return deepest


def nesting_problems(manifest: dict[str, Any], path: str) -> list[str]:
    """Return the problems with the nesting depth of `manifest`."""
    depth = nesting_depth(manifest)
    if depth > MAX_NESTING_DEPTH:
        return [f"manifest {path} is nested deeper than {MAX_NESTING_DEPTH} "
                f"levels ({depth} levels)"]
    return []


def pins_problems(manifest: dict[str, Any], path: str) -> list[str]:
    """Return the problems with pins in `manifest`. Pins only apply to the
    root crate of a workspace, so they mean nothing in an index; a few old
    manifests have them (even empty) as leftovers, which new ones must not."""
    if "pins" in manifest:
        return [f"manifest {path} has pins, which are not allowed in the "
                f"community index (not even an empty [[pins]] table)"]
    return []


# Origin URLs allowed in new manifests: http(s) and git, optionally with an
# explicit VCS prefix. This leaves out local origins (meaningless in a shared
# index), ssh ones (which need the user's keys, also git@host:path) and svn
# ones (whose revisions, unlike commits and archive hashes, do not pin content).
# Mirrors are not concerned: they must match the origin's commit or hashes.
ORIGIN_URL_RE = re.compile(r"^((git|hg)\+)?(https?|git)://")


def origin_urls(value: Any) -> list[str]:
    """Return the URLs in an origin table, including those in every case of
    case(...) expressions."""
    if isinstance(value, dict):
        return [url for key, child in value.items()
                for url in ([child] if key == "url" and isinstance(child, str)
                            else origin_urls(child))]
    return []


def origin_problems(manifest: dict[str, Any], path: str) -> list[str]:
    """Return the problems with the origin URLs of `manifest`."""
    return [f"origin URL {url} in {path} is not allowed in the community "
            f"index: only http(s) and git URLs are (no local, ssh or svn "
            f"origins)"
            for url in origin_urls(manifest.get("origin", {}))
            if not ORIGIN_URL_RE.match(url)]


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
                    problems += manifest_size_problems(path)
                    manifest = load(HEAD, path)
                    problems += nesting_problems(manifest, path)
                    problems += pins_problems(manifest, path)
                    problems += origin_problems(manifest, path)
                    if match["version"] != EXTERNAL:
                        problems += version_suffix_problems(
                            match["version"], f"file name {path}")
                        problems += version_suffix_problems(
                            str(manifest.get("version", "")), path)
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
