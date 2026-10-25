"""Test vulnerability lookup and ranking without network access."""

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))

import dependency_inventory as inventory
import vulnerabilities as vulns

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def fake_fetch(responses):
    calls = []

    def fetch(url, payload=None):
        calls.append((url, payload))
        for prefix, response in responses.items():
            if url.startswith(prefix):
                return response(payload) if callable(response) else response
        raise AssertionError(f"unexpected url {url}")

    fetch.calls = calls
    return fetch


OSV_BATCH = lambda payload: {  # noqa: E731
    "results": [
        {"vulns": [{"id": "GHSA-aaaa"}]}
        if query["package"]["name"] == "jinja2"
        else {"vulns": [{"id": "PYSEC-bbbb"}]}
        if query["package"]["name"] == "requests"
        else {}
        for query in payload["queries"]
    ]
}
RESPONSES = {
    vulns.OSV_BATCH: OSV_BATCH,
    "https://api.osv.dev/v1/vulns/GHSA-aaaa": {
        "aliases": ["CVE-2024-0001"],
        "summary": "Sandbox escape",
        "affected": [
            {
                "package": {"name": "Jinja2"},
                "ranges": [{"events": [{"introduced": "0"}, {"fixed": "3.1.5"}]}],
            }
        ],
    },
    "https://api.osv.dev/v1/vulns/PYSEC-bbbb": {"aliases": ["CVE-2024-0002"]},
    vulns.KEV_FEED: {"vulnerabilities": [{"cveID": "CVE-2024-0002"}]},
    "https://api.first.org/data/v1/epss": {
        "data": [
            {"cve": "CVE-2024-0001", "epss": "0.42"},
            {"cve": "CVE-2024-0002", "epss": "0.01"},
        ]
    },
}
PACKAGES = {
    ("pypi", "jinja2", "3.1.2"): ["shortlink"],
    ("pypi", "requests", "2.0.0"): ["platform-workflows", "shortlink"],
    ("pypi", "safe", "1.0"): ["shortlink"],
    ("githubactions", "actions/checkout", "abc"): ["shortlink"],
}


class VulnerabilityTests(unittest.TestCase):
    def test_only_osv_ecosystems_are_queried(self):
        fetch = fake_fetch(RESPONSES)
        vulns.affected(PACKAGES, fetch)
        queried = [q["package"]["name"] for q in fetch.calls[0][1]["queries"]]
        self.assertEqual(queried, ["jinja2", "requests", "safe"])

    def test_kev_outranks_higher_epss_and_fields_are_enriched(self):
        fetch = fake_fetch(RESPONSES)
        findings = vulns.enrich(vulns.affected(PACKAGES, fetch), fetch)
        self.assertEqual([f.package for f in findings], ["requests", "jinja2"])
        exploited, likely = findings
        self.assertTrue(exploited.kev)
        self.assertEqual(exploited.repos, ["platform-workflows", "shortlink"])
        self.assertEqual(
            (likely.cves, likely.fixed, likely.epss), (["CVE-2024-0001"], "3.1.5", 0.42)
        )
        self.assertTrue(likely.urgent)

    def test_no_findings_skips_kev_and_epss_calls(self):
        fetch = fake_fetch({vulns.OSV_BATCH: {"results": [{}]}})
        self.assertEqual(
            vulns.enrich(vulns.affected({("npm", "x", "1"): ["r"]}, fetch), fetch), []
        )
        self.assertEqual(len(fetch.calls), 1)

    def test_render_states_clean_result_explicitly(self):
        lines = vulns.render([], 91)
        self.assertIn("91 Python and npm package versions checked", "\n".join(lines))
        self.assertEqual(
            lines[-1], "No known vulnerabilities in any checked package version."
        )


class InventoryIntegrationTests(unittest.TestCase):
    def test_urgent_findings_need_attention_and_are_tabled(self):
        dependencies = [inventory.Dependency("pypi", "requests", "2.0.0", "shortlink")]
        finding = vulns.Finding(
            "pypi",
            "requests",
            "2.0.0",
            "PYSEC-bbbb",
            ["shortlink"],
            ["CVE-2024-0002"],
            kev=True,
        )
        body = inventory.render(dependencies, {}, NOW, [finding])
        self.assertIn("PYSEC-bbbb is **actively exploited (CISA KEV)**", body)
        self.assertIn(
            "| `requests` (pypi) | 2.0.0 | PYSEC-bbbb | CVE-2024-0002 | **YES** |", body
        )

    def test_unreachable_sources_are_reported_not_hidden(self):
        def broken(_url, _payload=None):
            raise OSError("down")

        findings, note = inventory.assess(
            [inventory.Dependency("pypi", "x", "1", "r")], broken
        )
        self.assertIsNone(findings)
        body = inventory.render([], {}, NOW, findings, note)
        self.assertIn(
            "Not checked this run: a vulnerability source was unreachable (OSError)",
            body,
        )


if __name__ == "__main__":
    unittest.main()
