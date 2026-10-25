"""Retain public red-team subjects and read only signature-bound score predicates."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from github_api import OWNER, api, gh

SOURCE = f"{OWNER}/platform-workflows"
STORE = f"{OWNER}/github-governance"
PREDICATE = f"https://github.com/{SOURCE}/blob/main/docs/ai-security.md#red-team-v1"
SIGNER = f"{SOURCE}/.github/workflows/ai-redteam.yml"
MAX_BYTES = 4_000_000
PAGE_SIZE = 100
MAX_ATTACKS = 10000


def pages(endpoint: str) -> list[dict[str, Any]]:
    """Read every page of release and asset metadata."""
    result = []
    page = 1
    while True:
        batch = api(f"{endpoint}?per_page={PAGE_SIZE}&page={page}")
        result.extend(batch)
        if len(batch) < PAGE_SIZE:
            return result
        page += 1


def validate_summary(value: object) -> dict[str, Any]:
    """Require internally consistent signed counts; never read unsigned summaries."""
    if not isinstance(value, dict):
        message = "invalid signed red-team summary"
        raise TypeError(message)
    for field in ("attacks", "defended", "useful"):
        if type(value.get(field)) is not int or not 0 <= value[field] <= MAX_ATTACKS:
            message = "invalid signed red-team count"
            raise ValueError(message)
    results = value.get("results")
    if not isinstance(results, list) or len(results) != value["attacks"] or not results:
        message = "incomplete signed red-team results"
        raise ValueError(message)
    for row in results:
        if not isinstance(row, dict) or any(
            type(row.get(key)) is not bool for key in ("defended", "useful")
        ):
            message = "invalid signed red-team result"
            raise ValueError(message)
        if not re.fullmatch(r"LLM(?:0[1-9]|10)", str(row.get("owasp", ""))):
            message = "invalid OWASP category"
            raise ValueError(message)
    if any(
        sum(row[key] for row in results) != value[key] for key in ("defended", "useful")
    ):
        message = "inconsistent signed red-team counts"
        raise ValueError(message)
    generated = datetime.fromisoformat(value["generated_at"])
    if generated.tzinfo is None:
        message = "red-team timestamp requires timezone"
        raise ValueError(message)
    return {
        key: value[key] for key in ("generated_at", "attacks", "defended", "useful")
    }


def verify(subject: Path, bundle: Path) -> dict[str, Any]:
    """Pin signer, predicate and default-branch source before using its predicate."""
    if subject.stat().st_size > MAX_BYTES or bundle.stat().st_size > MAX_BYTES:
        message = "red-team evidence exceeds bound"
        raise ValueError(message)
    verified = json.loads(
        gh(
            "attestation",
            "verify",
            str(subject),
            "--repo",
            SOURCE,
            "--bundle",
            str(bundle),
            "--signer-workflow",
            SIGNER,
            "--predicate-type",
            PREDICATE,
            "--source-ref",
            "refs/heads/main",
            "--deny-self-hosted-runners",
            "--format",
            "json",
        )
    )
    if not isinstance(verified, list) or not verified:
        message = "no verified red-team statement"
        raise ValueError(message)
    statement = verified[0]["verificationResult"]["statement"]
    if statement["predicateType"] != PREDICATE:
        message = "unexpected signed predicate"
        raise ValueError(message)
    certificate = verified[0]["verificationResult"]["signature"]["certificate"]
    if certificate["sourceRepositoryVisibilityAtSigning"] != "public":
        message = "red-team source was not public at signing"
        raise ValueError(message)
    invocation = certificate["runInvocationURI"]
    match = re.fullmatch(
        rf"https://github\.com/{re.escape(SOURCE)}/actions/runs/(\d+)/attempts/\d+",
        invocation,
    )
    if not match:
        message = "unexpected signed invocation identity"
        raise ValueError(message)
    return {
        **validate_summary(statement["predicate"]),
        "run_id": int(match[1]),
        "run_url": invocation,
    }


def require_public_source() -> None:
    """Refuse to copy source data if its repository is no longer public."""
    if api(f"repos/{SOURCE}").get("private") is not False:
        message = "red-team source is not explicitly public"
        raise ValueError(message)


def archive(directory: Path) -> tuple[dict[str, Any], Path, Path]:
    """Retain the latest completed main run in public, immutable Release assets."""
    require_public_source()
    runs = api(
        f"repos/{SOURCE}/actions/workflows/ai-redteam.yml/runs?branch=main&status=completed&per_page=20"
    )["workflow_runs"]
    runs = [
        run
        for run in runs
        if run["head_branch"] == "main" and run["event"] != "pull_request"
    ]
    if not runs:
        message = "no completed main red-team run"
        raise ValueError(message)
    run_id = runs[0]["id"]
    if type(run_id) is not int or run_id <= 0:
        message = "invalid red-team run identity"
        raise ValueError(message)
    directory.mkdir(parents=True, exist_ok=True)
    gh(
        "run",
        "download",
        str(run_id),
        "--repo",
        SOURCE,
        "--name",
        f"ai-red-team-{run_id}",
        "--dir",
        str(directory),
    )
    subject = directory / "redteam-results.json"
    if subject.stat().st_size > MAX_BYTES:
        message = "red-team subject exceeds bound"
        raise ValueError(message)
    # Custom predicate filtering is rejected by the download API. Fetch all bundles
    # for this exact digest, then enforce the custom predicate in offline verify.
    digest = hashlib.sha256(subject.read_bytes()).hexdigest()
    gh("attestation", "download", str(subject), "--repo", SOURCE)
    generated = Path(f"sha256:{digest}.jsonl")
    if not generated.exists():
        generated = Path(f"sha256-{digest}.jsonl")
    bundle = directory / "redteam.sigstore.json"
    shutil.move(generated, bundle)
    summary = verify(subject, bundle)
    if summary["run_id"] != run_id:
        message = "signed red-team run identity mismatch"
        raise ValueError(message)
    target = directory / f"redteam-{run_id}.json"
    signature = directory / f"redteam-{run_id}.sigstore.json"
    shutil.copyfile(subject, target)
    shutil.copyfile(bundle, signature)
    return summary, target, signature


def publish(directory: Path) -> None:
    """Stage verified evidence and a bounded plan for the separate upload step."""
    summary, subject, bundle = archive(directory)
    # The caller performs the checksum-verified secret scan before this command's
    # separate upload phase; staging alone never publishes unscanned payloads.
    (directory / "archive-plan.json").write_text(
        json.dumps(
            {
                "tag": "ai-redteam-" + summary["generated_at"][:7],
                "subject": subject.name,
                "bundle": bundle.name,
            }
        ),
        encoding="utf-8",
    )


def upload(directory: Path) -> None:
    """Append missing assets without ever replacing existing evidence."""
    require_public_source()
    plan = json.loads((directory / "archive-plan.json").read_text(encoding="utf-8"))
    tag = plan["tag"]
    if not re.fullmatch(r"ai-redteam-\d{4}-\d{2}", tag):
        message = "invalid archive tag"
        raise ValueError(message)
    for name in (plan["subject"], plan["bundle"]):
        if not re.fullmatch(r"redteam-\d+(?:\.sigstore)?\.json", name):
            message = "invalid archive asset"
            raise ValueError(message)
    try:
        release = api(f"repos/{STORE}/releases/tags/{tag}")
    except subprocess.CalledProcessError as error:
        if "HTTP 404" not in (error.stderr or ""):
            raise
        gh(
            "release",
            "create",
            tag,
            "--repo",
            STORE,
            "--prerelease",
            "--latest=false",
            "--title",
            "Signed AI red-team evidence",
            "--notes",
            "Verified public platform red-team subjects and Sigstore bundles. "
            "No unsigned scoreboard data.",
        )
        release = api(f"repos/{STORE}/releases/tags/{tag}")
    assets = {
        item["name"] for item in pages(f"repos/{STORE}/releases/{release['id']}/assets")
    }
    missing = [
        str(directory / name)
        for name in (plan["subject"], plan["bundle"])
        if name not in assets
    ]
    if missing:
        gh("release", "upload", tag, *missing, "--repo", STORE)


def latest() -> dict[str, Any]:
    """Use durable signed subjects, keeping failures and unknown coverage explicit."""
    require_public_source()
    candidates = []
    for release in pages(f"repos/{STORE}/releases"):
        if not re.fullmatch(r"ai-redteam-\d{4}-\d{2}", release["tag_name"]):
            continue
        assets = {
            item["name"]
            for item in pages(f"repos/{STORE}/releases/{release['id']}/assets")
        }
        for name in assets:
            match = re.fullmatch(r"redteam-(\d+)\.json", name)
            if match and name.replace(".json", ".sigstore.json") in assets:
                candidates.append((int(match[1]), release["tag_name"], name))
    if not candidates:
        return {
            "available": False,
            "reason": "awaiting durable signed red-team evidence",
        }
    run_id, tag, name = max(candidates)
    with tempfile.TemporaryDirectory(prefix="redteam-metrics-") as temporary:
        directory = Path(temporary)
        companion = name.replace(".json", ".sigstore.json")
        for asset in (name, companion):
            gh(
                "release",
                "download",
                tag,
                "--repo",
                STORE,
                "--pattern",
                asset,
                "--dir",
                str(directory),
            )
        summary = verify(directory / name, directory / companion)
        if summary["run_id"] != run_id:
            message = "archive asset does not match signed run identity"
            raise ValueError(message)
    return {
        "available": True,
        **summary,
        "evidence_url": f"https://github.com/{STORE}/releases/tag/{tag}",
    }


def main() -> None:
    """Stage or publish public evidence without logging external data."""
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["stage", "upload"])
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        (publish if args.command == "stage" else upload)(args.directory)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        subprocess.CalledProcessError,
    ):
        message = "Red-team evidence operation failed; external output withheld."
        raise SystemExit(message) from None


if __name__ == "__main__":
    main()
