"""Keep one living inventory of every dependency and upstream project in the fleet.

GitHub's dependency graph (SBOM endpoint) supplies Actions, Python and npm
packages. Pre-commit hooks, Terraform providers and container base images are
read from their files. Each upstream project is then checked for archive status,
recent activity and newer releases, so abandoned or outdated dependencies surface
without anyone having to remember to look.
"""

import argparse
import base64
import json
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from github_api import OWNER, Unavailable, api_or_unavailable, upsert_issue

ISSUE_TITLE = "Dependency inventory"
STALE_AFTER = timedelta(days=365)
REGISTRY_TIMEOUT_SECONDS = 20
DIGEST_PINNED = "pinned by digest"
UNCOMPARABLE = {"unknown", "unreadable", "no releases", DIGEST_PINNED}
PURL = re.compile(r"^pkg:(?P<ecosystem>[a-z]+)/(?P<name>@?[^@]+)@(?P<version>.+)$")
HOOK = re.compile(
    r"- repo: https://github\.com/(?P<repo>[\w.-]+/[\w.-]+?)(?:\.git)?\s*\n"
    r"\s*rev: (?P<rev>\S+)"
)
PROVIDER = re.compile(
    r'provider "registry\.terraform\.io/(?P<name>[\w-]+/[\w-]+)" \{\s*'
    r'version\s*=\s*"(?P<version>[^"]+)"'
)
IMAGE = re.compile(r"^FROM\s+(?P<image>[^\s@]+)(?:@(?P<digest>\S+))?", re.MULTILINE)
FILES = {
    "pre-commit": ".pre-commit-config.yaml",
    "terraform": ".terraform.lock.hcl",
    "docker": "Dockerfile",
}


@dataclass(frozen=True)
class Dependency:
    """One pinned dependency as used by one repository."""

    ecosystem: str
    name: str
    version: str
    repo: str


@dataclass
class Upstream:
    """What is known about the project behind a dependency."""

    latest: str = "unknown"
    archived: bool = False
    last_push: str = ""
    stars: int | None = None
    latest_commit: str = ""

    def stale(self, now: datetime) -> bool:
        """No push for over a year: possibly unmaintained."""
        if not self.last_push:
            return False
        pushed = datetime.fromisoformat(self.last_push.replace("Z", "+00:00"))
        return now - pushed > STALE_AFTER


def from_sbom(repo: str, sbom: dict[str, Any]) -> list[Dependency]:
    """Read Actions, PyPI and npm packages from a GitHub SBOM response."""
    found = []
    for package in sbom["sbom"]["packages"]:
        for ref in package.get("externalRefs", []):
            match = PURL.match(urllib.parse.unquote(ref.get("referenceLocator", "")))
            if match and match["ecosystem"] in {"githubactions", "pypi", "npm"}:
                found.append(
                    Dependency(
                        match["ecosystem"], match["name"], match["version"], repo
                    )
                )
    return found


def from_files(repo: str, files: dict[str, str]) -> list[Dependency]:
    """Read hooks, Terraform providers and base images the SBOM does not cover."""
    found = []
    for match in HOOK.finditer(files.get("pre-commit", "")):
        found.append(Dependency("pre-commit", match["repo"], match["rev"], repo))
    for match in PROVIDER.finditer(files.get("terraform", "")):
        found.append(Dependency("terraform", match["name"], match["version"], repo))
    for match in IMAGE.finditer(files.get("docker", "")):
        version = match["digest"] or "unpinned"
        found.append(Dependency("docker", match["image"], version, repo))
    return found


def github_project(dependency: Dependency) -> str | None:
    """Map an action or hook to its GitHub owner/repo, if it has one."""
    if dependency.ecosystem in {"githubactions", "pre-commit"}:
        return "/".join(dependency.name.split("/")[:2])
    return None


def github_upstream(project: str, read: Callable[[str], Any]) -> Upstream:
    """Archive status, activity and newest release of a GitHub project."""
    info = read(f"repos/{project}")
    if isinstance(info, Unavailable):
        return Upstream(latest="unreadable")
    upstream = Upstream(
        archived=info["archived"],
        last_push=info["pushed_at"],
        stars=info["stargazers_count"],
    )
    release = read(f"repos/{project}/releases/latest")
    if isinstance(release, Unavailable):
        upstream.latest = "no releases"
        return upstream
    upstream.latest = release["tag_name"]
    commit = read(f"repos/{project}/commits/{release['tag_name']}")
    if not isinstance(commit, Unavailable):
        upstream.latest_commit = commit["sha"]
    return upstream


def registry_latest(ecosystem: str, name: str) -> str:
    """Newest version published to the package's public registry."""
    quoted = urllib.parse.quote(name, safe="/@")
    urls = {
        "pypi": (f"https://pypi.org/pypi/{quoted}/json", ("info", "version")),
        "npm": (f"https://registry.npmjs.org/{quoted}/latest", ("version",)),
        "terraform": (
            f"https://registry.terraform.io/v1/providers/{quoted}",
            ("version",),
        ),
    }
    if ecosystem not in urls:
        return "unknown"
    url, path = urls[ecosystem]
    try:
        # Fixed https registries only; names come from our own lockfiles.
        with urllib.request.urlopen(url, timeout=REGISTRY_TIMEOUT_SECONDS) as response:  # noqa: S310
            data = json.load(response)
    except (OSError, ValueError):
        return "unknown"
    for key in path:
        data = data[key]
    return str(data)


