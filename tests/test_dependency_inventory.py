"""Test the dependency inventory without network access."""

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))

import dependency_inventory as inventory
from github_api import Unavailable

NOW = datetime(2026, 10, 25, tzinfo=UTC)
SHA = "a" * 40
OLD_SHA = "b" * 40


def sbom(*purls):
    return {
        "sbom": {
            "packages": [{"externalRefs": [{"referenceLocator": p}]} for p in purls]
        }
    }


def reader(responses):
    def read(path):
        if path in responses:
            return responses[path]
        return Unavailable("HTTP 404: not readable with this token")

    return read


class ParsingTests(unittest.TestCase):
    def test_sbom_keeps_supported_ecosystems_and_decodes_names(self):
        found = inventory.from_sbom(
            "r",
            sbom(
                f"pkg:githubactions/actions/checkout@{SHA}",
                "pkg:pypi/fastapi@0.162.0",
                "pkg:npm/%40scope/tool@1.2.3",
                "pkg:github/Abdel-Abdelaal/r@main",
            ),
        )
        self.assertEqual(
            [(d.ecosystem, d.name, d.version) for d in found],
            [
                ("githubactions", "actions/checkout", SHA),
                ("pypi", "fastapi", "0.162.0"),
                ("npm", "@scope/tool", "1.2.3"),
            ],
        )

    def test_files_cover_hooks_terraform_and_images(self):
        files = {
            "pre-commit": "repos:\n  - repo: https://github.com/astral-sh/ruff-pre-commit\n    rev: 44b5 # v0.17.0\n",
            "terraform": 'provider "registry.terraform.io/integrations/github" {\n  version     = "6.13.0"\n',
            "docker": "FROM python:3.12-slim@sha256:abc AS build\nFROM scratch\n",
        }
        found = inventory.from_files("r", files)
        self.assertEqual(
            [(d.ecosystem, d.name, d.version) for d in found],
            [
                ("pre-commit", "astral-sh/ruff-pre-commit", "44b5"),
                ("terraform", "integrations/github", "6.13.0"),
                ("docker", "python:3.12-slim", "sha256:abc"),
                ("docker", "scratch", "unpinned"),
            ],
        )

    def test_reusable_workflow_maps_to_its_repository(self):
        dependency = inventory.Dependency(
            "githubactions", "github/codeql-action/upload-sarif", SHA, "r"
        )
        self.assertEqual(inventory.github_project(dependency), "github/codeql-action")


class StatusTests(unittest.TestCase):
    def test_pinned_sha_matching_latest_release_is_current(self):
        upstream = inventory.Upstream(latest="v7.0.1", latest_commit=SHA)
        current = inventory.Dependency("githubactions", "actions/checkout", SHA, "r")
        older = inventory.Dependency("githubactions", "actions/checkout", OLD_SHA, "r")
        self.assertTrue(inventory.is_current(current, upstream))
        self.assertFalse(inventory.is_current(older, upstream))

    def test_registry_versions_compare_without_v_prefix(self):
        upstream = inventory.Upstream(latest="v1.2.3")
        self.assertTrue(
            inventory.is_current(
                inventory.Dependency("npm", "x", "1.2.3", "r"), upstream
            )
        )

    def test_digest_pins_and_unknowns_are_not_reported_as_outdated(self):
        for latest in ("pinned by digest", "unknown", "no releases"):
            dependency = inventory.Dependency("docker", "python", "sha256:abc", "r")
            self.assertIsNone(
                inventory.is_current(dependency, inventory.Upstream(latest=latest))
            )


class CollectAndRenderTests(unittest.TestCase):
    def test_archived_stale_and_unpinned_upstreams_need_attention(self):
        responses = {
            "repos/Abdel-Abdelaal/r/dependency-graph/sbom": sbom(
                f"pkg:githubactions/old/archived@{SHA}",
                f"pkg:githubactions/quiet/project@{SHA}",
            ),
            "repos/old/archived": {
                "archived": True,
                "pushed_at": "2026-10-01T00:00:00Z",
                "stargazers_count": 1,
            },
            "repos/quiet/project": {
                "archived": False,
                "pushed_at": "2024-01-01T00:00:00Z",
                "stargazers_count": 1,
            },
            "repos/Abdel-Abdelaal/r/contents/Dockerfile": {
                "content": "RlJPTSBzY3JhdGNoCg=="
            },
        }
        dependencies, upstreams = inventory.collect(
            ["r"], read=reader(responses), latest=lambda *_: "unknown"
        )
        body = inventory.render(dependencies, upstreams, NOW)
        self.assertIn("`old/archived` (githubactions) is **archived**", body)
        self.assertIn(
            "`quiet/project` (githubactions) has had no push since 2024-01-01", body
        )
        self.assertIn("Base image `scratch` is not digest-pinned", body)
        self.assertIn("**3** need attention", body)

    def test_outdated_caller_of_own_platform_is_counted(self):
        responses = {
            "repos/Abdel-Abdelaal/r/dependency-graph/sbom": sbom(
                f"pkg:githubactions/Abdel-Abdelaal/platform-workflows/.github/workflows/ci.yml@{OLD_SHA}"
            ),
            "repos/Abdel-Abdelaal/platform-workflows": {
                "archived": False,
                "pushed_at": "2026-10-25T00:00:00Z",
                "stargazers_count": 0,
            },
            "repos/Abdel-Abdelaal/platform-workflows/releases/latest": {
                "tag_name": "v1.5.0"
            },
            "repos/Abdel-Abdelaal/platform-workflows/commits/v1.5.0": {"sha": SHA},
        }
        dependencies, upstreams = inventory.collect(
            ["r"], read=reader(responses), latest=lambda *_: "unknown"
        )
        body = inventory.render(dependencies, upstreams, NOW)
        self.assertIn("**1** have a newer release", body)
        self.assertIn("update available (v1.5.0)", body)


if __name__ == "__main__":
    unittest.main()
