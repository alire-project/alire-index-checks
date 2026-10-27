#!/usr/bin/env python3
"""
Check that the PR author may change every crate touched by the PR.

Usage:
    check-author.py <PR author GitHub login>

Must run in an index checkout whose HEAD is the PR merge commit and HEAD~1
the base commit, as in CI.

Authority over a crate is taken exclusively from the base branch, never from
the files submitted in the PR: the author must be in `maintainers-logins` of
the latest release of the crate in the base branch or, for crates without
releases, of its external manifest. Crates with no manifest at all in the base
branch are new and require manual review, so they are skipped here.

Every change to an existing crate (new releases, externals, withdrawals) is
subject to this check. Which kinds of changes are acceptable at all is decided
by check-changes.py.

Environment:
    ALL_CRATES, DRY_RUN: see "Mirror workflows" in README.md

Exit status:
    0  the author may change every touched crate (or DRY_RUN set)
    1  some crate cannot be changed by the author
    2  usage error

Dependencies: tomllib (Python >= 3.11), semver (in requirements.txt)
"""

import os
import sys

from common import BASE, MANIFEST_RE, authority, changed_files, load, report


def check(author: str) -> list[str]:
    """Check every crate touched by the PR and return the problems found."""
    problems = []

    # Crate directory -> changed files in it. Files that are not manifests are
    # left to check-changes.py.
    crates: dict[str, list[str]] = {}
    for status, path in changed_files():
        match = MANIFEST_RE.match(path)
        if match:
            crates.setdefault(os.path.dirname(path), []).append(
                f"{status} {path}")

    for crate_dir, changes in sorted(crates.items()):
        crate = os.path.basename(crate_dir)
        print()
        print(f"== {crate}")
        for change in changes:
            print(f"   {change}")

        try:
            source = authority(crate_dir)
            if source is None:
                print("   SKIPPED: new crate, requires manual review")
                continue

            logins = load(BASE, source).get("maintainers-logins", [])
        except ValueError as e:
            problems.append(f"{crate}: {e}")
            continue

        print(f"   Maintainers per {source}: {', '.join(logins) or 'none'}")

        # GitHub logins are case-insensitive
        if author.lower() in (login.lower() for login in logins):
            print(f"   OK: {author} is a maintainer")
        else:
            problems.append(
                f"{crate}: {author} is not in maintainers-logins of {source}")

    return problems


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1]:
        print("Usage: check-author.py <PR author GitHub login>",
              file=sys.stderr)
        return 2

    author = sys.argv[1]
    print(f"PR author: {author}")

    return report(
        check(author),
        success="PR author may change every touched crate",
        failure="PR author is not a maintainer of some touched crate")


if __name__ == "__main__":
    sys.exit(main())
