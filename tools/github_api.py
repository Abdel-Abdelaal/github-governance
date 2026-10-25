"""Read GitHub data and maintain tracking issues through the GitHub CLI."""

import json
import subprocess
from dataclasses import dataclass
from typing import Any

OWNER = "Abdel-Abdelaal"
GOVERNANCE_LABEL = "governance"
MAX_ISSUE_BODY = 60000


@dataclass(frozen=True)
class Unavailable:
    """An endpoint the current token may not read, recorded instead of guessed."""

    reason: str


def gh(*arguments: str) -> str:
    """Run GitHub CLI without a shell and return its standard output."""
    return subprocess.run(["gh", *arguments], check=True, capture_output=True,
                          encoding="utf-8").stdout


def api(path: str) -> Any:
    """Return decoded JSON for one REST path."""
    return json.loads(gh("api", path))


def api_or_unavailable(path: str) -> Any:
    """Return decoded JSON, or Unavailable when the token lacks access."""
    try:
        return api(path)
    except subprocess.CalledProcessError as error:
        detail = error.stderr or ""
        for status in ("401", "403", "404"):
            if f"HTTP {status}" in detail:
                return Unavailable(f"HTTP {status}: not readable with this token")
        raise


def upsert_issue(title: str, body: str) -> None:
    """Edit the open governance issue with this exact title, or create it."""
    issues = json.loads(gh("issue", "list", "--state", "open", "--label", GOVERNANCE_LABEL,
                           "--search", f'"{title}" in:title', "--json", "number,title"))
    existing = next((issue for issue in issues if issue["title"] == title), None)
    bounded = body[:MAX_ISSUE_BODY]
    if existing:
        gh("issue", "edit", str(existing["number"]), "--body", bounded)
    else:
        gh("issue", "create", "--title", title, "--label", GOVERNANCE_LABEL, "--body", bounded)
