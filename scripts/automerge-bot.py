#!/usr/bin/env python3
"""
Merge eligible PRs to an Alire index automatically, as described in AEP-6
(doc/AEPs/aep-0006-auto-merge.md in the alire repository).

Usage:
    automerge-bot.py --repo OWNER/NAME --base BRANCH --mode dry-run|on
                     --bot-login LOGIN [--issue NUMBER]

Meant to run periodically (see auto-merge.yml in alire-index). Each run
decides, for every open PR to the base branch, whether to:

- skip it for now (drafts, PRs with conflicts or changed during the run);
- leave it to a manual review, giving the reasons;
- wait (an eligible PR whose checks are running, or that is cooling off);
- merge it.

A PR is merged when it is eligible (automerge.py), no maintainer vetoed it,
all its checks passed, each new release was built (or fetched) on at least
one platform, it has cooled off for COOLING_OFF since it last changed (except
withdrawals), and its author stays within MAX_RELEASES_PER_DAY.

Nothing from the PR is executed: its head is fetched into a bare repository
and merged there with the current tip of the base branch (`git merge-tree`,
as GitHub only updates its own merge commits from time to time), and only
manifests are parsed. Check results can be trusted because eligible PRs only
touch manifests, so they run the base workflows.

Modes:
    dry-run  Decide and report, without touching any PR. Changes in decisions
             are logged as comments in the tracking issue given by --issue.
             As nothing is merged, the rate limit always counts zero
             previous releases.
    on       Also label and comment on PRs, and merge them. PRs skipped after
             having been found eligible lose their pending label.

In both modes, a table of decisions is written to $GITHUB_STEP_SUMMARY when
set, and to the standard output.

Environment:
    GH_TOKEN  Token for the GitHub API (read access to the repository; write
              access to issues in dry-run mode, and to pull requests and
              contents in on mode)

Exit status:
    0  every PR was processed
    1  some PR could not be processed, or the run stopped early (when the
       base branch cannot be fetched after a merge, or comments are not posted
       as --bot-login); see the summary and the log
    2  usage error

Dependencies: tomllib (Python >= 3.11), semver (in requirements.txt)
"""

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from automerge import RELEASES, Eligibility, evaluate
from common import EXTERNAL, MANIFEST_RE

# Time between the last change to an eligible PR (see decide()) and its
# merge, during which maintainers can veto it. Withdrawals are merged without
# waiting.
COOLING_OFF = timedelta(hours=1)

# Releases that an author may have merged automatically within RATE_WINDOW,
# as crates.io limits publishing per user. Beyond it, PRs need a manual
# review.
MAX_RELEASES_PER_DAY = 10
RATE_WINDOW = timedelta(days=1)
RATE_WINDOW_TEXT = "the last 24 hours"

# Labels. The veto one is set by maintainers, the others by the bot.
VETO_LABEL = "auto-merge: veto"
PENDING_LABEL = "auto-merge: pending"
MANUAL_LABEL = "auto-merge: manual"
MERGED_LABEL = "auto-merged"

# Decisions
SKIP = "skip"
MANUAL = "manual"
PENDING = "pending"
MERGE = "merge"

# Check GHA run conclusions that do not block a merge: "success" (passed),
# "neutral" (finished without a verdict, which only third-party apps report)
# and "skipped" (a job not run because of its `if:` condition). The others,
# "failure", "cancelled", "timed_out", "action_required" and "stale", do.
PASSING = {"success", "neutral", "skipped"}

# Review authors whose requests for changes count as a veto: those with
# write access to the repository
MAINTAINERS = {"OWNER", "MEMBER", "COLLABORATOR"}

# Title of the annotation with build outcomes, written by gh-build-crate.sh
BUILD_ANNOTATION = "alire-build"
BUILD_SUCCESSES = {"built", "fetched"}

# Hidden marker in the bot's comments, identifying the PR and decision
MARKER_RE = re.compile(
    r"<!-- automerge pr=(?P<pr>\d+) sha=(?P<sha>\w+) action=(?P<action>\w+)"
    r" -->")

API = "https://api.github.com"

