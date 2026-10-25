"""Exercise signature gating and honest aggregate denominators offline."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))

import ai_telemetry_dashboard as dashboard


def record(record_id="a", status="valid", usage=True):
    """Build a realistic measured record without provider data."""
    return {
        "schema_version": 1,
        "record_id": record_id,
        "status": status,
        "identity": {
            "github_repository": f"{dashboard.OWNER}/shortlink",
            "mode": "review",
        },
        "attempts": []
        if status == "skipped"
        else [
            {
                "duration_seconds": 2,
                "exit_category": "ValidationError",
                "validation_errors": [{"type": "json_invalid"}],
            },
            {"duration_seconds": 4, "exit_category": "ok"},
        ],
        "usage_totals": {"gen_ai.usage.input_tokens": 100} if usage else {},
        "findings": {"proposed": 3},
        "patch_check": {"status": "rejected"},
        "publication": [{"status": "published", "findings_published": 2}],
    }


class MetricsTests(unittest.TestCase):
    """Unknown usage and skips do not masquerade as measured successes."""

    def test_denominators_retries_publication_and_deduplication(self):
        """One success, one failure and one skip retain different denominators."""
        entries = [
            {"record": record()},
            {"record": record("b", "no_valid_output", False)},
            {"record": record("c", "skipped", False)},
            {"record": record()},
        ]
        row = dashboard.metrics(entries)[0]
        self.assertEqual(row["records"], 3)
        self.assertEqual(row["attempted_runs"], 2)
        self.assertEqual(row["success_rate"], 0.5)
        self.assertEqual(row["attempts_per_run"], 2)
        self.assertEqual(row["mean_run_seconds"], 6)
        self.assertEqual(row["runs_with_usage"], 1)
        self.assertEqual(row["usage_totals"]["gen_ai.usage.input_tokens"], 100)
        self.assertEqual(row["failure_categories"]["validation:json_invalid"], 2)

    def test_only_skips_have_unknown_success_and_latency(self):
        """No provider run means the rate is undefined."""
        row = dashboard.metrics([{"record": record(status="skipped", usage=False)}])[0]
        self.assertIsNone(row["success_rate"])
        self.assertIsNone(row["mean_run_seconds"])
        self.assertEqual(row["usage_totals"], {})

    def test_partial_publication_and_evidence_gaps_remain_visible(self):
        """A posted summary counts even if a later check API call failed."""
        value = record()
        value["publication"][0]["status"] = "partially_published"
        value["evidence_gap"] = True
        value["invalid_publication_receipts"] = 1
        row = dashboard.metrics([{"record": value}])[0]
        self.assertEqual(row["findings_published"], 2)
        self.assertEqual(row["evidence_gaps"], 1)
        self.assertEqual(row["invalid_publication_receipts"], 1)

    def test_build_reports_collection_errors_and_empty_state(self):
        """Public collection failure is persisted beside the empty dataset."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(dashboard, "collect", side_effect=OSError),
        ):
            dashboard.build(["shortlink"], Path(directory))
            summary = json.loads((Path(directory) / "metrics.json").read_text())
            self.assertEqual(summary["verified_records"], 0)
            self.assertEqual(len(summary["collection_errors"]), 1)
            self.assertEqual((Path(directory) / "records.jsonl").read_text(), "")


class CollectionTests(unittest.TestCase):
    """Asset names, signatures and repository identity constrain ingestion."""

    def test_releases_and_assets_are_paginated(self):
        """Full pages do not silently truncate the dataset."""
        with patch.object(dashboard, "api", side_effect=[[{}] * 100, [{}]]) as api:
            self.assertEqual(len(dashboard.pages("endpoint")), 101)
            self.assertIn("page=2", api.call_args.args[0])

    def test_signature_failure_missing_bundle_and_wrong_repo_are_excluded(self):
        """Only successful cryptographic verification contributes a record."""
        name = "ai-123-1-review.json"
        bundle = "ai-123-1-review.sigstore.json"
        assets = [
            {
                "name": value,
                "browser_download_url": f"https://github.com/{dashboard.OWNER}/shortlink/releases/download/tag/{value}",
            }
            for value in (name, bundle)
        ]
        for failure in (None, "signature", "bundle", "identity"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as directory,
            ):
                payload = record()
                if failure == "identity":
                    payload["identity"]["github_repository"] = "attacker/repo"

                content = json.dumps(payload)

                def download(_url, path, content=content):
                    path.write_text(content)

                verify = (
                    Mock(side_effect=subprocess.CalledProcessError(1, "gh"))
                    if failure == "signature"
                    else Mock(return_value="")
                )
                with (
                    patch.object(
                        dashboard,
                        "pages",
                        side_effect=[
                            [{"tag_name": "ai-telemetry-2026-10", "id": 7}],
                            assets[:1] if failure == "bundle" else assets,
                        ],
                    ),
                    patch.object(dashboard, "download", side_effect=download),
                    patch.object(dashboard, "gh", verify),
                ):
                    entries, errors = dashboard.collect("shortlink", Path(directory))
                self.assertEqual(len(entries), 0 if failure else 1)
                self.assertEqual(len(errors), 1 if failure else 0)
                if not failure:
                    self.assertIn("--signer-workflow", verify.call_args.args)
                    self.assertIn(dashboard.PREDICATE, verify.call_args.args)

    def test_download_rejects_foreign_host_and_oversized_record(self):
        """Public URLs are bounded and credentials are never added."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            with self.assertRaises(ValueError):
                dashboard.download("https://attacker.example/record", path)
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = b"x" * (dashboard.MAX_RECORD_BYTES + 1)
            with (
                patch.object(
                    dashboard.urllib.request, "urlopen", return_value=response
                ),
                self.assertRaises(ValueError),
            ):
                dashboard.download(
                    f"https://github.com/{dashboard.OWNER}/shortlink/asset", path
                )


if __name__ == "__main__":
    unittest.main()
