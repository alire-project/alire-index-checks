"""
Offline tests for scripts/automerge-bot.py: its decisions on fake PR data,
and the parsing of build annotations, reviews and its own comments.

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import importlib.util
import os
import sys
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPTS)

# The script name has a dash, so it is loaded by path
_spec = importlib.util.spec_from_file_location(
    "automerge_bot", os.path.join(SCRIPTS, "automerge-bot.py"))
bot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bot)

from automerge import RELEASES, WITHDRAWALS, Eligibility  # noqa: E402

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def eligible(*releases: tuple[str, str], kind: str = RELEASES) -> Eligibility:
    return Eligibility(True, kind, list(releases), [])


@dataclass
class FakeData:
    """PRData with an eligible single release, built, whose checks passed
    long ago, unless told otherwise."""
    problem: str | None = None
    result: Eligibility = field(
        default_factory=lambda: eligible(("hello", "2.1.0")))
    review_list: list = field(default_factory=list)
    check_list: list = field(default_factory=lambda: [
        bot.CheckRun(1, "native / ubuntu-latest", "completed", "success"),
        bot.CheckRun(2, "check / Release-only", "completed", "success")])
    annotations: list = field(
        default_factory=lambda: ["built hello=2.1.0"])
    started: datetime | None = NOW - timedelta(hours=2)
    changed: datetime | None = None
    recent: int = 0
    asked: list = field(default_factory=list)

    def merge_problem(self):
        self.asked.append("merge")
        return self.problem

    def eligibility(self):
        self.asked.append("eligibility")
        return self.result

    def reviews(self):
        return self.review_list

    def checks(self):
        return self.check_list

    def build_annotations(self):
        self.asked.append("annotations")
        return self.annotations

    def first_check_at(self):
        return self.started

    def changed_at(self):
        return self.changed

    def recent_releases(self, author, since):
        assert since == NOW - bot.RATE_WINDOW
        return self.recent


def pr(**changes) -> "bot.PullRequest":
    values = dict(number=7, title="hello 2.1.0", author="bob",
                  head_sha="abc123", draft=False, labels=[],
                  created_at=NOW - timedelta(days=1))
    values.update(changes)
    return bot.PullRequest(**values)


class TestDecide(unittest.TestCase):

    def decide(self, data=None, **pr_changes):
        return bot.decide(pr(**pr_changes), data or FakeData(), NOW)

    def test_merge(self):
        decision = self.decide()
        self.assertEqual(decision.action, bot.MERGE)
        self.assertIn("all 2 checks passed", decision.reasons)

    def test_draft_is_skipped_without_fetching(self):
        data = FakeData()
        self.assertEqual(self.decide(data, draft=True).action, bot.SKIP)
        self.assertEqual(data.asked, [])

    def test_merge_problem_is_skipped(self):
        data = FakeData(problem="conflicts with the base branch")
        decision = self.decide(data)
        self.assertEqual(decision.action, bot.SKIP)
        self.assertNotIn("eligibility", data.asked)

    def test_not_eligible(self):
        decision = self.decide(FakeData(result=Eligibility(
            False, None, [("hello", "2.1.0")], ["hello: `actions` differs"])))
        self.assertEqual(decision.action, bot.MANUAL)
        self.assertEqual(decision.reasons, ["hello: `actions` differs"])

    def test_veto_label(self):
        decision = self.decide(labels=[bot.VETO_LABEL])
        self.assertEqual(decision.action, bot.MANUAL)

    def test_changes_requested_by_maintainer(self):
        data = FakeData(review_list=[
            bot.Review("alice", "MEMBER", "CHANGES_REQUESTED"),
            bot.Review("alice", "MEMBER", "COMMENTED")])
        self.assertEqual(self.decide(data).action, bot.MANUAL)

    def test_changes_requested_then_approved(self):
        data = FakeData(review_list=[
            bot.Review("alice", "MEMBER", "CHANGES_REQUESTED"),
            bot.Review("alice", "MEMBER", "APPROVED")])
        self.assertEqual(self.decide(data).action, bot.MERGE)

    def test_changes_requested_then_dismissed(self):
        data = FakeData(review_list=[
            bot.Review("alice", "COLLABORATOR", "CHANGES_REQUESTED"),
            bot.Review("alice", "COLLABORATOR", "DISMISSED")])
        self.assertEqual(self.decide(data).action, bot.MERGE)

    def test_changes_requested_by_non_maintainer_ignored(self):
        data = FakeData(review_list=[
            bot.Review("mallory", "NONE", "CHANGES_REQUESTED"),
            bot.Review("bob", "CONTRIBUTOR", "CHANGES_REQUESTED")])
        self.assertEqual(self.decide(data).action, bot.MERGE)

    def test_failed_check(self):
        data = FakeData()
        data.check_list.append(
            bot.CheckRun(3, "check / Author", "completed", "failure"))
        decision = self.decide(data)
        self.assertEqual(decision.action, bot.MANUAL)
        self.assertEqual(decision.reasons, ["check failed: check / Author"])

    def test_cancelled_check(self):
        data = FakeData()
        data.check_list.append(
            bot.CheckRun(3, "native / all", "completed", "cancelled"))
        self.assertEqual(self.decide(data).action, bot.MANUAL)

    def test_skipped_and_neutral_checks_pass(self):
        data = FakeData()
        data.check_list += [
            bot.CheckRun(3, "a", "completed", "skipped"),
            bot.CheckRun(4, "b", "completed", "neutral")]
        self.assertEqual(self.decide(data).action, bot.MERGE)

    def test_running_check(self):
        data = FakeData()
        data.check_list.append(bot.CheckRun(3, "distro / all", "queued",
                                            None))
        self.assertEqual(self.decide(data).action, bot.PENDING)
        self.assertNotIn("annotations", data.asked)

    def test_no_checks_yet(self):
        self.assertEqual(self.decide(FakeData(check_list=[])).action,
                         bot.PENDING)

    def test_not_built(self):
        data = FakeData(annotations=[
            "skipped hello=2.1.0 (missing dependencies)"])
        decision = self.decide(data)
        self.assertEqual(decision.action, bot.MANUAL)
        self.assertEqual(decision.reasons,
                         ["hello=2.1.0 was not built on any platform"])

    def test_no_annotations(self):
        self.assertEqual(self.decide(FakeData(annotations=[])).action,
                         bot.MANUAL)

    def test_built_on_one_platform(self):
        data = FakeData(annotations=[
            "skipped hello=2.1.0 (unavailable)", "built hello=2.1.0"])
        self.assertEqual(self.decide(data).action, bot.MERGE)

    def test_fetched_counts_as_built(self):
        data = FakeData(result=eligible(("gnat_native", "15.0.0")),
                        annotations=["fetched gnat_native=15.0.0"])
        self.assertEqual(self.decide(data).action, bot.MERGE)

    def test_every_release_must_be_built(self):
        data = FakeData(result=eligible(("a", "1.0.0"), ("b", "1.0.0")),
                        annotations=["built a=1.0.0;skipped b=1.0.0 (x)"])
        decision = self.decide(data)
        self.assertEqual(decision.action, bot.MANUAL)
        self.assertEqual(decision.reasons,
                         ["b=1.0.0 was not built on any platform"])

    def test_cooling_off(self):
        started = NOW - bot.COOLING_OFF + timedelta(minutes=1)
        decision = self.decide(FakeData(started=started))
        self.assertEqual(decision.action, bot.PENDING)
        self.assertEqual(decision.merge_after, started + bot.COOLING_OFF)

    def test_cooled_off(self):
        decision = self.decide(FakeData(started=NOW - bot.COOLING_OFF))
        self.assertEqual(decision.action, bot.MERGE)

    def test_cooling_off_after_change(self):
        # Left draft, reopened or force-pushed
        changed = NOW - timedelta(minutes=10)
        decision = self.decide(FakeData(changed=changed))
        self.assertEqual(decision.action, bot.PENDING)
        self.assertEqual(decision.merge_after, changed + bot.COOLING_OFF)

    def test_cooled_off_after_change(self):
        decision = self.decide(FakeData(changed=NOW - bot.COOLING_OFF))
        self.assertEqual(decision.action, bot.MERGE)

    def test_cooling_off_after_opening_with_old_head(self):
        # The head was checked long ago (e.g. in a PR to another branch)
        opened = NOW - timedelta(minutes=5)
        decision = self.decide(created_at=opened)
        self.assertEqual(decision.action, bot.PENDING)
        self.assertEqual(decision.merge_after, opened + bot.COOLING_OFF)

    def test_withdrawal_skips_build_and_cooling_off(self):
        data = FakeData(result=eligible(("hello", "1.0.0"), kind=WITHDRAWALS),
                        annotations=[], started=NOW, recent=1000)
        self.assertEqual(self.decide(data).action, bot.MERGE)

    def test_rate_limit(self):
        data = FakeData(recent=bot.MAX_RELEASES_PER_DAY - 1,
                        result=eligible(("a", "1.0.0"), ("b", "1.0.0")),
                        annotations=["built a=1.0.0;built b=1.0.0"])
        decision = self.decide(data)
        self.assertEqual(decision.action, bot.MANUAL)
        self.assertIn("would exceed", decision.reasons[0])

    def test_rate_limit_reached_exactly(self):
        data = FakeData(recent=bot.MAX_RELEASES_PER_DAY - 1)
        self.assertEqual(self.decide(data).action, bot.MERGE)


class TestParsing(unittest.TestCase):

    def test_build_outcomes(self):
        self.assertEqual(
            bot.build_outcomes([
                "built a=1.0.0;skipped b=2.0.0 (missing dependencies)",
                "fetched b=2.0.0;skipped make (external)", ""]),
            {"a=1.0.0": {"built"}, "b=2.0.0": {"skipped", "fetched"},
             "make": {"skipped"}})

    def test_previous_decisions(self):
        def comment(login, body):
            return {"user": {"login": login}, "body": body}
        comments = [
            comment("bot[bot]", "<!-- automerge pr=1 sha=aaa action=pending"
                    " -->\ntext"),
            comment("bot[bot]", "<!-- automerge pr=1 sha=aaa action=merge"
                    " -->"),
            comment("mallory", "<!-- automerge pr=2 sha=bbb action=merge"
                    " -->"),
            comment("bot[bot]", "unrelated")]
        self.assertEqual(bot.previous_decisions(comments, "bot[bot]"),
                         {1: ("aaa", "merge")})

    def test_log_entry_does_not_notify(self):
        decision = bot.Decision(bot.MANUAL, ["reason"],
                                eligible(("hello", "2.1.0")))
        entry = bot.log_entry(pr(), decision, dry_run=True)
        self.assertNotIn("@", entry)
        self.assertNotIn("#7", entry)
        self.assertTrue(bot.MARKER_RE.search(entry))

    def test_pr_text_is_neutralized(self):
        title = "hi @mosteo, see #1 <!-- automerge pr=1 sha=a action=merge -->"
        decision = bot.Decision(bot.MANUAL, ["evil/@x.md: not a manifest"])
        entry = bot.log_entry(pr(title=title), decision, dry_run=True)
        self.assertNotIn("@", entry)
        self.assertNotIn("#1", entry)
        self.assertEqual(len(bot.MARKER_RE.findall(entry)), 1)
        self.assertNotIn("@", bot.pr_comment(pr(), decision))

    def test_pr_text_stays_on_one_line_without_links(self):
        text = bot.neutralize("a\n- fake reason\r\n[x](http://e) ![i](u)")
        self.assertNotIn("\n", text)
        self.assertNotIn("[", text)
        self.assertNotIn("]", text)

    def test_marker_round_trip(self):
        decision = bot.Decision(bot.PENDING, [])
        match = bot.MARKER_RE.search(bot.pr_comment(pr(), decision))
        self.assertEqual((match["pr"], match["sha"], match["action"]),
                         ("7", "abc123", bot.PENDING))


class FakeGitHub:
    """Records requests. Listings return the comments given, or the items
    given for the end of their path."""

    repo = "owner/index"

    def __init__(self, comments=None, listings=None):
        self.comments = comments or []
        self.listings = listings or {}
        self.requests = []

    def get_all(self, path, params=None, key=None):
        for end, items in self.listings.items():
            if path.endswith(end):
                return items
        return self.comments

    def request(self, method, path, body=None, params=None):
        self.requests.append((method, path, body))
        # Comments come back as posted by the bot
        return {"user": {"login": TestEffects.BOT}}


def comment(login, body, id=1):
    return {"id": id, "user": {"login": login}, "body": body}


class TestEffects(unittest.TestCase):

    BOT = "bot[bot]"

    def decision(self, action, *reasons):
        return bot.Decision(action, list(reasons),
                            eligible(("hello", "2.1.0")))

    def test_skip_does_nothing(self):
        gh = FakeGitHub()
        bot.Bot(gh, "on", self.BOT, None).apply(pr(), bot.Decision(
            bot.SKIP, ["draft"]))
        self.assertEqual(gh.requests, [])

    def test_skip_withdraws_pending(self):
        pending = comment(self.BOT, bot.pr_comment(pr(), self.decision(
            bot.PENDING)), id=5)
        gh = FakeGitHub([pending])
        skip = bot.Decision(bot.SKIP, ["conflicts with the base branch"])
        bot.Bot(gh, "on", self.BOT, None).apply(
            pr(labels=[bot.PENDING_LABEL]), skip)
        self.assertEqual([(m, p) for m, p, _ in gh.requests], [
            ("DELETE", "/repos/{repo}/issues/7/labels/auto-merge%3A%20pending"),
            ("PATCH", "/repos/{repo}/issues/comments/5")])
        self.assertIn("on hold", gh.requests[1][2]["body"])

    def test_skip_without_comment_posts_none(self):
        gh = FakeGitHub()
        bot.Bot(gh, "on", self.BOT, None).apply(
            pr(labels=[bot.PENDING_LABEL]),
            bot.Decision(bot.SKIP, ["draft"]))
        self.assertEqual([m for m, _, _ in gh.requests], ["DELETE"])

    def test_dry_run_logs_new_decision_only(self):
        manual = self.decision(bot.MANUAL, "reason")
        logged = comment(self.BOT, bot.log_entry(pr(), manual, True))
        gh = FakeGitHub([logged])
        runner = bot.Bot(gh, "dry-run", self.BOT, 99)

        runner.apply(pr(), manual)
        self.assertEqual(gh.requests, [], "same decision logged again")

        runner.apply(pr(head_sha="def456"), manual)
        runner.apply(pr(), self.decision(bot.MERGE))
        self.assertEqual([(m, p) for m, p, _ in gh.requests],
                         [("POST", "/repos/{repo}/issues/99/comments")] * 2)
        self.assertIn("would be merged", gh.requests[1][2]["body"])

    def test_wrong_identity_stops(self):
        gh = FakeGitHub()
        runner = bot.Bot(gh, "dry-run", "other[bot]", 99)
        with self.assertRaises(bot.WrongIdentity):
            runner.apply(pr(), self.decision(bot.MANUAL))
        self.assertEqual(len(gh.requests), 1)

    def test_pr_by_deleted_account(self):
        data = {"number": 3, "title": "t", "user": None,
                "head": {"sha": "abc"}, "draft": False, "labels": [],
                "created_at": "2026-10-28T10:00:00Z"}
        self.assertEqual(bot.pull_request(data).author, bot.DELETED_AUTHOR)

    def test_deleted_accounts_are_nobody(self):
        merge = self.decision(bot.MERGE)
        ghost = {"id": 1, "user": None,
                 "body": bot.log_entry(pr(), merge, True)}
        gh = FakeGitHub([ghost])
        bot.Bot(gh, "dry-run", self.BOT, 99).apply(pr(), merge)
        self.assertEqual(len(gh.requests), 1)

    def test_dry_run_ignores_comments_by_others(self):
        merge = self.decision(bot.MERGE)
        gh = FakeGitHub([comment("mallory", bot.log_entry(pr(), merge,
                                                          True))])
        bot.Bot(gh, "dry-run", self.BOT, 99).apply(pr(), merge)
        self.assertEqual(len(gh.requests), 1)

    def test_dry_run_never_touches_the_pr(self):
        gh = FakeGitHub()
        runner = bot.Bot(gh, "dry-run", self.BOT, 99)
        for action in (bot.MANUAL, bot.PENDING, bot.MERGE):
            runner.apply(pr(), self.decision(action))
        self.assertTrue(all(path == "/repos/{repo}/issues/99/comments"
                            for _, path, _ in gh.requests))

    def test_on_merges_evaluated_head(self):
        gh = FakeGitHub()
        runner = bot.Bot(gh, "on", self.BOT, None)
        merged = pr(labels=[bot.PENDING_LABEL])
        runner.merge(merged)
        runner.apply(merged, self.decision(bot.MERGE))
        self.assertEqual(gh.requests[0], (
            "PUT", "/repos/{repo}/pulls/7/merge",
            {"merge_method": "squash", "sha": "abc123"}))
        methods = [(m, p) for m, p, _ in gh.requests[1:]]
        self.assertEqual(methods, [
            ("DELETE", "/repos/{repo}/issues/7/labels/auto-merge%3A%20pending"),
            ("POST", "/repos/{repo}/issues/7/labels"),
            ("POST", "/repos/{repo}/issues/7/comments")])

    def test_on_updates_own_comment(self):
        manual = self.decision(bot.MANUAL, "new reason")
        old = comment(self.BOT, bot.pr_comment(pr(), self.decision(
            bot.PENDING)), id=5)
        gh = FakeGitHub([comment("mallory", "<!-- automerge pr=7 sha=x "
                                 "action=merge -->", id=4), old])
        bot.Bot(gh, "on", self.BOT, None).apply(
            pr(labels=[bot.MANUAL_LABEL]), manual)
        self.assertEqual(gh.requests, [(
            "PATCH", "/repos/{repo}/issues/comments/5",
            {"body": bot.pr_comment(pr(), manual)})])

    def test_on_leaves_unchanged_comment(self):
        pending = self.decision(bot.PENDING)
        gh = FakeGitHub([comment(self.BOT, bot.pr_comment(pr(), pending))])
        bot.Bot(gh, "on", self.BOT, None).apply(
            pr(labels=[bot.PENDING_LABEL]), pending)
        self.assertEqual(gh.requests, [])



def suite(status, conclusion, slug="github-actions"):
    return {"id": 9, "app": {"slug": slug}, "status": status,
            "conclusion": conclusion,
            "created_at": "2026-10-28T10:00:00Z"}


class TestLiveChecks(unittest.TestCase):

    RUN = {"id": 1, "name": "check / Author", "status": "completed",
           "conclusion": "success"}

    def checks(self, *suites):
        gh = FakeGitHub(listings={"/check-runs": [self.RUN],
                                  "/check-suites": list(suites)})
        return bot.LivePRData(gh, "", pr(), {}).checks()

    def test_completed_suites_add_nothing(self):
        self.assertEqual(len(self.checks(suite("completed", "success"))), 1)

    def test_queued_workflow_without_runs_is_pending(self):
        checks = self.checks(suite("queued", None))
        self.assertEqual(bot.decide(pr(), FakeData(check_list=checks),
                                    NOW).action, bot.PENDING)

    def test_workflows_awaiting_approval_fail(self):
        checks = self.checks(suite("completed", "action_required"))
        self.assertEqual(bot.decide(pr(), FakeData(check_list=checks),
                                    NOW).action, bot.MANUAL)

    def test_suites_of_other_apps_are_ignored(self):
        self.assertEqual(len(self.checks(suite("queued", None, "other"))), 1)


def pr_listing(number, author="bob"):
    return {"number": number, "title": f"PR {number}",
            "user": {"login": author}, "head": {"sha": f"sha{number}"},
            "draft": False, "labels": [],
            "created_at": "2026-10-27T10:00:00Z"}


class CountingData(FakeData):
    """FakeData whose rate limit counts the merges of the run."""

    def __init__(self, merged_in_run, releases=1):
        super().__init__(
            result=eligible(*[(f"c{i}", "1.0.0") for i in range(releases)]),
            annotations=[f"built c{i}=1.0.0" for i in range(releases)])
        self.merged_in_run = merged_in_run

    def recent_releases(self, author, since):
        return self.merged_in_run.get(author, 0)


class TestProcess(unittest.TestCase):

    def run_prs(self, prs, pr_data=None, mode="on", fetch=lambda: None,
                login=TestEffects.BOT):
        self.gh = FakeGitHub()
        runner = bot.Bot(self.gh, mode, login, 99)
        return bot.process(
            prs, pr_data or (lambda pr, merged: CountingData(merged)),
            runner, fetch, NOW)

    def merges(self):
        return [path for method, path, _ in self.gh.requests
                if method == "PUT"]

    def test_merges_all(self):
        rows, errors = self.run_prs([pr_listing(1), pr_listing(2)])
        self.assertEqual([row[2] for row in rows], [bot.MERGE, bot.MERGE])
        self.assertEqual(errors, 0)
        self.assertEqual(len(self.merges()), 2)

    def test_base_fetched_after_each_merge(self):
        fetches = []
        self.run_prs([pr_listing(1), pr_listing(2)],
                     fetch=lambda: fetches.append(1))
        self.assertEqual(len(fetches), 2)

    def test_stops_when_base_cannot_be_fetched(self):
        def fail():
            raise OSError("network")
        rows, errors = self.run_prs([pr_listing(1), pr_listing(2)],
                                    fetch=fail)
        self.assertEqual([row[2] for row in rows],
                         ["error", "not processed"])
        self.assertEqual(errors, 1)
        self.assertEqual(len(self.merges()), 1)

    def test_rate_limit_counts_merges_of_the_run(self):
        rows, _ = self.run_prs(
            [pr_listing(1), pr_listing(2), pr_listing(3, author="carol")],
            pr_data=lambda pr, merged: CountingData(merged, releases=6))
        self.assertEqual([row[2] for row in rows],
                         [bot.MERGE, bot.MANUAL, bot.MERGE])

    def test_errors_are_isolated(self):
        def pr_data(pr, merged):
            if pr.number == 1:
                raise KeyError("odd response")
            return CountingData(merged)
        rows, errors = self.run_prs([pr_listing(1), pr_listing(2)], pr_data)
        self.assertEqual([row[2] for row in rows], ["error", bot.MERGE])
        self.assertEqual(errors, 1)
        self.assertEqual(self.merges(), ["/repos/{repo}/pulls/2/merge"])

    def test_wrong_identity_stops_the_run(self):
        with self.assertRaises(bot.WrongIdentity):
            self.run_prs([pr_listing(1), pr_listing(2)], login="other[bot]")
        self.assertEqual(len(self.merges()), 1)

    def test_dry_run_never_merges(self):
        rows, _ = self.run_prs([pr_listing(1)], mode="dry-run")
        self.assertEqual(rows[0][2], bot.MERGE)
        self.assertEqual(self.merges(), [])

    def test_pr_by_deleted_account_is_not_merged(self):
        listing = pr_listing(1)
        listing["user"] = None
        rows, errors = self.run_prs(
            [listing], pr_data=lambda pr, merged: FakeData(result=Eligibility(
                False, None, [], [f"not a maintainer: {pr.author}"])))
        self.assertEqual(rows[0][1:3], (bot.DELETED_AUTHOR, bot.MANUAL))


if __name__ == "__main__":
    unittest.main()
