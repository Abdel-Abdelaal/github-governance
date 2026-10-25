"""Publish a bounded plan summary and maintain one drift issue."""

import json
import os
from pathlib import Path

from github_api import gh


def main() -> None:
    """Keep PR comments and drift issues idempotent."""
    plan = Path("plan.txt").read_text(encoding="utf-8")
    body = (
        "<!-- terraform-plan -->\n## Terraform plan\n\n```text\n"
        + plan[:50000]
        + "\n```\n"
    )
    Path("plan-comment.md").write_text(body, encoding="utf-8")
    if os.environ["EVENT_NAME"] == "pull_request":
        pr_number = os.environ["PR_NUMBER"]
        repository = os.environ["GH_REPO"]
        comments = json.loads(
            gh(
                "api",
                f"repos/{repository}/issues/{pr_number}/comments",
                "--paginate",
                "--slurp",
            )
        )
        existing = next(
            (
                comment
                for page in comments
                for comment in page
                if comment["body"].startswith("<!-- terraform-plan -->")
                and comment["user"]["login"] == "github-actions[bot]"
            ),
            None,
        )
        if existing:
            gh(
                "api",
                "-X",
                "PATCH",
                f"repos/{repository}/issues/comments/{existing['id']}",
                "-F",
                "body=@plan-comment.md",
            )
        else:
            gh("pr", "comment", pr_number, "--body-file", "plan-comment.md")
    if os.environ["EVENT_NAME"] not in {"schedule", "workflow_dispatch"}:
        return
    issues = json.loads(
        gh(
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            "governance",
            "--search",
            '"Drift detected" in:title',
            "--json",
            "number,title",
        )
    )
    existing = next(
        (issue for issue in issues if issue["title"] == "Drift detected"), None
    )
    if os.environ["PLAN_RESULT"] == "2":
        if existing:
            gh(
                "issue",
                "edit",
                str(existing["number"]),
                "--body-file",
                "plan-comment.md",
            )
        else:
            gh(
                "issue",
                "create",
                "--title",
                "Drift detected",
                "--label",
                "governance",
                "--body-file",
                "plan-comment.md",
            )
    elif existing:
        gh(
            "issue",
            "close",
            str(existing["number"]),
            "--comment",
            "Desired state now matches GitHub.",
        )


if __name__ == "__main__":
    main()
