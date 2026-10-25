"""Summarise fleet health from public GitHub data into one tracking issue."""

import argparse
import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from github_api import OWNER, Unavailable, api_or_unavailable, upsert_issue

ISSUE_TITLE = "Fleet health"
SCORECARD_URL = "https://api.scorecard.dev/projects/github.com/{owner}/{repo}"
FAILED_CONCLUSIONS = {"failure", "timed_out", "startup_failure", "action_required"}
DEPENDABOT_LOGIN = "dependabot[bot]"


class UnreadableRepositoryError(Exception):
    """A managed repository could not be read at all, so no report is possible."""

    def __init__(self, repo: str, unavailable: Unavailable) -> None:
        """Name the repository and the reason."""
        super().__init__(f"{repo} is managed but not readable: {unavailable.reason}")


@dataclass
class RepoHealth:
    """Everything the report says about one repository."""

    name: str
    missing_checks: list[str] = field(default_factory=list)
    extra_checks: list[str] = field(default_factory=list)
    runs: list[dict[str, str]] = field(default_factory=list)
    governance_issues: list[dict[str, str]] = field(default_factory=list)
    open_prs: int = 0
    dependabot_prs: int = 0
    scorecard: str = "unavailable"
    unreadable: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        """`attention` beats `incomplete`, which beats `ok`."""
        if self.needs_attention:
            return "attention"
        return "incomplete" if self.unreadable else "ok"

    @property
    def failed_runs(self) -> list[dict[str, str]]:
        """Latest runs per workflow that did not succeed."""
        return [run for run in self.runs if run["conclusion"] in FAILED_CONCLUSIONS]

    @property
    def needs_attention(self) -> bool:
        """Whether a human should look at this repository."""
        return bool(
            self.missing_checks
            or self.extra_checks
            or self.failed_runs
            or self.governance_issues
        )


def desired_checks(spec: dict[str, Any]) -> dict[str, list[str]]:
    """Map each managed repository to its declared required checks."""
    default = spec["defaults"].get("required_checks", [])
    return {
        name: (settings or {}).get("required_checks", default)
        for name, settings in spec["repos"].items()
    }


def live_checks(rules: list[dict[str, Any]]) -> list[str]:
    """Extract required check names from a branch rules response."""
    return [
        check["context"]
        for rule in rules
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    ]


