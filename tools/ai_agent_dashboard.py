"""Verify hosted ingestion of controller-reported agent steps, separately from CI."""

import hashlib
import json
import re
import subprocess
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from github_api import OWNER, api, gh

SOURCE = f"{OWNER}/platform-workflows"
SIGNER = f"{SOURCE}/.github/workflows/ai-agent-ingest.yml"
PREDICATE = (
    f"https://github.com/{SOURCE}/blob/main/docs/ai-agent-telemetry.md#agent-batch-v1"
)
ASSET = re.compile(r"agent-batch-[a-f0-9]{64}-[0-9]+\.json")
MAX_BYTES = 2_000_000
MAX_RECORDS = 25
PAGE_SIZE = 100
STAGES = {
    "triage",
    "plan",
    "implement",
    "repair",
    "review",
    "ci_repair",
    "ci_review",
    "gate",
    "route",
    "publish",
    "error",
}


def pages(endpoint: str) -> list[dict[str, Any]]:
    """Follow every monthly release and asset page."""
    result: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = api(f"{endpoint}?per_page=100&page={page}")
        result.extend(batch)
        if len(batch) < PAGE_SIZE:
            return result
        page += 1


def download(url: str, path: Path) -> None:
    """Read only bounded public assets in the fixed ingestion repository."""
    if not url.startswith(f"https://github.com/{SOURCE}/releases/download/"):
        raise ValueError
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError
    path.write_bytes(data)


def verify(subject: Path, bundle: Path) -> dict[str, Any]:
    """Require exact predicate, signer, public source and protected main ref."""
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
    result = verified[0]["verificationResult"]
    certificate = result["signature"]["certificate"]
    statement = result["statement"]
    document = json.loads(subject.read_text(encoding="utf-8"))
    if (
        certificate["sourceRepositoryVisibilityAtSigning"] != "public"
        or statement["predicateType"] != PREDICATE
        or statement["predicate"]["batch_id"] != document["batch_id"]
        or document.get("schema_version") != 1
        or document.get("record_type") != "agent-batch/v1"
        or not re.fullmatch(r"[a-f0-9]{64}", str(document["batch_id"]))
    ):
        raise ValueError
    records = document["records"]
    if not isinstance(records, list) or not 0 < len(records) <= MAX_RECORDS:
        raise ValueError
    canonical = json.dumps(records, sort_keys=True, ensure_ascii=True, allow_nan=False)
    if hashlib.sha256(canonical.encode()).hexdigest() != document["batch_id"]:
        raise ValueError
    invocation = certificate["runInvocationURI"]
    match = re.fullmatch(
        rf"https://github\.com/{re.escape(SOURCE)}/actions/runs/([0-9]+)/attempts/[0-9]+",
        invocation,
    )
    if not match or document["ingestion"]["github_run_id"] != match[1]:
        raise ValueError
    return document


def public_records(
    document: dict[str, Any], permitted: set[str]
) -> list[dict[str, Any]]:
    """Validate the entire batch before returning any currently public entries."""
    records = []
    for record in document["records"]:
        if not isinstance(record, dict):
            raise TypeError
        if record.get("repo") not in permitted:
            continue
        if (
            record.get("schema") != "agent-dispatcher.attempt/v1"
            or record.get("stage") not in STAGES
            or not isinstance(record.get("record_id"), str)
            or not re.fullmatch(r"[a-f0-9-]{36}", record["record_id"])
        ):
            raise ValueError
        records.append(record)
    return records


def collect(
    directory: Path, public_repos: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Only verified steps targeting currently public fleet repos enter the dataset."""
    if api(f"repos/{SOURCE}").get("private") is not False:
        raise ValueError
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    permitted = {f"{OWNER}/{name}" for name in public_repos}
    seen: set[str] = set()
    for release in pages(f"repos/{SOURCE}/releases"):
        if not re.fullmatch(r"ai-telemetry-[0-9]{4}-[0-9]{2}", release["tag_name"]):
            continue
        assets = {
            a["name"]: a
            for a in pages(f"repos/{SOURCE}/releases/{release['id']}/assets")
        }
        for name, asset in assets.items():
            if not ASSET.fullmatch(name):
                continue
            try:
                subject = directory / name
                companion = name[:-5] + ".sigstore.json"
                bundle = directory / companion
                download(asset["browser_download_url"], subject)
                download(assets[companion]["browser_download_url"], bundle)
                document = verify(subject, bundle)
                for record in public_records(document, permitted):
                    identity = record["record_id"]
                    if identity in seen:
                        continue
                    seen.add(identity)
                    entries.append(
                        {
                            "record": record,
                            "url": asset["browser_download_url"],
                            "verified": True,
                            "assurance": "controller-reported; hosted ingestion",
                        }
                    )
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                IndexError,
                subprocess.SubprocessError,
            ):
                errors.append(
                    {
                        "repository": "platform-workflows",
                        "asset": name,
                        "reason": "agent batch download/schema/signature failed",
                    }
                )
    return entries, errors


def metrics(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Report measured counters and outcomes without calling them correctness scores."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        record = entry["record"]
        groups[
            (record["repo"], record["stage"], record.get("provider") or "controller")
        ].append(record)
    rows = []
    for (repo, stage, provider), records in sorted(groups.items()):
        usage: Counter[str] = Counter()
        timed = [
            r["duration_ms"]
            for r in records
            if type(r.get("duration_ms")) in (int, float)
        ]
        for record in records:
            usage.update(
                {
                    key: value
                    for key, value in (record.get("usage") or {}).items()
                    if type(value) in (int, float) and value >= 0
                }
            )
        rows.append(
            {
                "repository": repo,
                "stage": stage,
                "provider": provider,
                "records": len(records),
                "models": sorted({r["model"] for r in records if r.get("model")}),
                "outcomes": dict(
                    Counter(r.get("outcome") or "unknown" for r in records)
                ),
                "usage_totals": dict(usage),
                "records_with_usage": sum(bool(r.get("usage")) for r in records),
                "mean_seconds": sum(timed) / len(timed) / 1000 if timed else None,
                "records_with_timing": len(timed),
            }
        )
    return {
        "records": len(entries),
        "rows": rows,
        "assurance": "Controller-reported attempts; signatures attest ingestion, "
        "not local execution.",
        "payload_retention": "Hashes, sizes and bounded excerpts; "
        "full prompts/outputs are not retained.",
    }
