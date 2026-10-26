"""Verify public fleet AI records and build a static, evidence-only dashboard."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ai_agent_dashboard
import ai_redteam_dashboard
from github_api import OWNER, api, gh

PREDICATE = f"https://github.com/{OWNER}/platform-workflows/blob/main/docs/ai-telemetry.md#record-v1"
SIGNER = f"{OWNER}/platform-workflows/.github/workflows/ai.yml"
MAX_RECORD_BYTES = 2_000_000
PAGE_SIZE = 100
ASSET_NAME = re.compile(r"ai-[0-9]+-[0-9]+-(review|audit|fix|standards)\.json")


def download(url: str, path: Path) -> None:
    """Download a bounded public asset, without credentials or arbitrary hosts."""
    if not url.startswith(f"https://github.com/{OWNER}/"):
        msg = "asset URL outside the public fleet"
        raise ValueError(msg)
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        data = response.read(MAX_RECORD_BYTES + 1)
    if len(data) > MAX_RECORD_BYTES:
        msg = "telemetry asset exceeds size limit"
        raise ValueError(msg)
    path.write_bytes(data)


def pages(endpoint: str) -> list[dict[str, Any]]:
    """Follow pagination for both releases and their potentially large asset lists."""
    result: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = api(f"{endpoint}?per_page={PAGE_SIZE}&page={page}")
        result.extend(batch)
        if len(batch) < PAGE_SIZE:
            return result
        page += 1


def validate_identity(record: dict[str, Any], repository: str) -> None:
    """Reject unsupported or misattributed records after signature verification."""
    if (
        record["schema_version"] != 1
        or record["identity"]["github_repository"] != f"{OWNER}/{repository}"
    ):
        msg = "record schema or repository mismatch"
        raise ValueError(msg)


def collect(
    repository: str, directory: Path
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Read every monthly release page, count only verified record subjects."""
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    releases = pages(f"repos/{OWNER}/{repository}/releases")
    for release in releases:
        if not re.fullmatch(r"ai-telemetry-\d{4}-\d{2}", release["tag_name"]):
            continue
        assets = {
            asset["name"]: asset
            for asset in pages(
                f"repos/{OWNER}/{repository}/releases/{release['id']}/assets"
            )
        }
        for name, asset in assets.items():
            if not ASSET_NAME.fullmatch(name):
                continue
            path = directory / name
            bundle = directory / name.replace(".json", ".sigstore.json")
            try:
                download(asset["browser_download_url"], path)
                companion = assets[bundle.name]
                download(companion["browser_download_url"], bundle)
                gh(
                    "attestation",
                    "verify",
                    str(path),
                    "--repo",
                    f"{OWNER}/{repository}",
                    "--bundle",
                    str(bundle),
                    "--predicate-type",
                    PREDICATE,
                    "--signer-workflow",
                    SIGNER,
                    "--deny-self-hosted-runners",
                )
                record = json.loads(path.read_text(encoding="utf-8"))
                validate_identity(record, repository)
                records.append(
                    {
                        "record": record,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "url": asset["browser_download_url"],
                        "verified": True,
                    }
                )
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                subprocess.CalledProcessError,
            ):
                # Do not print model output, API errors or external content.
                errors.append(
                    {
                        "repository": repository,
                        "asset": name,
                        "reason": "download, schema or signature verification failed",
                    }
                )
    return records, errors


