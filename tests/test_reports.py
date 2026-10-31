"""Test fleet health and access review reports without touching GitHub."""

import base64
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))

import access_review
import fleet_report
import github_api
from github_api import Unavailable

NOW = datetime(2026, 10, 25, 6, 0, tzinfo=UTC)
SPEC = {
    "defaults": {"required_checks": ["a", "b"]},
    "repos": {
        "one": {"description": "x"},
        "two": {"required_checks": ["c"]},
        "three": None,
    },
}


def fake_reader(responses):
    """Return a reader that serves canned responses by path prefix."""

    def read(path):
        for prefix, response in responses.items():
            if path.startswith(prefix):
                return response
        raise AssertionError(f"unexpected path {path}")

    return read


SHA = "9963dc1ffc49cff752cc62f2ec9c1651bdb3f482"
FULL_CALLERS = f"""
jobs:
  pr-title:
    uses: Abdel-Abdelaal/platform-workflows/.github/workflows/pr-title.yml@{SHA} # v1.6.0
  security:
    uses: Abdel-Abdelaal/platform-workflows/.github/workflows/security.yml@{SHA}
  conformance:
    uses: 'Abdel-Abdelaal/platform-workflows/.github/workflows/conformance.yml@{SHA}'
  review:
    uses: Abdel-Abdelaal/platform-workflows/.github/workflows/ai-review.yml@{SHA}
  scorecard:
    steps:
      - uses: ossf/scorecard-action@2d1146689b8cda280b9bc96326124645441f03bc # v2.4.4
"""
REQUIRED_TREE = [{"path": path, "type": "blob"} for path in fleet_report.REQUIRED_FILES]


def tree(*paths):
    """Return a recursive git tree holding the required files plus `paths`."""
    return {"tree": REQUIRED_TREE + [{"path": path, "type": "blob"} for path in paths]}


def content(text):
    return {"content": base64.b64encode(text.encode()).decode()}


def run(name, conclusion, event="schedule"):
    return {
        "name": name,
        "event": event,
        "conclusion": conclusion,
        "status": "completed",
        "html_url": f"https://example.test/{name}",
        "created_at": "2026-10-25T00:00:00Z",
    }