# Base branch in the bare repository where PRs are merged
BASE_REF = "refs/heads/base"


@dataclass
class PullRequest:
    number: int
    title: str
    author: str
    head_sha: str
    draft: bool
    labels: list[str]
    created_at: datetime


@dataclass
class Review:
    login: str
    association: str
    """author_association, e.g. MEMBER or NONE"""
    state: str
    """APPROVED, CHANGES_REQUESTED, COMMENTED or DISMISSED"""


@dataclass
class CheckRun:
    id: int
    name: str
    status: str
    """queued, in_progress or completed"""
    conclusion: str | None


@dataclass
class Decision:
    action: str
    reasons: list[str]
    eligibility: Eligibility | None = None
    merge_after: datetime | None = None


class PRData(Protocol):
    """Data about a PR, fetched on demand, so that decide() only asks for
    what it needs for its decision."""

    def merge_problem(self) -> str | None:
        """Why the PR cannot be merged with the base branch, if so. Must be
        called before eligibility(), which evaluates that merge."""

    def eligibility(self) -> Eligibility: ...

    def reviews(self) -> list[Review]: ...

    def checks(self) -> list[CheckRun]: ...

    def build_annotations(self) -> list[str]:
        """Messages of the BUILD_ANNOTATION annotations of every check"""

    def first_check_at(self) -> datetime | None:
        """When checks first started for the current head of the PR"""

    def changed_at(self) -> datetime | None:
        """When the PR last left draft (as `alr publish` opens drafts), was
        reopened, retargeted from another base branch, or had its head
        force-pushed, if ever"""

    def recent_releases(self, author: str, since: datetime) -> int:
        """Releases in PRs by `author` merged automatically since `since`"""


# --- Decisions (no side effects)