def metrics(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate known measurements; unavailable usage never becomes measured zero."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for entry in entries:
        record = entry["record"]
        if record["record_id"] in seen:
            continue
        seen.add(record["record_id"])
        identity = record["identity"]
        grouped[(identity["github_repository"], identity["mode"])].append(record)
    rows: list[dict[str, Any]] = []
    for (repo, mode), records in sorted(grouped.items()):
        attempted = [r for r in records if r["status"] != "skipped"]
        timed = [r for r in attempted if r["attempts"]]
        valid = sum(r["status"] == "valid" for r in attempted)
        attempts = [a for r in timed for a in r["attempts"]]
        failures: Counter[str] = Counter()
        for attempt in attempts:
            if attempt["exit_category"] != "ok":
                failures[attempt["exit_category"]] += 1
            for error in attempt.get("validation_errors", []):
                failures["validation:" + error["type"]] += 1
        usage: Counter[str] = Counter()
        for record in records:
            usage.update(record.get("usage_totals", {}))
        receipts = [p for r in records for p in r.get("publication", [])]
        patches = Counter(
            r.get("patch_check", {}).get("status", "unknown") for r in records
        )
        rows.append(
            {
                "repository": repo,
                "mode": mode,
                "records": len(records),
                "attempted_runs": len(attempted),
                "valid_runs": valid,
                "success_rate": valid / len(attempted) if attempted else None,
                "skipped": len(records) - len(attempted),
                "runs_with_attempts": len(timed),
                "runs_without_attempt_evidence": len(attempted) - len(timed),
                "attempts_per_run": len(attempts) / len(timed) if timed else None,
                "mean_run_seconds": sum(a["duration_seconds"] for a in attempts)
                / len(timed)
                if timed
                else None,
                "usage_totals": dict(usage),
                "runs_with_usage": sum(bool(r.get("usage_totals")) for r in attempted),
                "evidence_gaps": sum(bool(r.get("evidence_gap")) for r in records),
                "invalid_publication_receipts": sum(
                    r.get("invalid_publication_receipts", 0) for r in records
                ),
                "findings_proposed": sum(
                    r.get("findings", {}).get("proposed", 0) for r in records
                ),
                "findings_published": sum(
                    p.get("findings_published", 0)
                    for p in receipts
                    if p["status"] in {"published", "partially_published"}
                ),
                "patches": dict(patches),
                "failure_categories": dict(failures),
            }
        )
    return rows


def build(
    repositories: list[str],
    output: Path,
    *,
    include_redteam: bool = False,
    include_agents: bool = False,
) -> None:
    """Create a queryable dataset and metrics from verified records only."""
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    agent_entries: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ai-metrics-") as directory:
        for repository in repositories:
            try:
                records, failures = collect(repository, Path(directory))
                entries.extend(records)
                errors.extend(failures)
            except (OSError, ValueError, TypeError, subprocess.CalledProcessError):
                errors.append(
                    {
                        "repository": repository,
                        "reason": "release collection unavailable",
                    }
                )
        if include_agents:
            try:
                agent_entries, agent_errors = ai_agent_dashboard.collect(
                    Path(directory), repositories
                )
                errors.extend(agent_errors)
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                subprocess.SubprocessError,
            ):
                errors.append(
                    {
                        "repository": "platform-workflows",
                        "reason": "agent evidence collection unavailable",
                    }
                )
    output.mkdir(parents=True, exist_ok=True)
    redteam = "not yet collected"
    if include_redteam:
        try:
            redteam = ai_redteam_dashboard.latest()
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            subprocess.CalledProcessError,
        ):
            redteam = {
                "available": False,
                "reason": "signed red-team evidence unavailable",
            }
        if not redteam.get("available"):
            errors.append(
                {"repository": "platform-workflows", "reason": redteam["reason"]}
            )
        if os.environ.get("REDTEAM_ARCHIVE_RESULT") == "failure":
            errors.append(
                {
                    "repository": "platform-workflows",
                    "reason": "latest red-team archival failed; "
                    "retained evidence may be stale",
                }
            )
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "repositories": repositories,
        "rows": metrics(entries),
        "collection_errors": errors,
        "verified_records": len(entries),
        "repositories_without_records": [
            repository
            for repository in repositories
            if not any(
                entry["record"]["identity"]["github_repository"]
                == f"{OWNER}/{repository}"
                for entry in entries
            )
        ],
        "outcomes": "not yet collected",
        "redteam": redteam,
        "agents": ai_agent_dashboard.metrics(agent_entries),
    }
    (output / "metrics.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (output / "records.jsonl").write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8"
    )
    (output / "agent-records.jsonl").write_text(
        "".join(json.dumps(entry) + "\n" for entry in agent_entries), encoding="utf-8"
    )
    (output / "badge.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "label": "AI records",
                "message": str(len(entries)),
                "color": "orange" if errors else "blue",
            }
        ),
        encoding="utf-8",
    )


def main() -> None:
    """Read the managed fleet list supplied by the workflow."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--discover-public", action="store_true")
    parser.add_argument("--include-redteam", action="store_true")
    parser.add_argument("--include-agents", action="store_true")
    args = parser.parse_args()
    spec = json.loads(args.repos.read_text(encoding="utf-8"))
    repositories = list(spec["repos"])
    if args.discover_public:
        # This endpoint lists public repositories; reject private metadata again
        # defensively so a future API/client change cannot expose private names.
        repositories = sorted(
            {
                item["name"]
                for item in pages(f"users/{OWNER}/repos")
                if item.get("private") is False
                and not item.get("fork")
                and not item.get("archived")
                and isinstance(item.get("name"), str)
                and re.fullmatch(r"[A-Za-z0-9_.-]+", item["name"])
            }
        )
    build(
        repositories,
        args.output,
        include_redteam=args.include_redteam,
        include_agents=args.include_agents,
    )


if __name__ == "__main__":
    main()