class FleetReportTests(unittest.TestCase):
    def test_repo_override_replaces_default_checks(self):
        self.assertEqual(
            fleet_report.desired_checks(SPEC),
            {"one": ["a", "b"], "two": ["c"], "three": ["a", "b"]},
        )

    def test_latest_run_per_workflow_wins(self):
        runs = fleet_report.latest_runs(
            [run("CI", "success"), run("CI", "failure"), run("Docs", None)]
        )
        self.assertEqual(
            [(r["name"], r["conclusion"]) for r in runs],
            [("CI", "success"), ("Docs", "completed")],
        )

    def test_collect_reports_check_drift_and_failures(self):
        rules = [
            {"type": "pull_request", "parameters": {}},
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "a"}, {"context": "z"}]
                },
            },
        ]
        issues = [
            {"title": "Drift detected", "html_url": "u"},
            {"title": "a PR", "html_url": "p", "pull_request": {}},
            {"title": "Fleet health", "html_url": "self"},
            {"title": "Dependency inventory", "html_url": "inventory"},
        ]
        pulls = [{"user": {"login": "dependabot[bot]"}}, {"user": {"login": "someone"}}]
        read = fake_reader(
            {
                "repos/Abdel-Abdelaal/one/rules": rules,
                "repos/Abdel-Abdelaal/one/actions": {
                    "workflow_runs": [run("CI", "failure")]
                },
                "repos/Abdel-Abdelaal/one/issues": issues,
                "repos/Abdel-Abdelaal/one/pulls": pulls,
                "repos/Abdel-Abdelaal/one/git/trees/main": tree(
                    ".github/workflows/ci.yml"
                ),
                "repos/Abdel-Abdelaal/one/contents/.github/workflows/ci.yml": content(
                    FULL_CALLERS
                ),
                "repos/Abdel-Abdelaal/one": {"default_branch": "main"},
            }
        )
        health = fleet_report.collect(
            "one", ["a", "b"], read=read, scorecard=lambda _: "6.4 (2026-10-25)"
        )
        self.assertEqual((health.missing_files, health.missing_callers), ([], []))
        self.assertEqual((health.missing_checks, health.extra_checks), (["b"], ["z"]))
        self.assertEqual(len(health.failed_runs), 1)
        self.assertEqual(len(health.governance_issues), 1)
        self.assertEqual((health.open_prs, health.dependabot_prs), (2, 1))
        body = fleet_report.render([health], NOW)
        self.assertIn(
            "| one | attention | 1 not applied, 1 undeclared | complete | 1 | 1 | 2 (1) "
            "| 6.4 (2026-10-25) |",
            body,
        )
        self.assertIn("Desired check not applied: `b`", body)

    def test_unreadable_endpoints_are_not_counted_as_healthy_data(self):
        denied = Unavailable("HTTP 403: not readable with this token")
        read = fake_reader(
            {
                "repos/Abdel-Abdelaal/one/": denied,
                "repos/Abdel-Abdelaal/one": {"default_branch": "main"},
            }
        )
        health = fleet_report.collect(
            "one", ["a"], read=read, scorecard=lambda _: "unavailable"
        )
        self.assertEqual(health.status, "incomplete")
        body = fleet_report.render([health], NOW)
        self.assertIn(
            "| one | incomplete | unreadable | unreadable | unreadable | unreadable "
            "| unreadable | unavailable |",
            body,
        )
        self.assertIn(
            "Could not read required checks; it is not counted as healthy.", body
        )

    def test_missing_files_and_callers_need_attention(self):
        partial = tree(".github/workflows/pr.yml", ".github/workflows/sub/x.yml")
        partial["tree"] = [
            item for item in partial["tree"] if item["path"] != "AGENTS.md"
        ]
        tag_pinned = FULL_CALLERS.replace(f"security.yml@{SHA}", "security.yml@v1")
        without_scorecard = "\n".join(
            line for line in tag_pinned.splitlines() if "ossf" not in line
        )
        read = fake_reader(
            {
                "repos/Abdel-Abdelaal/one/git/trees/main": partial,
                "repos/Abdel-Abdelaal/one/contents/.github/workflows/pr.yml": content(
                    without_scorecard
                ),
                "repos/Abdel-Abdelaal/one/rules": [],
                "repos/Abdel-Abdelaal/one/actions": {"workflow_runs": []},
                "repos/Abdel-Abdelaal/one/issues": [],
                "repos/Abdel-Abdelaal/one/pulls": [],
                "repos/Abdel-Abdelaal/one": {"default_branch": "main"},
            }
        )
        health = fleet_report.collect(
            "one", [], read=read, scorecard=lambda _: "unavailable"
        )
        self.assertEqual(health.missing_files, ["AGENTS.md"])
        self.assertEqual(health.missing_callers, ["security", "scorecard"])
        self.assertEqual(health.status, "attention")
        body = fleet_report.render([health], NOW)
        self.assertIn("| one | attention | match | missing 1 file, 2 callers |", body)
        self.assertIn("- Required file missing: `AGENTS.md`", body)
        self.assertIn(
            "- No `security.yml` platform-workflows caller pinned by full SHA", body
        )
        self.assertIn("- No `ossf/scorecard-action` step pinned by full SHA", body)

    def test_platform_workflows_may_call_itself_by_local_path(self):
        local = FULL_CALLERS.replace(
            f"Abdel-Abdelaal/platform-workflows/.github/workflows/pr-title.yml@{SHA}",
            "./.github/workflows/pr-title.yml",
        )
        self.assertEqual(
            fleet_report.missing_callers("platform-workflows", [local]), []
        )
        self.assertEqual(fleet_report.missing_callers("other", [local]), ["pr-title"])

    def test_unreadable_workflow_file_is_not_counted_as_complete(self):
        read = fake_reader(
            {
                "repos/Abdel-Abdelaal/one/git/trees/main": tree(
                    ".github/workflows/ci.yml"
                ),
                "repos/Abdel-Abdelaal/one/contents/": Unavailable(
                    "HTTP 403: not readable with this token"
                ),
                "repos/Abdel-Abdelaal/one/rules": [],
                "repos/Abdel-Abdelaal/one/actions": {"workflow_runs": []},
                "repos/Abdel-Abdelaal/one/issues": [],
                "repos/Abdel-Abdelaal/one/pulls": [],
                "repos/Abdel-Abdelaal/one": {"default_branch": "main"},
            }
        )
        health = fleet_report.collect(
            "one", [], read=read, scorecard=lambda _: "unavailable"
        )
        self.assertEqual(health.unreadable, ["workflows"])
        self.assertEqual(health.missing_callers, [])
        self.assertEqual(fleet_report.files_cell(health), "unreadable")
        self.assertEqual(health.status, "incomplete")
        self.assertIn(
            "- Could not read workflows; it is not counted as healthy.",
            fleet_report.render([health], NOW),
        )

    def test_empty_repository_files_are_unreadable_not_complete(self):
        empty = Unavailable("HTTP 409: the repository is empty")
        read = fake_reader(
            {
                "repos/Abdel-Abdelaal/one/git/trees": empty,
                "repos/Abdel-Abdelaal/one/rules": [],
                "repos/Abdel-Abdelaal/one/actions": {"workflow_runs": []},
                "repos/Abdel-Abdelaal/one/issues": [],
                "repos/Abdel-Abdelaal/one/pulls": [],
                "repos/Abdel-Abdelaal/one": {"default_branch": "main"},
            }
        )
        health = fleet_report.collect(
            "one", [], read=read, scorecard=lambda _: "unavailable"
        )
        self.assertEqual(health.status, "incomplete")
        self.assertIn(
            "| one | incomplete | match | unreadable |",
            fleet_report.render([health], NOW),
        )

    def test_public_repos_missing_from_repos_yaml_are_unmanaged(self):
        def repo(name, size=10, *, private=False, fork=False, archived=False):
            return {
                "name": name,
                "html_url": f"https://github.com/Abdel-Abdelaal/{name}",
                "created_at": "2026-10-25T09:00:00Z",
                "size": size,
                "private": private,
                "fork": fork,
                "archived": archived,
            }

        first_page = [repo(f"managed-{n}") for n in range(99)] + [repo("ello", 0)]
        second_page = [
            repo("one"),
            repo("secret", private=True),
            repo("forked", fork=True),
            repo("old", archived=True),
        ]
        pages = []

        def read(path):
            pages.append(path)
            return first_page if path.endswith("page=1") else second_page

        owned = fleet_report.owned_public_repos(read=read)
        self.assertEqual(len(pages), 2)
        managed = {"one"} | {f"managed-{n}" for n in range(99)}
        unmanaged = fleet_report.unmanaged_repos(owned, managed)
        self.assertEqual(
            unmanaged,
            [
                {
                    "name": "ello",
                    "url": "https://github.com/Abdel-Abdelaal/ello",
                    "created": "2026-10-25",
                    "content": "empty",
                }
            ],
        )
        body = fleet_report.render([], NOW, unmanaged)
        self.assertIn(
            "1 public repositories: 0 managed in `repos.yaml`, 1 unmanaged.", body
        )
        self.assertIn(
            "\n| ello | unmanaged | not in `repos.yaml` | not checked | not checked "
            "| not checked | not checked | not checked |\n",
            body,
        )
        header = next(line for line in body.splitlines() if line.startswith("| Repo"))
        row = next(line for line in body.splitlines() if line.startswith("| ello"))
        self.assertEqual(row.count("|"), header.count("|"))
        self.assertIn("standards/repo-structure.md#starting-a-new-repo", body)

    def test_unreadable_owner_fails_loudly(self):
        with self.assertRaises(fleet_report.UnreadableOwnerError):
            fleet_report.owned_public_repos(
                read=lambda _: Unavailable("HTTP 403: not readable with this token")
            )

    def test_unreadable_repository_fails_loudly(self):
        read = fake_reader(
            {
                "repos/Abdel-Abdelaal/one": Unavailable(
                    "HTTP 404: not readable with this token"
                )
            }
        )
        with self.assertRaises(fleet_report.UnreadableRepositoryError):
            fleet_report.collect(
                "one", [], read=read, scorecard=lambda _: "unavailable"
            )

    def test_unreadable_repository_keeps_the_rest_of_the_fleet(self):
        def fake(repo, desired):
            if repo == "two":
                raise fleet_report.UnreadableRepositoryError(
                    repo, Unavailable("HTTP 404: not readable with this token")
                )
            return fleet_report.RepoHealth(repo, missing_files=["README.md"])

        with patch.object(fleet_report, "collect", fake):
            healths = fleet_report.collect_fleet(SPEC)
        self.assertEqual([health.name for health in healths], ["one", "two", "three"])
        self.assertEqual(healths[1].unreadable, ["repository"])
        self.assertEqual(healths[1].status, "incomplete")
        body = fleet_report.render(healths, NOW)
        self.assertIn(
            "| two | incomplete | unreadable | unreadable | unreadable | unreadable "
            "| unreadable | unavailable |",
            body,
        )
        self.assertIn(
            "- Could not read repository; it is not counted as healthy.", body
        )
        self.assertIn(
            "3 public repositories: 3 managed in `repos.yaml`, 0 unmanaged.", body
        )