def is_current(dependency: Dependency, upstream: Upstream) -> bool | None:
    """Whether the pinned version is the newest release; None when unknown."""
    if upstream.latest in UNCOMPARABLE:
        return None
    if (
        dependency.ecosystem in {"githubactions", "pre-commit"}
        and upstream.latest_commit
    ):
        return dependency.version in {upstream.latest_commit, upstream.latest}
    return dependency.version.lstrip("v") == upstream.latest.lstrip("v")


def render(
    dependencies: Iterable[Dependency],
    upstreams: dict[tuple[str, str], Upstream],
    now: datetime,
) -> str:
    """Render the tracking issue body."""
    by_key: dict[tuple[str, str], list[Dependency]] = defaultdict(list)
    for dependency in dependencies:
        by_key[(dependency.ecosystem, dependency.name)].append(dependency)
    attention, behind = [], 0
    rows: dict[str, list[str]] = defaultdict(list)
    for (ecosystem, name), uses in sorted(by_key.items()):
        upstream = upstreams.get((ecosystem, name), Upstream())
        repos = ", ".join(sorted({use.repo for use in uses}))
        versions = ", ".join(sorted({use.version[:12] for use in uses}))
        current = [is_current(use, upstream) for use in uses]
        if any(state is False for state in current):
            behind += 1
            status = f"update available ({upstream.latest})"
        elif all(state is None for state in current):
            status = upstream.latest
        else:
            status = "current"
        if upstream.archived:
            attention.append(
                f"- `{name}` ({ecosystem}) is **archived**; used by {repos}"
            )
        elif upstream.stale(now):
            attention.append(
                f"- `{name}` ({ecosystem}) has had no push since "
                f"{upstream.last_push[:10]}; used by {repos}"
            )
        if ecosystem == "docker" and "unpinned" in versions:
            attention.append(f"- Base image `{name}` is not digest-pinned in {repos}")
        rows[ecosystem].append(f"| `{name}` | {versions} | {repos} | {status} |")
    lines = [
        "<!-- dependency-inventory -->",
        f"Generated {now:%Y-%m-%d %H:%M} UTC by the Dependency inventory workflow "
        "from GitHub's dependency graph plus hook, Terraform and Dockerfile pins.",
        "",
        f"**{len(by_key)}** distinct dependencies across "
        f"{len({d.repo for deps in by_key.values() for d in deps})} repos. "
        f"**{behind}** have a newer release; Dependabot proposes those after the "
        "14-day cooldown. **"
        f"{len(attention)}** need attention.",
        "",
        "## Needs attention",
        *(attention or ["- Nothing archived, abandoned or unpinned."]),
    ]
    for ecosystem in sorted(rows):
        lines += [
            "",
            f"<details><summary>{ecosystem} ({len(rows[ecosystem])})</summary>",
            "",
            "| Name | Pinned | Used by | Upstream |",
            "|---|---|---|---|",
            *rows[ecosystem],
            "",
            "</details>",
        ]
    return "\n".join(lines) + "\n"


def read_file(repo: str, path: str, read: Callable[[str], Any]) -> str:
    """Return a repository file's text, or an empty string if it does not exist."""
    content = read(f"repos/{OWNER}/{repo}/contents/{path}")
    if isinstance(content, Unavailable):
        return ""
    return base64.b64decode(content["content"]).decode("utf-8")


def collect(
    repos: list[str],
    read: Callable[[str], Any] = api_or_unavailable,
    latest: Callable[[str, str], str] = registry_latest,
) -> tuple[list[Dependency], dict[tuple[str, str], Upstream]]:
    """Gather every dependency and its upstream status."""
    dependencies: list[Dependency] = []
    for repo in repos:
        sbom = read(f"repos/{OWNER}/{repo}/dependency-graph/sbom")
        if not isinstance(sbom, Unavailable):
            dependencies += from_sbom(repo, sbom)
        files = {kind: read_file(repo, path, read) for kind, path in FILES.items()}
        dependencies += from_files(repo, files)
    upstreams: dict[tuple[str, str], Upstream] = {}
    projects: dict[str, Upstream] = {}
    for dependency in dependencies:
        key = (dependency.ecosystem, dependency.name)
        if key in upstreams:
            continue
        # Our own platform workflows are checked like any other upstream, so a
        # caller left on an older platform release shows as "update available".
        project = github_project(dependency)
        if project:
            if project not in projects:
                projects[project] = github_upstream(project, read)
            upstreams[key] = projects[project]
        elif dependency.ecosystem == "docker":
            # Dependabot's docker ecosystem tracks newer digests.
            upstreams[key] = Upstream(latest=DIGEST_PINNED)
        else:
            upstreams[key] = Upstream(
                latest=latest(dependency.ecosystem, dependency.name)
            )
    return dependencies, upstreams


def main() -> None:
    """Build the inventory, or publish a built inventory."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--repos", type=Path, required=True, help="repos.yaml as JSON")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--json", type=Path, required=True, help="machine-readable copy")
    publish = commands.add_parser("publish")
    publish.add_argument("--body", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command == "build":
        spec = json.loads(arguments.repos.read_text(encoding="utf-8"))
        dependencies, upstreams = collect(list(spec["repos"]))
        now = datetime.now(UTC)
        arguments.output.write_text(
            render(dependencies, upstreams, now), encoding="utf-8"
        )
        record = {
            "generated_at": now.isoformat(),
            "dependencies": [asdict(dependency) for dependency in dependencies],
            "upstreams": {f"{e}:{n}": asdict(u) for (e, n), u in upstreams.items()},
        }
        arguments.json.write_text(json.dumps(record, indent=2), encoding="utf-8")
    else:
        upsert_issue(ISSUE_TITLE, arguments.body.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
