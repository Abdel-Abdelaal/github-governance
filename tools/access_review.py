"""Record who and what can change each managed repository, once a month."""

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from github_api import OWNER, Unavailable, api_or_unavailable, upsert_issue

SECRET_MAX_AGE = timedelta(days=90)
SKIPPED = "skipped: {reason}. Configure the governance App (docs/setup.md) to review this."


def secret_lines(secrets: dict[str, Any] | Unavailable, now: datetime) -> list[str]:
    """List secret names and ages, with a rotation checkbox past the maximum age."""
    if isinstance(secrets, Unavailable):
        return [f"- Actions secrets: {SKIPPED.format(reason=secrets.reason)}"]
    lines = []
    for secret in secrets["secrets"]:
        updated = datetime.fromisoformat(secret["updated_at"].replace("Z", "+00:00"))
        age = (now - updated).days
        if now - updated > SECRET_MAX_AGE:
            lines.append(f"- [ ] Rotate `{secret['name']}` ({age} days old)")
        else:
            lines.append(f"- `{secret['name']}`: {age} days old")
    return lines or ["- Actions secrets: none"]


def named_lines(label: str, items: list[dict[str, Any]] | Unavailable,
                describe: Callable[[dict[str, Any]], str]) -> list[str]:
    """List items with one formatter, or record why they could not be read."""
    if isinstance(items, Unavailable):
        return [f"- {label}: {SKIPPED.format(reason=items.reason)}"]
    if not items:
        return [f"- {label}: none"]
    return [f"- {label}: " + ", ".join(describe(item) for item in items)]


def bypass_lines(repo: str, read: Callable[[str], Any]) -> list[str]:
    """Report bypass actors for every ruleset; any bypass needs a written reason."""
    rulesets = read(f"repos/{OWNER}/{repo}/rulesets")
    if isinstance(rulesets, Unavailable):
        return [f"- Ruleset bypass actors: {SKIPPED.format(reason=rulesets.reason)}"]
    lines = []
    for ruleset in rulesets:
        detail = read(f"repos/{OWNER}/{repo}/rulesets/{ruleset['id']}")
        if isinstance(detail, Unavailable) or "bypass_actors" not in detail:
            lines.append(f"- Ruleset `{ruleset['name']}` bypass actors: "
                         + SKIPPED.format(reason="bypass list is admin-only"))
        elif detail["bypass_actors"]:
            lines.append(f"- [ ] Ruleset `{ruleset['name']}` has "
                         f"{len(detail['bypass_actors'])} bypass actor(s): justify or remove")
        else:
            lines.append(f"- Ruleset `{ruleset['name']}`: no bypass actors")
    return lines


def review(repos: list[str], now: datetime,
           read: Callable[[str], Any] = api_or_unavailable) -> str:
    """Render the access review for every managed repository."""
    lines = [
        "<!-- access-review -->",
        f"Generated {now:%Y-%m-%d %H:%M} UTC by the Access review workflow. Items marked "
        "skipped were not readable with the token in use; nothing is inferred for them.",
    ]
    for repo in repos:
        base = f"repos/{OWNER}/{repo}"
        lines += ["", f"## {repo}"]
        lines += named_lines("Collaborators", read(f"{base}/collaborators?affiliation=all"),
                             lambda user: f"`{user['login']}` ({user['role_name']})")
        lines += named_lines("Deploy keys", read(f"{base}/keys"),
                             lambda key: f"`{key['title']}` ({'read-only' if key['read_only'] else 'read-write'})")
        lines += secret_lines(read(f"{base}/actions/secrets"), now)
        lines += bypass_lines(repo, read)
    lines += ["", "Close this issue once every checkbox is resolved or justified in a comment."]
    return "\n".join(lines) + "\n"


def main() -> None:
    """Build this month's review, or publish a built review."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--repos", type=Path, required=True, help="repos.yaml converted to JSON")
    build.add_argument("--output", type=Path, required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--body", type=Path, required=True)
    arguments = parser.parse_args()
    now = datetime.now(UTC)
    if arguments.command == "build":
        spec = json.loads(arguments.repos.read_text(encoding="utf-8"))
        arguments.output.write_text(review(list(spec["repos"]), now), encoding="utf-8")
    else:
        upsert_issue(f"Access review {now:%Y-%m}", arguments.body.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
