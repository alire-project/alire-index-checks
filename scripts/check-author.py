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

from semver import Version

from common import (BASE, EXTERNAL, MANIFEST_RE, changed_files, git, load,
                    report)


def latest(versions: list[str]) -> str:
    """Return the latest of the given versions (as found in manifest file
    names) by semver precedence (https://semver.org/#spec-item-11), which is
    what decides which release has authority over a crate.

    Text order would not do, as an attacker could gain authority with a
    version that sorts last as text but is not the latest. Semver precedence,
    as implemented by the semver library, gives instead:

    - 10.0.0 > 9.0.0 (numeric components, while "9.0.0" > "10.0.0" as text)
    - 2.0.0 > 2.0.0-rc1 (a pre-release is lower than its plain version)
    - 1.0.0-alpha.beta > 1.0.0-alpha.10 > 1.0.0-alpha.2 (numeric identifiers
      compare numerically and lower than alphanumeric ones)

    To fail closed rather than guess, a ValueError is raised for:

    - Versions that are not strict semver (e.g., 01.0.0 or 1.0), some of
      which alr accepts with its own interpretation.
    - Ties: semver ignores build metadata, so 2.0.0 and 2.0.0+x have the same
      precedence and neither is unambiguously the latest."""
    try:
        parsed = sorted((Version.parse(v), v) for v in versions)
    except ValueError as e:
        raise ValueError(f"cannot order versions: {e}") from e

    if len(parsed) > 1 and parsed[-1][0] == parsed[-2][0]:
        raise ValueError("cannot order versions with the same precedence: "
                         f"{parsed[-2][1]}, {parsed[-1][1]}")
    return parsed[-1][1]


def base_manifests(crate_dir: str) -> dict[str, str]:
    """Return {version: path} of the manifests of a crate in the base branch,
    with EXTERNAL as the version of its external manifest."""
    paths = git("ls-tree", "-r", "-z", "--name-only", BASE, "--",
                crate_dir + "/").decode().split("\0")
    manifests = {}
    for path in paths:
        match = MANIFEST_RE.match(path)
        if match:
            manifests[match["version"]] = path
    return manifests


def authority(crate_dir: str) -> str | None:
    """Return the base-branch manifest that decides who maintains a crate, or
    None for a crate that does not exist in the base branch.

    This is the latest release by semver precedence (see latest), not the
    most recently added file nor the release preceding the submitted version.
    Since only base-branch files are considered, a PR cannot influence which
    release is the latest, e.g. by adding a lower or intermediate version.
    Pre-releases count: if 3.0.0-rc1 is the latest, its maintainers decide."""
    manifests = base_manifests(crate_dir)
    releases = [v for v in manifests if v != EXTERNAL]
    if releases:
        return manifests[latest(releases)]
    return manifests.get(EXTERNAL)


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