class AccessReviewTests(unittest.TestCase):
    def test_old_secret_gets_rotation_checkbox(self):
        secrets = {
            "secrets": [
                {"name": "OLD", "updated_at": "2026-06-01T00:00:00Z"},
                {"name": "NEW", "updated_at": "2026-10-20T00:00:00Z"},
            ]
        }
        lines = access_review.secret_lines(secrets, NOW)
        self.assertTrue(lines[0].startswith("- [ ] Rotate `OLD`"))
        self.assertEqual(lines[1], "- `NEW`: 5 days old")

    def test_unreadable_sections_are_skipped_not_guessed(self):
        denied = Unavailable("HTTP 403: not readable with this token")
        body = access_review.review(["one"], NOW, read=lambda _: denied)
        self.assertEqual(body.count("skipped: HTTP 403"), 4)
        self.assertNotIn("none", body)

    def test_bypass_actors_need_justification(self):
        read = fake_reader(
            {
                "repos/Abdel-Abdelaal/one/collaborators": [
                    {"login": "owner", "role_name": "admin"}
                ],
                "repos/Abdel-Abdelaal/one/keys": [],
                "repos/Abdel-Abdelaal/one/actions/secrets": {"secrets": []},
                "repos/Abdel-Abdelaal/one/rulesets/1": {
                    "bypass_actors": [{"actor_id": 5}]
                },
                "repos/Abdel-Abdelaal/one/rulesets/2": {"bypass_actors": []},
                "repos/Abdel-Abdelaal/one/rulesets": [
                    {"id": 1, "name": "loose"},
                    {"id": 2, "name": "protect-main"},
                ],
            }
        )
        body = access_review.review(["one"], NOW, read=read)
        self.assertIn("- Collaborators: `owner` (admin)", body)
        self.assertIn("- Deploy keys: none", body)
        self.assertIn("- [ ] Ruleset `loose` has 1 bypass actor(s)", body)
        self.assertIn("- Ruleset `protect-main`: no bypass actors", body)


