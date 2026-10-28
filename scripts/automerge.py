"""
Decide whether a PR to an Alire index may be merged automatically, as
described in AEP-6 (doc/AEPs/aep-0006-auto-merge.md in the alire repository).

Used by the auto-merge bot, and through check-automerge.py from the command
line. Like the checks, it operates on an index checkout whose HEAD is the PR
merge commit and HEAD~1 the base commit.

A PR is eligible when it is either:

- New releases of crates that already have releases in the base branch, each
  by one of the crate's maintainers and keeping every property in
  UNCHANGEABLE, and the origin kind and repository, as in the latest release
  in the base branch, which is the one reviewed by hand last.
- Withdrawals (`available = false`) of releases of crates maintained by the
  PR author.

Everything else needs a manual review: first releases, externals, mixed PRs,
other modifications, deletions and any file outside the release manifests.
The checks run on PRs (check-changes.py and the others) still apply: the bot
requires them to pass. Their results can be trusted because an eligible PR
does not touch the workflows that run them.

Dependencies: tomllib (Python >= 3.11), semver (in requirements.txt)
"""

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from semver import Version

from common import (BASE, EXTERNAL, HEAD, MANIFEST_RE, authority,
                    base_manifests, changed_files, is_withdrawal, load,
                    version_suffix_problems)

# Properties that only a manual review may change, as they decide who
# maintains a crate, which crates it stands for or excludes, and what runs on
# users' machines besides the build. `build-switches` is left out on purpose:
# some switches (e.g. GCC's -wrapper) run programs during the build, but the
# crate's project files, which change freely with its sources, can do the
# same, and code run by the build is out of scope for AEP-6.
UNCHANGEABLE = ["maintainers-logins", "provides", "forbids", "actions",
                "environment", "gpr-set-externals", "mirror"]

RELEASES = "releases"
WITHDRAWALS = "withdrawals"


@dataclass
class Eligibility:
    eligible: bool
    kind: str | None
    """RELEASES or WITHDRAWALS when eligible (withdrawals skip the cooling
    off period), None otherwise"""
    releases: list[tuple[str, str]]
    """(crate, version) of every release added or withdrawn"""
    reasons: list[str]
    """Why the PR is not eligible, one per broken rule (empty if eligible)"""


def repository(url: str, archive: bool) -> str:
    """Return the repository an origin URL belongs to: its host and first
    two path segments (owner and repository in code forges), lowercased and
    without VCS prefix or `.git` suffix. For archives, the file name is left
    out first, as it usually contains the version (e.g. a host serving
    /alire/crate-1.0.tgz)."""
    for prefix in ("git+", "hg+"):
        url = url.removeprefix(prefix)
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if archive:
        segments = segments[:-1]
    return "/".join([parts.hostname or ""] + segments[:2]) \
        .lower().removesuffix(".git")


def origin_identities(value: Any) -> set[tuple[str, str]]:
    """Return the (kind, repository) of every origin in an origin table,
    including those in every case of case(...) expressions. The kind is
    "vcs" for origins with a commit, "archive" for the rest."""
    if not isinstance(value, dict):
        return set()
    if isinstance(value.get("url"), str):
        kind = "vcs" if "commit" in value else "archive"
        return {(kind, repository(value["url"], archive=kind == "archive"))}
    return set().union(*(origin_identities(child)
                         for child in value.values()))


def changed_properties(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Return which of the properties that only a manual review may change
    differ between releases `old` and `new`: keys in UNCHANGEABLE, and
    "origin" for the origin kind or repository."""
    changed = [key for key in UNCHANGEABLE if old.get(key) != new.get(key)]
    if (origin_identities(old.get("origin"))
            != origin_identities(new.get("origin"))):
        changed.append("origin")
    return changed


def change_reasons(crate: str, old: dict[str, Any], new: dict[str, Any],
                   old_version: str) -> list[str]:
    """Return the reasons why release `new` of `crate` cannot be merged
    automatically after release `old`, of version `old_version`."""
    return [f"{crate}: "
            + ("origin kind or repository" if key == "origin"
               else f"`{key}`")
            + f" differs from release {old_version}"
            for key in changed_properties(old, new)]


def maintainer_reasons(crate: str, source: str, author: str) -> list[str]:
    """Return the reason why `author` may not change `crate`, whose
    maintainers are given by the base-branch manifest at `source`."""
    logins = load(BASE, source).get("maintainers-logins", [])
    # GitHub logins are case-insensitive
    if author.lower() in (login.lower() for login in logins):
        return []
    return [f"{crate}: {author} is not in maintainers-logins of {source}"]


def release_reasons(path: str, crate: str, version: str,
                    author: str) -> list[str]:
    """Return the reasons why the release added at `path` cannot be merged
    automatically."""
    crate_dir = os.path.dirname(path)
    manifests = base_manifests(crate_dir)
    versions = [v for v in manifests if v != EXTERNAL]
    if not versions:
        return [f"{crate}: first release of the crate"]

    source = authority(crate_dir)
    reasons = maintainer_reasons(crate, source, author)

    try:
        parsed = Version.parse(version)
    except ValueError:
        return reasons + [f"{crate}: version {version} is not a strict "
                          f"semantic version"]
    reasons += version_suffix_problems(version, f"file name {path}")
    for existing in versions:
        try:
            if Version.parse(existing) == parsed:
                reasons.append(f"{crate}: version {version} has the same "
                               f"precedence as existing release {existing}")
        except ValueError:
            pass  # Not strict, so it cannot equal a strict version

    # Authority is decided from the file path, so the release must be the
    # one the path says (alr checks it too, this does not rely on it)
    new = load(HEAD, path)
    if new.get("name") != crate or new.get("version") != version:
        reasons.append(f"{crate}: name or version in {path} differ from its "
                       f"file name")

    old_version = MANIFEST_RE.match(source)["version"]
    return reasons + change_reasons(crate, load(BASE, source), new,
                                    old_version)


def evaluate(author: str) -> Eligibility:
    """Decide whether the PR, opened by `author`, may be merged
    automatically."""
    releases = []
    reasons = []
    kinds = set()

    changes = changed_files()
    if not changes:
        reasons.append("the PR changes no files")

    for status, path in changes:
        match = MANIFEST_RE.match(path)
        if (not match or match["file_crate"] != match["crate"]
                or match["shelf"] != match["crate"][:2]):
            reasons.append(f"{path}: not a release manifest")
            continue

        crate = match["crate"]
        version = match["version"]
        if version == EXTERNAL:
            reasons.append(f"{path}: externals need a manual review")
            continue

        try:
            if status == "A":
                kinds.add(RELEASES)
                reasons += release_reasons(path, crate, version, author)
            elif status == "M" and is_withdrawal(path):
                kinds.add(WITHDRAWALS)
                reasons += maintainer_reasons(
                    crate, authority(os.path.dirname(path)), author)
            else:
                reasons.append(f"{path}: only new releases and withdrawals "
                               f"can be merged automatically")
                continue
        except ValueError as e:
            reasons.append(f"{crate}: {e}")
            continue

        releases.append((crate, version))

    if len(kinds) > 1:
        reasons.append("the PR mixes new releases and withdrawals")

    eligible = not reasons
    return Eligibility(eligible=eligible,
                       kind=kinds.pop() if eligible else None,
                       releases=releases,
                       reasons=reasons)
