#!/usr/bin/env python3
"""
Check that the releases added by a PR to an Alire index have a website.

Usage:
    check-website.py [ignored]

Must run in an index checkout whose HEAD is the PR merge commit and HEAD~1
the base commit, as in CI. Every added manifest, externals excepted, must
have a non-blank top-level `website`. A crate without a website of its own
can use its upstream repository; for git origins, the repository URL is
suggested. Withdrawals and other changes are left to the other checks.

Environment:
    ALL_CRATES, DRY_RUN: see "Mirror workflows" in README.md

Exit status:
    0  every added release has a website (or DRY_RUN set)
    1  some added release has none

Dependencies: tomllib (Python >= 3.11)
"""

import sys
from typing import Any

from common import EXTERNAL, HEAD, MANIFEST_RE, changed_files, load, report


def repository(manifest: dict[str, Any]) -> str | None:
    """Return the web URL of the git repository of a release, if it has a git
    origin, e.g. https://github.com/user/repo for
    git+https://github.com/user/repo.git."""
    origin = manifest.get("origin")
    url = origin.get("url") if isinstance(origin, dict) else None
    if not isinstance(url, str) or not (url.startswith("git+")
                                        or url.endswith(".git")):
        return None
    url = url.removeprefix("git+").removesuffix(".git")
    return url if url.startswith("https://") else None


def check() -> list[str]:
    """Return the added releases without a website."""
    problems = []

    for status, path in changed_files():
        match = MANIFEST_RE.match(path)
        if status != "A" or not match or match["version"] == EXTERNAL:
            continue

        try:
            manifest = load(HEAD, path)
        except ValueError as e:
            problems.append(str(e))
            continue

        website = manifest.get("website")
        if isinstance(website, str) and website.strip():
            print(f"{path}: {website}")
            continue

        problem = f"{path} has no `website`"
        url = repository(manifest)
        if url:
            problem += f"; its repository can serve: website = \"{url}\""
        problems.append(problem)

    return problems


def main() -> int:
    return report(
        check(),
        success="every new release has a website",
        failure="some new release has no website. A crate without a website "
                "of its own can use its upstream repository, e.g. "
                "`website = \"https://github.com/user/repo\"`")


if __name__ == "__main__":
    sys.exit(main())