class GitHubApiTests(unittest.TestCase):
    def test_forbidden_becomes_unavailable(self):
        error = subprocess.CalledProcessError(
            1, "gh", stderr="gh: Resource not accessible (HTTP 403)"
        )
        with patch.object(github_api, "gh", side_effect=error):
            self.assertIsInstance(
                github_api.api_or_unavailable("repos/x/y/keys"), Unavailable
            )

    def test_empty_repository_becomes_unavailable(self):
        error = subprocess.CalledProcessError(
            1, "gh", stderr="gh: Git Repository is empty. (HTTP 409)"
        )
        with patch.object(github_api, "gh", side_effect=error):
            self.assertEqual(
                github_api.api_or_unavailable("repos/x/y/git/trees/main"),
                Unavailable("HTTP 409: the repository is empty"),
            )
        error = subprocess.CalledProcessError(1, "gh", stderr="gh: Conflict (HTTP 409)")
        with patch.object(github_api, "gh", side_effect=error):
            self.assertEqual(
                github_api.api_or_unavailable("repos/x/y/merges"),
                Unavailable("HTTP 409: conflict"),
            )

    def test_other_errors_still_fail(self):
        error = subprocess.CalledProcessError(
            1, "gh", stderr="gh: Bad gateway (HTTP 502)"
        )
        with (
            patch.object(github_api, "gh", side_effect=error),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            github_api.api_or_unavailable("repos/x/y")

    def test_upsert_edits_matching_issue_only(self):
        with patch.object(
            github_api,
            "gh",
            side_effect=[
                '[{"number": 4, "title": "Fleet health old"}, {"number": 5, "title": "Fleet health"}]',
                "",
            ],
        ) as gh:
            github_api.upsert_issue("Fleet health", "body")
        self.assertEqual(gh.call_args.args[:3], ("issue", "edit", "5"))

    def test_upsert_creates_with_governance_label(self):
        with patch.object(github_api, "gh", side_effect=["[]", ""]) as gh:
            github_api.upsert_issue("Access review 2026-10", "body")
        self.assertEqual(
            gh.call_args.args[:4],
            ("issue", "create", "--title", "Access review 2026-10"),
        )
        self.assertIn("governance", gh.call_args.args)


if __name__ == "__main__":
    unittest.main()