def latest_runs(runs: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Keep the newest run of each workflow, assuming newest-first API order."""
    latest: dict[str, dict[str, str]] = {}
    for run in runs:
        name = run["name"]
        if name not in latest:
            latest[name] = {
                "name": name,
                "event": run["event"],
                "conclusion": run["conclusion"] or run["status"],
                "url": run["html_url"],
                "created_at": run["created_at"],
            }
    return sorted(latest.values(), key=lambda run: run["name"])


def fetch_scorecard(repo: str) -> str:
    """Return the public OpenSSF Scorecard score and date, or 'unavailable'."""
    url = SCORECARD_URL.format(owner=OWNER, repo=repo)
    try:
        with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - fixed https URL
            result = json.load(response)
    except (OSError, ValueError):
        return "unavailable"
    return f"{result['score']} ({result['date'][:10]})"


def collect(
    repo: str,
    desired: list[str],
    read: Callable[[str], Any] = api_or_unavailable,
    scorecard: Callable[[str], str] = fetch_scorecard,
) -> RepoHealth:
    """Gather one repository's health from public endpoints."""
    health = RepoHealth(repo, scorecard=scorecard(repo))
    base = f"repos/{OWNER}/{repo}"
    info = read(base)
    if isinstance(info, Unavailable):
        raise UnreadableRepositoryError(repo, info)
    branch = info["default_branch"]
    sections = {
        "required checks": f"{base}/rules/branches/{branch}",
        "workflow runs": f"{base}/actions/runs?branch={branch}&per_page=100"
        "&exclude_pull_requests=true",
        "governance issues": f"{base}/issues?labels=governance&state=open&per_page=100",
        "pull requests": f"{base}/pulls?state=open&per_page=100",
    }
    data = {}
    for section, path in sections.items():
        response = read(path)
        if isinstance(response, Unavailable):
            health.unreadable.append(section)
        else:
            data[section] = response
    if "required checks" in data:
        live = live_checks(data["required checks"])
        health.missing_checks = sorted(set(desired) - set(live))
        health.extra_checks = sorted(set(live) - set(desired))
    if "workflow runs" in data:
        health.runs = latest_runs(data["workflow runs"]["workflow_runs"])
    if "governance issues" in data:
        # The report's own tracking issue is not a finding about the fleet.
        health.governance_issues = [
            {"title": issue["title"], "url": issue["html_url"]}
            for issue in data["governance issues"]
            if "pull_request" not in issue and issue["title"] != ISSUE_TITLE
        ]
    if "pull requests" in data:
        pulls = data["pull requests"]
        health.open_prs = len(pulls)
        health.dependabot_prs = sum(
            pull["user"]["login"] == DEPENDABOT_LOGIN for pull in pulls
        )
    return health


def cell(health: RepoHealth, section: str, value: object) -> str:
    """Show a value only when its section was actually read."""
    return "unreadable" if section in health.unreadable else str(value)


def render(healths: list[RepoHealth], generated_at: datetime) -> str:
    """Render the tracking issue body."""
    lines = [
        "<!-- fleet-health -->",
        f"Generated {generated_at:%Y-%m-%d %H:%M} UTC by the Fleet health workflow "
        "from public GitHub data. Admin-only data (secrets, alerts, installations) "
        "is in the monthly access review.",
        "",
        "| Repo | Status | Required checks vs desired | Failed latest runs "
        "| Governance issues | Open PRs (Dependabot) | Scorecard |",
        "|---|---|---|---|---|---|---|",
    ]
    for health in healths:
        drift = "match"
        if health.missing_checks or health.extra_checks:
            drift = (
                f"{len(health.missing_checks)} not applied, "
                f"{len(health.extra_checks)} undeclared"
            )
        pulls = f"{health.open_prs} ({health.dependabot_prs})"
        cells = [
            health.name,
            health.status,
            cell(health, "required checks", drift),
            cell(health, "workflow runs", len(health.failed_runs)),
            cell(health, "governance issues", len(health.governance_issues)),
            cell(health, "pull requests", pulls),
            health.scorecard,
        ]
        lines.append("| " + " | ".join(cells) + " |")
    for health in healths:
        if health.status == "ok":
            continue
        lines += ["", f"## {health.name}"]
        lines += [
            f"- Could not read {section}; it is not counted as healthy."
            for section in health.unreadable
        ]
        lines += [
            f"- Desired check not applied: `{check}`" for check in health.missing_checks
        ]
        lines += [
            f"- Live check not declared: `{check}`" for check in health.extra_checks
        ]
        lines += [
            f"- Latest `{run['name']}` ({run['event']}) is **{run['conclusion']}**: "
            f"{run['url']}"
            for run in health.failed_runs
        ]
        lines += [
            f"- Open governance issue: [{issue['title']}]({issue['url']})"
            for issue in health.governance_issues
        ]
    lines += [
        "",
        "Desired checks come from `repos.yaml`. A check listed as not applied "
        "is desired state waiting for a Terraform apply, not a bypass.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    """Build the report, or publish a built report."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument(
        "--repos", type=Path, required=True, help="repos.yaml converted to JSON"
    )
    build.add_argument("--output", type=Path, required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--body", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command == "build":
        spec = json.loads(arguments.repos.read_text(encoding="utf-8"))
        healths = [
            collect(repo, checks) for repo, checks in desired_checks(spec).items()
        ]
        arguments.output.write_text(
            render(healths, datetime.now(UTC)), encoding="utf-8"
        )
    else:
        upsert_issue(ISSUE_TITLE, arguments.body.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
