"""Summarise fleet health from public GitHub data into one tracking issue."""

import argparse
import base64
import json
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from github_api import OWNER, Unavailable, api_or_unavailable, upsert_issue

ISSUE_TITLE = "Fleet health"
# Report issues only restate data. Issues with to-dos (access review checkboxes,
# AI health reports, drift) still count as findings.
REPORT_TITLES = frozenset({ISSUE_TITLE, "Dependency inventory"})
SCORECARD_URL = "https://api.scorecard.dev/projects/github.com/{owner}/{repo}"
FAILED_CONCLUSIONS = {"failure", "timed_out", "startup_failure", "action_required"}
DEPENDABOT_LOGIN = "dependabot[bot]"
ONBOARDING_URL = (
    f"https://github.com/{OWNER}/.github/blob/main/standards/repo-structure.md"
    "#starting-a-new-repo"
)
PAGE_SIZE = 100
# The handbook's "Files every repo has", as platform-workflows conformance enforces.
REQUIRED_FILES = (
    "README.md",
    "LICENSE",
    ".gitattributes",
    "AGENTS.md",
    "CLAUDE.md",
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
    "opencode.json",
)
# Reusable platform workflows every repo calls, as in the new-repo starter.
PLATFORM_CALLERS = ("pr-title", "security", "conformance", "ai-review")
SCORECARD_CALLER = "scorecard"
FULL_SHA = "[0-9a-f]{40}"
WORKFLOW_FILE = re.compile(r"\.github/workflows/[^/]+\.ya?ml")


class UnreadableOwnerError(Exception):
    """The owner's repository list could not be read, so unmanaged repos are unknown."""

    def __init__(self, unavailable: Unavailable) -> None:
        """Name the reason."""
        super().__init__(
            f"{OWNER}'s repositories are not readable: {unavailable.reason}"
        )


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
    missing_files: list[str] = field(default_factory=list)
    missing_callers: list[str] = field(default_factory=list)
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
            or self.missing_files
            or self.missing_callers
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


def caller_pattern(repo: str, caller: str) -> re.Pattern[str]:
    """Match a `uses:` line that calls a required workflow, pinned by full SHA.

    platform-workflows calls its own reusable workflows by local path instead.
    """
    if caller == SCORECARD_CALLER:
        target = f"ossf/scorecard-action@{FULL_SHA}"
    else:
        workflow = rf"\.github/workflows/{re.escape(caller)}\.yml"
        target = f"{OWNER}/platform-workflows/{workflow}@{FULL_SHA}"
        if repo == "platform-workflows":
            target = rf"(?:{target}|\./{workflow})"
    return re.compile(rf"^\s*(?:-\s*)?uses:\s*['\"]?{target}\b", re.MULTILINE)


def missing_callers(repo: str, workflows: list[str]) -> list[str]:
    """Required callers that no workflow file contains."""
    text = "\n".join(workflows)
    return [
        caller
        for caller in (*PLATFORM_CALLERS, SCORECARD_CALLER)
        if not caller_pattern(repo, caller).search(text)
    ]


def workflow_paths(tree: dict[str, Any]) -> list[str]:
    """Workflow files directly under `.github/workflows/`."""
    return sorted(
        item["path"]
        for item in tree["tree"]
        if item["type"] == "blob" and WORKFLOW_FILE.fullmatch(item["path"])
    )


def owned_public_repos(
    read: Callable[[str], Any] = api_or_unavailable,
) -> list[dict[str, Any]]:
    """List the owner's public repositories that could join the fleet.

    Forks and archived repositories are not expected to be managed.
    """
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = read(f"users/{OWNER}/repos?type=owner&per_page={PAGE_SIZE}&page={page}")
        if isinstance(batch, Unavailable):
            raise UnreadableOwnerError(batch)
        repos += batch
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return sorted(
        (
            repo
            for repo in repos
            if not repo["private"] and not repo["fork"] and not repo["archived"]
        ),
        key=lambda repo: repo["name"],
    )


def unmanaged_repos(
    owned: list[dict[str, Any]], managed: set[str]
) -> list[dict[str, str]]:
    """Public repositories that `repos.yaml` does not declare."""
    return [
        {
            "name": repo["name"],
            "url": repo["html_url"],
            "created": repo["created_at"][:10],
            "content": "empty" if repo["size"] == 0 else "has content",
        }
        for repo in owned
        if repo["name"] not in managed
    ]


def fetch_scorecard(repo: str) -> str:
    """Return the public OpenSSF Scorecard score and date, or 'unavailable'."""
    url = SCORECARD_URL.format(owner=OWNER, repo=repo)
    try:
        with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - fixed https URL
            result = json.load(response)
    except (OSError, ValueError):
        return "unavailable"
    return f"{result['score']} ({result['date'][:10]})"


