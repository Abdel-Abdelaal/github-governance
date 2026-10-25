"""Find known vulnerabilities in fleet packages and rank them by real-world risk.

Sources (all free and public, no key needed):
- OSV (https://osv.dev): which advisories affect each exact package version.
- CISA KEV: vulnerabilities with evidence of active exploitation.
- FIRST EPSS: probability of exploitation in the next 30 days.

These are different signals. KEV is evidence of attacks, EPSS is a forecast and
CVSS (not used here) is technical severity, so ranking puts KEV first, then EPSS.
"""

import json
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/{id}"
KEV_FEED = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
EPSS_API = "https://api.first.org/data/v1/epss?cve={cves}"
OSV_ECOSYSTEMS = {"pypi": "PyPI", "npm": "npm"}
OSV_BATCH_SIZE = 500
EPSS_BATCH_SIZE = 100
HIGH_EPSS = 0.1
TIMEOUT_SECONDS = 60

Fetch = Callable[[str, dict[str, Any] | None], Any]


@dataclass
class Finding:
    """One advisory affecting one package version somewhere in the fleet."""

    ecosystem: str
    package: str
    version: str
    advisory: str
    repos: list[str] = field(default_factory=list[str])
    cves: list[str] = field(default_factory=list[str])
    summary: str = ""
    fixed: str = ""
    kev: bool = False
    epss: float | None = None

    @property
    def urgent(self) -> bool:
        """Known exploited, or likely to be exploited soon."""
        return self.kev or (self.epss or 0) >= HIGH_EPSS


def fetch_json(url: str, payload: dict[str, Any] | None = None) -> Any:  # noqa: ANN401 - decoded JSON
    """GET (or POST JSON) a fixed public https endpoint and decode the response."""
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoints only
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        return json.load(response)


def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    """Split a list for batch APIs."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def affected(
    packages: dict[tuple[str, str, str], list[str]], fetch: Fetch = fetch_json
) -> list[Finding]:
    """Query OSV for every (ecosystem, name, version) and return raw findings."""
    keys = sorted(key for key in packages if key[0] in OSV_ECOSYSTEMS)
    findings: list[Finding] = []
    for batch in chunks(keys, OSV_BATCH_SIZE):
        queries = [
            {
                "package": {"name": name, "ecosystem": OSV_ECOSYSTEMS[eco]},
                "version": ver,
            }
            for eco, name, ver in batch
        ]
        results = fetch(OSV_BATCH, {"queries": queries})["results"]
        for (eco, name, ver), result in zip(batch, results, strict=True):
            findings += [
                Finding(eco, name, ver, vuln["id"], sorted(packages[(eco, name, ver)]))
                for vuln in result.get("vulns", [])
            ]
    return findings


def fixed_version(vuln: dict[str, Any], package: str) -> str:
    """The first fixed version OSV lists for this package, if any."""
    for entry in vuln.get("affected", []):
        if entry.get("package", {}).get("name", "").lower() != package.lower():
            continue
        for version_range in entry.get("ranges", []):
            for event in version_range.get("events", []):
                if "fixed" in event:
                    return str(event["fixed"])
    return ""


def enrich(findings: list[Finding], fetch: Fetch = fetch_json) -> list[Finding]:
    """Add CVE aliases, fix versions, KEV status and EPSS scores, then rank."""
    for finding in findings:
        vuln = fetch(OSV_VULN.format(id=finding.advisory), None)
        aliases = [finding.advisory, *vuln.get("aliases", [])]
        finding.cves = sorted({alias for alias in aliases if alias.startswith("CVE-")})
        finding.summary = str(vuln.get("summary", ""))[:120]
        finding.fixed = fixed_version(vuln, finding.package)
    cves = sorted({cve for finding in findings for cve in finding.cves})
    if cves:
        kev = {item["cveID"] for item in fetch(KEV_FEED, None)["vulnerabilities"]}
        scores: dict[str, float] = {}
        for batch in chunks(cves, EPSS_BATCH_SIZE):
            response = fetch(EPSS_API.format(cves=",".join(batch)), None)
            scores |= {row["cve"]: float(row["epss"]) for row in response["data"]}
        for finding in findings:
            finding.kev = any(cve in kev for cve in finding.cves)
            known = [scores[cve] for cve in finding.cves if cve in scores]
            finding.epss = max(known) if known else None
    return sorted(findings, key=lambda f: (not f.kev, -(f.epss or -1), f.package))


def attention(findings: list[Finding]) -> list[str]:
    """One "needs attention" line per urgent finding."""
    lines = []
    for finding in findings:
        if not finding.urgent:
            continue
        reason = (
            "actively exploited (CISA KEV)"
            if finding.kev
            else f"EPSS {finding.epss or 0:.0%} chance of exploitation in 30 days"
        )
        lines.append(
            f"- `{finding.package}` {finding.version}: {finding.advisory} is "
            f"**{reason}**; used by {', '.join(finding.repos)}"
        )
    return lines


def render(findings: list[Finding], checked: int) -> list[str]:
    """Markdown section for the inventory issue."""
    lines = [
        "",
        "## Known vulnerabilities, ranked by real-world risk",
        "",
        f"{checked} Python and npm package versions checked against "
        "[OSV](https://osv.dev); advisories ranked by "
        "[CISA KEV](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) "
        "(actively exploited) first, then [EPSS](https://www.first.org/epss/) "
        "(probability of exploitation in 30 days).",
        "",
    ]
    if not findings:
        return [*lines, "No known vulnerabilities in any checked package version."]
    lines += [
        "| Package | Version | Advisory | CVE | Exploited (KEV) | EPSS "
        "| Fixed in | Used by |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in findings:
        epss = f"{f.epss:.1%}" if f.epss is not None else "-"
        lines.append(
            f"| `{f.package}` ({f.ecosystem}) | {f.version} | {f.advisory} | "
            f"{', '.join(f.cves) or '-'} | {'**YES**' if f.kev else 'no'} | {epss} | "
            f"{f.fixed or '-'} | {', '.join(f.repos)} |"
        )
    return lines
