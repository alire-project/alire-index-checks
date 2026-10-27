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

# Maximum length of everything after X.Y.Z in a version: pre-release and build
# metadata, with their '-' and '+' separators. Same limit as alr's
# Alire.Index.Max_Version_Suffix_Length.
MAX_VERSION_SUFFIX_LENGTH = 32


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True,
                          capture_output=True).stdout


def changed_files() -> list[tuple[str, str]]:
    """Return (status, path) pairs changed by the PR. Renames are reported
    as a deletion plus an addition.

    With ALL_CRATES set, every manifest in the index is reported as added
    instead (see "Mirror workflows" in README.md)."""
    if "ALL_CRATES" in os.environ:
        paths = git("ls-tree", "-r", "-z", "--name-only", HEAD, "--",
                    "index/").decode().split("\0")
        return [("A", path) for path in sorted(paths)
                if path.endswith(".toml") and "-" in os.path.basename(path)]

    fields = git("diff", "--name-status", "--no-renames", "-z",
                 BASE, HEAD).decode().split("\0")
    fields = [f for f in fields if f]
    return list(zip(fields[0::2], fields[1::2]))


def load(rev: str, path: str) -> dict[str, Any]:
    """Parse the manifest at `path` in revision `rev`. Raises ValueError on
    invalid TOML, or TOML nested too deep for the (recursive) parser."""
    try:
        return tomllib.loads(git("show", f"{rev}:{path}").decode())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"invalid TOML in {rev}:{path}: {e}") from e
    except RecursionError as e:
        raise ValueError(f"TOML nested too deep in {rev}:{path}") from e


def report(problems: list[str], success: str, failure: str) -> int:
    """Print the outcome of a check and return its exit status, honouring
    DRY_RUN (see "Mirror workflows" in README.md)."""
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

    # Imported here, as checks not ordering versions run without the
    # dependencies in requirements.txt (e.g. check-changes.py)
    from semver import Version

    try:
        parsed = sorted((Version.parse(v), v) for v in versions)
    except ValueError as e:
        raise ValueError(f"cannot order versions: {e}") from e

    if len(parsed) > 1 and parsed[-1][0] == parsed[-2][0]:
        raise ValueError("cannot order versions with the same precedence: "
                         f"{parsed[-2][1]}, {parsed[-1][1]}")
    return parsed[-1][1]


def base_manifests(crate_dir: str, rev: str = BASE) -> dict[str, str]:
    """Return {version: path} of the manifests of a crate in the base branch
    (or in revision `rev`), with EXTERNAL as the version of its external
    manifest."""
    paths = git("ls-tree", "-r", "-z", "--name-only", rev, "--",
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


def without_available(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of `manifest` without its top-level `available`
    key, so that a withdrawal can be detected by comparing the remainder of
    the base and PR versions. Nested `available` keys (e.g. inside a table)
    are kept, as they are not a withdrawal of the release."""

    return {k: v for k, v in manifest.items() if k != "available"}


def is_withdrawal(path: str) -> bool:
    """Say if the PR modifies the manifest at `path` only to withdraw its
    release: it is identical to the base version once parsed, except for a
    top-level `available = false`."""

    old = load(BASE, path)
    new = load(HEAD, path)
    return (new.get("available") is False
            and old.get("available") is not False
            and without_available(new) == without_available(old))


def version_suffix_problems(version: str, where: str) -> list[str]:
    """Return the problems with the length of what follows X.Y.Z in
    `version`, found in `where`."""

    # X.Y.Z has neither '-' nor '+', so the suffix starts at the first of them
    starts = [i for i in (version.find("-"), version.find("+")) if i >= 0]
    suffix = version[min(starts):] if starts else ""
    if len(suffix) > MAX_VERSION_SUFFIX_LENGTH:
        return [f"pre-release and build metadata of version {version} in "
                f"{where} are longer than {MAX_VERSION_SUFFIX_LENGTH} "
                f"characters in total"]
    return []
