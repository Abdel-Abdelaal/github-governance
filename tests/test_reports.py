"""Test fleet health and access review reports without touching GitHub."""

import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))

import access_review  # noqa: E402
import fleet_report  # noqa: E402
import github_api  # noqa: E402
from github_api import Unavailable  # noqa: E402

NOW = datetime(2026, 10, 25, 6, 0, tzinfo=UTC)
SPEC = {"defaults": {"required_checks": ["a", "b"]},
        "repos": {"one": {"description": "x"}, "two": {"required_checks": ["c"]}, "three": None}}


def fake_reader(responses):
    """Return a reader that serves canned responses by path prefix."""
    def read(path):
        for prefix, response in responses.items():
            if path.startswith(prefix):
                return response
        raise AssertionError(f"unexpected path {path}")
    return read


def run(name, conclusion, event="schedule"):
    return {"name": name, "event": event, "conclusion": conclusion, "status": "completed",
            "html_url": f"https://example.test/{name}", "created_at": "2026-10-25T00:00:00Z"}


class FleetReportTests(unittest.TestCase):
    def test_repo_override_replaces_default_checks(self):
        self.assertEqual(fleet_report.desired_checks(SPEC), {"one": ["a", "b"], "two": ["c"], "three": ["a", "b"]})

    def test_latest_run_per_workflow_wins(self):
        runs = fleet_report.latest_runs([run("CI", "success"), run("CI", "failure"), run("Docs", None)])
        self.assertEqual([(r["name"], r["conclusion"]) for r in runs], [("CI", "success"), ("Docs", "completed")])

    def test_collect_reports_check_drift_and_failures(self):
        rules = [{"type": "pull_request", "parameters": {}},
                 {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "a"}, {"context": "z"}]}}]
        issues = [{"title": "Drift detected", "html_url": "u"}, {"title": "a PR", "html_url": "p", "pull_request": {}},
                  {"title": "Fleet health", "html_url": "self"}]
        pulls = [{"user": {"login": "dependabot[bot]"}}, {"user": {"login": "someone"}}]
        read = fake_reader({
            "repos/Abdel-Abdelaal/one/rules": rules,
            "repos/Abdel-Abdelaal/one/actions": {"workflow_runs": [run("CI", "failure")]},
            "repos/Abdel-Abdelaal/one/issues": issues,
            "repos/Abdel-Abdelaal/one/pulls": pulls,
            "repos/Abdel-Abdelaal/one": {"default_branch": "main"},
        })
        health = fleet_report.collect("one", ["a", "b"], read=read, scorecard=lambda _: "6.4 (2026-10-25)")
        self.assertEqual((health.missing_checks, health.extra_checks), (["b"], ["z"]))
        self.assertEqual(len(health.failed_runs), 1)
        self.assertEqual(len(health.governance_issues), 1)
        self.assertEqual((health.open_prs, health.dependabot_prs), (2, 1))
        body = fleet_report.render([health], NOW)
        self.assertIn("| one | attention | 1 not applied, 1 undeclared | 1 | 1 | 2 (1) | 6.4 (2026-10-25) |", body)
        self.assertIn("Desired check not applied: `b`", body)

    def test_unreadable_endpoints_are_not_counted_as_healthy_data(self):
        denied = Unavailable("HTTP 403: not readable with this token")
        read = fake_reader({"repos/Abdel-Abdelaal/one/": denied, "repos/Abdel-Abdelaal/one": {"default_branch": "main"}})
        health = fleet_report.collect("one", ["a"], read=read, scorecard=lambda _: "unavailable")
        self.assertEqual(health.status, "incomplete")
        body = fleet_report.render([health], NOW)
        self.assertIn("| one | incomplete | unreadable | unreadable | unreadable | unreadable | unavailable |", body)
        self.assertIn("Could not read required checks; it is not counted as healthy.", body)

    def test_unreadable_repository_fails_loudly(self):
        read = fake_reader({"repos/Abdel-Abdelaal/one": Unavailable("HTTP 404: not readable with this token")})
        with self.assertRaises(RuntimeError):
            fleet_report.collect("one", [], read=read, scorecard=lambda _: "unavailable")


class AccessReviewTests(unittest.TestCase):
    def test_old_secret_gets_rotation_checkbox(self):
        secrets = {"secrets": [{"name": "OLD", "updated_at": "2026-06-01T00:00:00Z"},
                               {"name": "NEW", "updated_at": "2026-10-20T00:00:00Z"}]}
        lines = access_review.secret_lines(secrets, NOW)
        self.assertTrue(lines[0].startswith("- [ ] Rotate `OLD`"))
        self.assertEqual(lines[1], "- `NEW`: 5 days old")

    def test_unreadable_sections_are_skipped_not_guessed(self):
        denied = Unavailable("HTTP 403: not readable with this token")
        body = access_review.review(["one"], NOW, read=lambda _: denied)
        self.assertEqual(body.count("skipped: HTTP 403"), 4)
        self.assertNotIn("none", body)

    def test_bypass_actors_need_justification(self):
        read = fake_reader({
            "repos/Abdel-Abdelaal/one/collaborators": [{"login": "owner", "role_name": "admin"}],
            "repos/Abdel-Abdelaal/one/keys": [],
            "repos/Abdel-Abdelaal/one/actions/secrets": {"secrets": []},
            "repos/Abdel-Abdelaal/one/rulesets/1": {"bypass_actors": [{"actor_id": 5}]},
            "repos/Abdel-Abdelaal/one/rulesets/2": {"bypass_actors": []},
            "repos/Abdel-Abdelaal/one/rulesets": [{"id": 1, "name": "loose"}, {"id": 2, "name": "protect-main"}],
        })
        body = access_review.review(["one"], NOW, read=read)
        self.assertIn("- Collaborators: `owner` (admin)", body)
        self.assertIn("- Deploy keys: none", body)
        self.assertIn("- [ ] Ruleset `loose` has 1 bypass actor(s)", body)
        self.assertIn("- Ruleset `protect-main`: no bypass actors", body)


class GitHubApiTests(unittest.TestCase):
    def test_forbidden_becomes_unavailable(self):
        error = subprocess.CalledProcessError(1, "gh", stderr="gh: Resource not accessible (HTTP 403)")
        with patch.object(github_api, "gh", side_effect=error):
            self.assertIsInstance(github_api.api_or_unavailable("repos/x/y/keys"), Unavailable)

    def test_other_errors_still_fail(self):
        error = subprocess.CalledProcessError(1, "gh", stderr="gh: Bad gateway (HTTP 502)")
        with patch.object(github_api, "gh", side_effect=error), self.assertRaises(subprocess.CalledProcessError):
            github_api.api_or_unavailable("repos/x/y")

    def test_upsert_edits_matching_issue_only(self):
        with patch.object(github_api, "gh", side_effect=['[{"number": 4, "title": "Fleet health old"}, {"number": 5, "title": "Fleet health"}]', ""]) as gh:
            github_api.upsert_issue("Fleet health", "body")
        self.assertEqual(gh.call_args.args[:3], ("issue", "edit", "5"))

    def test_upsert_creates_with_governance_label(self):
        with patch.object(github_api, "gh", side_effect=["[]", ""]) as gh:
            github_api.upsert_issue("Access review 2026-10", "body")
        self.assertEqual(gh.call_args.args[:4], ("issue", "create", "--title", "Access review 2026-10"))
        self.assertIn("governance", gh.call_args.args)


if __name__ == "__main__":
    unittest.main()