# Custom to avoid a dependency
def duration(span: timedelta) -> str:
    """Return a short text for a time span, rounded down to its two largest
    units, and to minutes unless shorter: 2d 3h, 1h 5m, 1h, 23m, 45s."""

    seconds = max(int(span.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s"
    parts = []
    for name, size in [("d", 86400), ("h", 3600), ("m", 60)]:
        if seconds >= size or parts:
            parts.append((seconds // size, name))
            seconds %= size
    # Two units at most, without a trailing zero ("1h", not "1h 0m")
    parts = parts[:2]
    if len(parts) == 2 and parts[1][0] == 0:
        parts.pop()
    return " ".join(f"{value}{name}" for value, name in parts)


def build_outcomes(messages: list[str]) -> dict[str, set[str]]:
    """Return {milestone: outcomes} from BUILD_ANNOTATION messages, which
    look like "built hello=1.0.0;skipped world=2.0.0 (missing ...)"."""
    outcomes: dict[str, set[str]] = {}
    for message in messages:
        for entry in message.split(";"):
            words = entry.split()
            if len(words) >= 2:
                outcomes.setdefault(words[1], set()).add(words[0])
    return outcomes


def veto_reasons(labels: list[str], reviews: list[Review]) -> list[str]:
    """Return the vetoes against merging a PR: the veto label, or a pending
    request for changes by a maintainer (their latest review that is not a
    plain comment)."""
    vetoes = []
    if VETO_LABEL in labels:
        vetoes.append(f"vetoed with the `{VETO_LABEL}` label")

    latest: dict[str, Review] = {}
    for review in reviews:  # In chronological order
        if (review.association in MAINTAINERS
                and review.state != "COMMENTED"):
            latest[review.login] = review
    vetoes += [f"changes requested by {review.login}"
               for review in latest.values()
               if review.state == "CHANGES_REQUESTED"]
    return vetoes


def decide(pr: PullRequest, data: PRData, now: datetime) -> Decision:
    """Decide what to do with `pr` at time `now`."""
    if pr.draft:
        return Decision(SKIP, ["draft"])

    problem = data.merge_problem()
    if problem:
        return Decision(SKIP, [problem])

    eligibility = data.eligibility()
    if not eligibility.eligible:
        return Decision(MANUAL, eligibility.reasons, eligibility)

    vetoes = veto_reasons(pr.labels, data.reviews())
    if vetoes:
        return Decision(MANUAL, vetoes, eligibility)

    checks = data.checks()
    failed = [check.name for check in checks
              if check.status == "completed"
              and check.conclusion not in PASSING]
    if failed:
        return Decision(MANUAL, [f"check failed: {name}" for name in failed],
                        eligibility)
    running = [check.name for check in checks if check.status != "completed"]
    if running or not checks:
        return Decision(PENDING, ["waiting for checks to complete"],
                        eligibility)

    passed = f"all {len(checks)} checks passed"
    if eligibility.kind != RELEASES:
        return Decision(MERGE, ["eligible withdrawal", passed], eligibility)

    outcomes = build_outcomes(data.build_annotations())
    unbuilt = [f"{crate}={version}" for crate, version in eligibility.releases
               if not outcomes.get(f"{crate}={version}", set())
               & BUILD_SUCCESSES]
    if unbuilt:
        return Decision(MANUAL, [f"{milestone} was not built on any platform"
                                 for milestone in unbuilt], eligibility)

    # Cooling off starts when the PR last changed, so that maintainers see it
    # for the whole period: when checks first ran on its head, when it was
    # opened (the head may have been checked long before in another PR), or
    # when it left draft, was reopened, retargeted or force-pushed (maybe to
    # an old head)
    started = max(time for time in (data.first_check_at(), pr.created_at,
                                    data.changed_at())
                  if time)
    merge_after = started + COOLING_OFF
    if now < merge_after:
        return Decision(PENDING,
                        [f"cooling off: {duration(merge_after - now)} left"],
                        eligibility, merge_after)

    recent = data.recent_releases(pr.author, now - RATE_WINDOW)
    if recent + len(eligibility.releases) > MAX_RELEASES_PER_DAY:
        return Decision(MANUAL, [
            f"{pr.author} would exceed {MAX_RELEASES_PER_DAY} releases "
            f"merged automatically in {RATE_WINDOW_TEXT} ({recent} so far)"],
            eligibility)

    return Decision(MERGE, [
        "eligible releases", passed,
        "built or fetched: " + ", ".join(
            f"{crate}={version}" for crate, version in eligibility.releases),
        f"no veto since {started:%Y-%m-%d %H:%M} UTC",
        f"{recent} other releases by {pr.author} merged automatically in "
        f"{RATE_WINDOW_TEXT}"],
        eligibility, merge_after)


# --- Messages


def neutralize(text: str) -> str:
    """Return text from a PR (its title, or file names in reasons) safe to
    include in the bot's comments: on a single line, and with HTML entities
    so that `@` and `#` cannot notify users or reference issues, `<` cannot
    add HTML (e.g. a fake marker) and `[` cannot add links or images."""
    text = " ".join(text.splitlines())
    for char, entity in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                         ("@", "&#64;"), ("#", "&#35;"), ("[", "&#91;"),
                         ("]", "&#93;")):
        text = text.replace(char, entity)
    return text


def marker(pr: PullRequest, decision: Decision) -> str:
    return (f"<!-- automerge pr={pr.number} sha={pr.head_sha} "
            f"action={decision.action} -->")


def describe(decision: Decision) -> str:
    """Return a Markdown description of a decision."""
    releases = (decision.eligibility.releases
                if decision.eligibility else [])
    lines = []
    if releases:
        lines.append("Releases: " + ", ".join(f"`{crate}={version}`"
                                              for crate, version in releases))
    lines += [f"- {neutralize(reason)}" for reason in decision.reasons]
    return "\n".join(lines)


def log_entry(pr: PullRequest, decision: Decision, dry_run: bool) -> str:
    """Return the tracking issue comment for a new decision. The PR is not
    linked nor its author mentioned, so that neither is notified."""
    would = "would be merged" if dry_run else "merged"
    headline = {
        MANUAL: "needs a manual review",
        PENDING: "is eligible and waiting"
        + (f" until {decision.merge_after:%Y-%m-%d %H:%M} UTC"
           if decision.merge_after else ""),
        MERGE: would,
    }[decision.action]
    return (f"{marker(pr, decision)}\n"
            f"PR {pr.number} ({neutralize(pr.title)}) by `{pr.author}` "
            f"{headline}.\n\n"
            f"{describe(decision)}\n")


def pr_comment(pr: PullRequest, decision: Decision) -> str:
    """Return the bot's comment on a PR for a decision (on mode)."""
    if decision.action == MANUAL:
        text = ("This PR cannot be merged automatically, and will be "
                "reviewed by the index maintainers:")
    elif decision.action == SKIP:
        text = "Automatic merging of this PR is on hold:"
    elif decision.action == PENDING:
        when = (f"after {decision.merge_after:%Y-%m-%d %H:%M} UTC"
                if decision.merge_after else "once its checks pass")
        text = (f"This PR is eligible for automatic merging, and will be "
                f"merged {when} unless a maintainer vetoes it (with the "
                f"`{VETO_LABEL}` label or by requesting changes).")
    else:
        text = "This PR was merged automatically:"
    return f"{marker(pr, decision)}\n{text}\n\n{describe(decision)}\n"


def login(item: dict[str, Any]) -> str | None:
    """Return the login of the author of a comment or review, or None for
    deleted accounts (the API gives them no user)."""
    return (item.get("user") or {}).get("login")


def previous_decisions(comments: list[dict[str, Any]],
                       bot_login: str) -> dict[int, tuple[str, str]]:
    """Return {PR number: (head SHA, action)} of the latest decision logged
    by the bot in `comments`. Comments by anyone else are ignored, so that
    they cannot suppress or fake entries."""
    decisions = {}
    for comment in comments:
        if login(comment) != bot_login:
            continue
        match = MARKER_RE.search(comment["body"])
        if match:
            decisions[int(match["pr"])] = (match["sha"], match["action"])
    return decisions


# --- GitHub


class HTTPError(OSError):
    """An HTTP error answered by GitHub, with its explanation."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class GitHub:
    """Minimal client for the GitHub REST API."""

    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.token = token

    def request(self, method: str, path: str, body: Any = None,
                params: dict[str, Any] | None = None) -> Any:
        url = API + path.replace("{repo}", self.repo)
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Accept": "application/vnd.github+json",
                     "Authorization": f"Bearer {self.token}",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "alire-automerge-bot"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
        except urllib.error.HTTPError as e:
            # Keep GitHub's explanation, e.g. why a merge was refused
            raise HTTPError(e.code, f"{method} {path}: {e.code} "
                            f"{e.read().decode(errors='replace')}") from e
        return json.loads(data) if data else None

    def get_all(self, path: str, params: dict[str, Any] | None = None,
                key: str | None = None) -> list[Any]:
        """Return all the items of a paginated listing (under `key` for
        listings wrapped in an object)."""
        items: list[Any] = []
        page = 1
        while True:
            data = self.request("GET", path, params={
                **(params or {}), "per_page": 100, "page": page})
            batch = data[key] if key else data
            items += batch
            if len(batch) < 100:
                return items
            page += 1


def git(git_dir: str, *args: str) -> str:
    return subprocess.run(["git", "--git-dir", git_dir, *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class LivePRData:
    """PRData from GitHub, cached for the duration of a run."""

    def __init__(self, gh: GitHub, git_dir: str, pr: PullRequest,
                 merged_in_run: dict[str, int]):
        self.gh = gh
        self.git_dir = git_dir
        self.pr = pr
        self.merged_in_run = merged_in_run
        """Releases merged by author in this run, which searches miss"""
        self._checks: list[CheckRun] | None = None
        self._suites: list[dict[str, Any]] | None = None

    def suites(self) -> list[dict[str, Any]]:
        if self._suites is None:
            self._suites = self.gh.get_all(
                f"/repos/{{repo}}/commits/{self.pr.head_sha}/check-suites",
                key="check_suites")
        return self._suites

    def merge_problem(self) -> str | None:
        head = f"refs/pr/{self.pr.number}"
        git(self.git_dir, "fetch", "--quiet", "--filter=blob:none", "origin",
            f"+refs/pull/{self.pr.number}/head:{head}")
        if git(self.git_dir, "rev-parse", head) != self.pr.head_sha:
            return "the PR changed during this run"

        merged = subprocess.run(
            ["git", "--git-dir", self.git_dir, "merge-tree", "--write-tree",
             BASE_REF, head], capture_output=True, text=True)
        if merged.returncode == 1:
            return "conflicts with the base branch"
        merged.check_returncode()

        # automerge.py works on HEAD (the merge) and HEAD~1 (the base)
        commit = git(self.git_dir, "-c", "user.name=automerge",
                     "-c", "user.email=automerge@localhost", "commit-tree",
                     merged.stdout.split()[0], "-p", BASE_REF, "-p", head,
                     "-m", f"Merge PR {self.pr.number}")
        git(self.git_dir, "update-ref", "--no-deref", "HEAD", commit)
        return None

    def eligibility(self) -> Eligibility:
        with contextlib.chdir(self.git_dir):
            return evaluate(self.pr.author)

    def reviews(self) -> list[Review]:
        return [Review(login(review), review["author_association"],
                       review["state"])
                for review in self.gh.get_all(
                    f"/repos/{{repo}}/pulls/{self.pr.number}/reviews")
                if login(review)]

    def checks(self) -> list[CheckRun]:
        if self._checks is None:
            self._checks = [
                CheckRun(run["id"], run["name"], run["status"],
                         run["conclusion"])
                for run in self.gh.get_all(
                    f"/repos/{{repo}}/commits/{self.pr.head_sha}/check-runs",
                    params={"filter": "latest"}, key="check_runs")]
            # Workflows create check runs only once their jobs start, so a
            # workflow still queued shows only as an unfinished suite. Those
            # of other apps are left out, as some never complete.
            self._checks += [
                CheckRun(0, f"workflows (suite {suite['id']})",
                         suite["status"], suite["conclusion"])
                for suite in self.suites()
                if (suite.get("app") or {}).get("slug") == "github-actions"
                and (suite["status"] != "completed"
                     or suite["conclusion"] not in PASSING)]
        return self._checks

    def build_annotations(self) -> list[str]:
        return [annotation["message"]
                for check in self.checks() if check.id
                for annotation in self.gh.get_all(
                    f"/repos/{{repo}}/check-runs/{check.id}/annotations")
                if annotation.get("title") == BUILD_ANNOTATION]

    def first_check_at(self) -> datetime | None:
        times = [datetime.fromisoformat(suite["created_at"])
                 for suite in self.suites()]
        return min(times, default=None)

    def changed_at(self) -> datetime | None:
        times = [datetime.fromisoformat(event["created_at"])
                 for event in self.gh.get_all(
                     f"/repos/{{repo}}/issues/{self.pr.number}/timeline")
                 if event.get("event") in ("ready_for_review", "reopened",
                                           "base_ref_changed",
                                           "head_ref_force_pushed")]
        return max(times, default=None)

    def recent_releases(self, author: str, since: datetime) -> int:
        query = (f'repo:{self.gh.repo} is:pr is:merged '
                 f'label:"{MERGED_LABEL}" author:{author} '
                 f'merged:>={since:%Y-%m-%dT%H:%M:%SZ}')
        count = 0
        for item in self.gh.get_all("/search/issues", {"q": query},
                                    key="items"):
            for changed in self.gh.get_all(
                    f"/repos/{{repo}}/pulls/{item['number']}/files"):
                match = MANIFEST_RE.match(changed["filename"])
                if (changed["status"] == "added" and match
                        and match["version"] != EXTERNAL):
                    count += 1
        return count + self.merged_in_run.get(author, 0)


# --- Effects


class WrongIdentity(Exception):
    """The bot posts as another account than --bot-login, so it would not
    recognize its own comments, and would post them again on every run."""


class Bot:
    def __init__(self, gh: GitHub, mode: str, bot_login: str,
                 issue: int | None):
        self.gh = gh
        self.mode = mode
        self.bot_login = bot_login
        self.issue = issue
        self.logged = (previous_decisions(self.gh.get_all(
            f"/repos/{{repo}}/issues/{issue}/comments"), bot_login)
            if mode == "dry-run" else {})

    def post(self, method: str, path: str, body: str) -> None:
        """Post or edit a comment, checking that it is the bot's own."""
        comment = self.gh.request(method, path, {"body": body})
        if login(comment) != self.bot_login:
            raise WrongIdentity(f"comments are posted as {login(comment)}, "
                                f"not as {self.bot_login} (--bot-login)")

    def log(self, pr: PullRequest, decision: Decision) -> None:
        """Log a new decision in the tracking issue (dry-run mode)."""
        if self.logged.get(pr.number) == (pr.head_sha, decision.action):
            return
        self.post("POST", f"/repos/{{repo}}/issues/{self.issue}/comments",
                  log_entry(pr, decision, dry_run=True))

    def unlabel(self, pr: PullRequest, label: str) -> None:
        """Remove a label, unless someone did it after the PR was listed."""
        try:
            self.gh.request("DELETE", f"/repos/{{repo}}/issues/{pr.number}"
                            f"/labels/" + urllib.parse.quote(label))
        except HTTPError as e:
            if e.code != 404:
                raise

    def label(self, pr: PullRequest, wanted: str) -> None:
        """Leave `wanted` as the only label of the bot on the PR."""
        for label in (PENDING_LABEL, MANUAL_LABEL, MERGED_LABEL):
            if label in pr.labels and label != wanted:
                self.unlabel(pr, label)
        if wanted not in pr.labels:
            self.gh.request("POST",
                            f"/repos/{{repo}}/issues/{pr.number}/labels",
                            {"labels": [wanted]})

    def comment(self, pr: PullRequest, body: str,
                only_update: bool = False) -> None:
        """Create or update the bot's comment on a PR (only update it, with
        `only_update`)."""
        for comment in self.gh.get_all(
                f"/repos/{{repo}}/issues/{pr.number}/comments"):
            if (login(comment) == self.bot_login
                    and MARKER_RE.search(comment["body"])):
                if comment["body"] != body:
                    self.post(
                        "PATCH",
                        f"/repos/{{repo}}/issues/comments/{comment['id']}",
                        body)
                return
        if not only_update:
            self.post("POST", f"/repos/{{repo}}/issues/{pr.number}/comments",
                      body)

    def merge(self, pr: PullRequest) -> None:
        """Merge the evaluated head of a PR: GitHub refuses it if the PR
        changed since, and enforces the required checks. Must precede
        apply(), which labels and comments the merged PR."""
        self.gh.request("PUT", f"/repos/{{repo}}/pulls/{pr.number}/merge",
                        {"merge_method": "squash", "sha": pr.head_sha})

    def apply(self, pr: PullRequest, decision: Decision) -> None:
        """Label and comment according to a decision (on mode), or log it
        (dry-run mode)."""
        if self.mode == "dry-run":
            if decision.action != SKIP:
                self.log(pr, decision)
            return

        if decision.action == SKIP:
            # Withdraw an earlier promise to merge
            if PENDING_LABEL in pr.labels:
                self.unlabel(pr, PENDING_LABEL)
                self.comment(pr, pr_comment(pr, decision), only_update=True)
            return

        if decision.action == MERGE:
            self.label(pr, MERGED_LABEL)
        else:
            self.label(pr, PENDING_LABEL if decision.action == PENDING
                       else MANUAL_LABEL)
        self.comment(pr, pr_comment(pr, decision))


# Author of PRs by deleted accounts, which maintain no crate
DELETED_AUTHOR = "(deleted)"


def pull_request(data: dict[str, Any]) -> PullRequest:
    return PullRequest(number=data["number"], title=data["title"],
                       author=login(data) or DELETED_AUTHOR,
                       head_sha=data["head"]["sha"], draft=data["draft"],
                       labels=[label["name"] for label in data["labels"]],
                       created_at=datetime.fromisoformat(data["created_at"]))


# Row of the summary: PR number, author, decision, reasons and releases
Row = tuple[int, str, str, str, list[str]]


def milestones(decision: Decision) -> list[str]:
    """Return the releases a decision is about, when known (they are not for
    PRs skipped before being evaluated, e.g. drafts)."""
    if not decision.eligibility:
        return []
    return [f"{crate}={version}"
            for crate, version in decision.eligibility.releases]


def summary(rows: list[Row], mode: str) -> str:
    """Return a Markdown table of the rows, with one line per release (the
    PR is repeated for PRs with several releases)."""
    lines = [f"## Auto-merge ({mode})", "",
             "| PR | Author | Release | Decision | Reasons |",
             "|---|---|---|---|---|"]
    for number, author, action, reasons, releases in rows:
        for release in releases or [""]:
            lines.append(f"| {number} | `{author}` | {release} | {action} | "
                         f"{neutralize(reasons).replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def process(prs: list[dict[str, Any]],
            pr_data: Callable[[PullRequest, dict[str, int]], PRData],
            bot: Bot, fetch_base: Callable[[], None],
            now: datetime) -> tuple[list[Row], int]:
    """Decide and act on every PR (as listed by the API), oldest first.
    Return the rows of the summary and the number of errors."""
    rows = []
    errors = 0
    merged_in_run: dict[str, int] = {}
    stale_base = False
    for data in prs:
        number = data["number"]
        author = login(data) or DELETED_AUTHOR
        if stale_base:
            rows.append((number, author, "not processed",
                         "the base branch could not be fetched after a "
                         "merge", []))
            continue
        try:
            pr = pull_request(data)
            decision = decide(pr, pr_data(pr, merged_in_run), now)
            if decision.action == MERGE and bot.mode == "on":
                bot.merge(pr)
                # Recorded before anything else can fail: the rate limit must
                # count it, and later PRs must be evaluated against the base
                # including it (e.g. to notice a duplicate), or not at all in
                # this run. Labelling it first makes later runs count it too.
                if decision.eligibility.kind == RELEASES:
                    merged_in_run[pr.author] = (
                        merged_in_run.get(pr.author, 0)
                        + len(decision.eligibility.releases))
                stale_base = True
                try:
                    bot.apply(pr, decision)
                finally:
                    fetch_base()
                    stale_base = False
            else:
                bot.apply(pr, decision)
            rows.append((number, author, decision.action,
                         "; ".join(decision.reasons), milestones(decision)))
        except WrongIdentity:
            raise  # Every comment would be posted again on every run
        except Exception as e:
            # Any failure leaves the PR alone (unmerged, unless it was merged
            # just before) and the others are still processed
            traceback.print_exc()
            errors += 1
            rows.append((number, author, "error",
                         f"{type(e).__name__}: {e}", []))
    return rows, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--mode", required=True, choices=["dry-run", "on"])
    parser.add_argument("--bot-login", required=True)
    parser.add_argument("--issue", type=int)
    args = parser.parse_args()
    if args.mode == "dry-run" and args.issue is None:
        parser.error("--issue is required in dry-run mode")

    gh = GitHub(args.repo, os.environ["GH_TOKEN"])
    bot = Bot(gh, args.mode, args.bot_login, args.issue)
    now = datetime.now(timezone.utc)

    with tempfile.TemporaryDirectory() as git_dir:
        def fetch_base() -> None:
            # Without file contents, which git fetches when needed
            git(git_dir, "fetch", "--quiet", "--filter=blob:none", "origin",
                f"+refs/heads/{args.base}:{BASE_REF}")

        git(git_dir, "init", "--quiet", "--bare")
        git(git_dir, "remote", "add", "origin",
            f"https://github.com/{args.repo}.git")
        fetch_base()

        rows, errors = process(
            gh.get_all("/repos/{repo}/pulls",
                       {"state": "open", "base": args.base,
                        "sort": "created", "direction": "asc"}),
            lambda pr, merged_in_run: LivePRData(gh, git_dir, pr,
                                                 merged_in_run),
            bot, fetch_base, now)

    text = summary(rows, args.mode)
    print(text)
    if "GITHUB_STEP_SUMMARY" in os.environ:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
