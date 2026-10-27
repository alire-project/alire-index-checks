#!/usr/bin/env python3
"""
Report whether a PR to an Alire index may be merged automatically. The rules
are in automerge.py, which the auto-merge bot uses directly.

Usage:
    check-automerge.py <PR author GitHub login>

Must run in an index checkout whose HEAD is the PR merge commit and HEAD~1
the base commit, as in CI.

With ALL_CRATES set, the PR is ignored, and every release in the index is
compared instead with the release before it (the author is not checked).
This replays the history of the index, to see which releases would have been
eligible and why the others would not.

Environment:
    ALL_CRATES, DRY_RUN: see "Mirror workflows" in README.md

Exit status:
    0  the PR is eligible (or DRY_RUN set)
    1  the PR needs a manual review
    2  usage error

Dependencies: tomllib (Python >= 3.11), semver (in requirements.txt)
"""

import os
import sys
from collections import Counter

from semver import Version

from common import EXTERNAL, HEAD, MANIFEST_RE, git, load, report
from automerge import change_reasons, changed_properties, evaluate


def replay() -> int:
    """Compare every release in HEAD with the one before it, by semver
    precedence, and report those that would not have been eligible."""
    crates: dict[str, list[tuple[Version, str]]] = {}
    unordered = 0
    paths = git("ls-tree", "-r", "-z", "--name-only", HEAD, "--",
                "index/").decode().split("\0")
    for path in paths:
        match = MANIFEST_RE.match(path)
        if not match or match["version"] == EXTERNAL:
            continue
        try:
            version = Version.parse(match["version"])
        except ValueError:
            unordered += 1
            continue
        crates.setdefault(match["crate"], []).append((version, path))

    problems = []
    changes: Counter[str] = Counter()
    compared = 0
    ineligible = 0
    for crate, releases in sorted(crates.items()):
        releases.sort()
        for (old_version, old_path), (_, new_path) in zip(releases,
                                                          releases[1:]):
            compared += 1
            try:
                old = load(HEAD, old_path)
                new = load(HEAD, new_path)
            except ValueError as e:
                problems.append(f"{new_path}: {e}")
                ineligible += 1
                continue
            changed = changed_properties(old, new)
            changes.update(changed)
            ineligible += bool(changed)
            problems += [f"{new_path}: {reason}" for reason in
                         change_reasons(crate, old, new, str(old_version))]

    print(f"Releases compared with the previous one: {compared}")
    print(f"Would have been eligible: {compared - ineligible}")
    print(f"Releases skipped, as their version is not strict: {unordered}")
    print("Releases changing each property:")
    for key, count in changes.most_common():
        print(f"   {count:5} {key}")

    return report(problems,
                  success="every release would have been eligible",
                  failure="some releases would have needed a manual review")


def main() -> int:
    if "ALL_CRATES" in os.environ:
        return replay()

    if len(sys.argv) != 2 or not sys.argv[1]:
        print("Usage: check-automerge.py <PR author GitHub login>",
              file=sys.stderr)
        return 2

    author = sys.argv[1]
    print(f"PR author: {author}")

    result = evaluate(author)
    for crate, version in result.releases:
        print(f"   {crate}={version}")

    return report(
        result.reasons,
        success=f"PR is eligible for auto-merge ({result.kind})",
        failure="PR needs a manual review")


if __name__ == "__main__":
    sys.exit(main())