def check_files(
    health: RepoHealth, tree: dict[str, Any], read: Callable[[str], Any], ref: str
) -> None:
    """Record missing required files and platform-workflows callers."""
    paths = {item["path"] for item in tree["tree"]}
    health.missing_files = [path for path in REQUIRED_FILES if path not in paths]
    workflows = []
    for path in workflow_paths(tree):
        response = read(f"repos/{OWNER}/{health.name}/contents/{path}?ref={ref}")
        if isinstance(response, Unavailable):
            health.unreadable.append("workflows")
            return
        workflows.append(base64.b64decode(response["content"]).decode("utf-8"))
    health.missing_callers = missing_callers(health.name, workflows)


def collect_fleet(spec: dict[str, Any]) -> list[RepoHealth]:
    """Collect one health row per managed repository in the spec.

    A repository that cannot be read at all is reported as unreadable
    instead of aborting the report; other errors still propagate.
    """
    healths: list[RepoHealth] = []
    for repo, checks in desired_checks(spec).items():
        try:
            healths.append(collect(repo, checks))
        except UnreadableRepositoryError:
            healths.append(RepoHealth(repo, unreadable=["repository"]))
    return healths


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
        "files": f"{base}/git/trees/{branch}?recursive=1",
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
    if "files" in data:
        check_files(health, data["files"], read, branch)
    if "workflow runs" in data:
        health.runs = latest_runs(data["workflow runs"]["workflow_runs"])
    if "governance issues" in data:
        # Report issues are not findings about the fleet.
        health.governance_issues = [
            {"title": issue["title"], "url": issue["html_url"]}
            for issue in data["governance issues"]
            if "pull_request" not in issue and issue["title"] not in REPORT_TITLES
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
    if "repository" in health.unreadable or section in health.unreadable:
        return "unreadable"
    return str(value)


def files_cell(health: RepoHealth) -> str:
    """Summarise required files and callers, when both were read."""
    if {"repository", "files", "workflows"} & set(health.unreadable):
        return "unreadable"
    if not health.missing_files and not health.missing_callers:
        return "complete"
    files, callers = len(health.missing_files), len(health.missing_callers)
    return (
        f"missing {files} file{'' if files == 1 else 's'}, "
        f"{callers} caller{'' if callers == 1 else 's'}"
    )


def unmanaged_lines(repo: dict[str, str]) -> list[str]:
    """Explain one unmanaged repository and how to onboard it."""
    return [
        "",
        f"## {repo['name']}",
        f"- Unmanaged: [{repo['name']}]({repo['url']}) is public "
        f"({repo['content']}, created {repo['created']}) but not in `repos.yaml`, "
        "so nothing governs its settings, rulesets or labels.",
        f"- Onboard it with the [new-repo starter]({ONBOARDING_URL}), "
        "then add it to `repos.yaml` by PR.",
    ]


def render(
    healths: list[RepoHealth],
    generated_at: datetime,
    unmanaged: list[dict[str, str]] | None = None,
) -> str:
    """Render the tracking issue body."""
    unmanaged = unmanaged or []
    lines = [
        "<!-- fleet-health -->",
        f"Generated {generated_at:%Y-%m-%d %H:%M} UTC by the Fleet health workflow "
        "from public GitHub data. Admin-only data (secrets, alerts, installations) "
        "is in the monthly access review.",
        "",
        f"{len(healths) + len(unmanaged)} public repositories: {len(healths)} "
        f"managed in `repos.yaml`, {len(unmanaged)} unmanaged.",
        "",
        "| Repo | Status | Required checks vs desired | Required files and callers "
        "| Failed latest runs | Governance issues | Open PRs (Dependabot) "
        "| Scorecard |",
        "|---|---|---|---|---|---|---|---|",
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
            files_cell(health),
            cell(health, "workflow runs", len(health.failed_runs)),
            cell(health, "governance issues", len(health.governance_issues)),
            cell(health, "pull requests", pulls),
            health.scorecard,
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        f"| {repo['name']} | unmanaged | not in `repos.yaml` | not checked "
        "| not checked | not checked | not checked | not checked |"
        for repo in unmanaged
    ]
    for repo in unmanaged:
        lines += unmanaged_lines(repo)
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
        lines += [f"- Required file missing: `{path}`" for path in health.missing_files]
        lines += [
            "- No `ossf/scorecard-action` step pinned by full SHA"
            if caller == SCORECARD_CALLER
            else f"- No `{caller}.yml` platform-workflows caller pinned by full SHA"
            for caller in health.missing_callers
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
        "is desired state waiting for a Terraform apply, not a bypass. Required "
        "files and callers follow the handbook's repo structure standard. Forks "
        "and archived repositories are not expected to be managed.",
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
        healths = collect_fleet(spec)
        unmanaged = unmanaged_repos(owned_public_repos(), set(spec["repos"]))
        arguments.output.write_text(
            render(healths, datetime.now(UTC), unmanaged), encoding="utf-8"
        )
    else:
        upsert_issue(ISSUE_TITLE, arguments.body.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
