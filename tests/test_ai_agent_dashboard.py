"""Only verified public controller evidence can affect the agent view."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import ai_agent_dashboard as agents


def record(**changes):
    return {
        "schema": "agent-dispatcher.attempt/v1",
        "record_id": "a" * 36,
        "repo": "Abdel-Abdelaal/shortlink",
        "stage": "plan",
        "provider": "codex",
        "model": "gpt-6.1-sol",
        "outcome": "ok",
        "usage": None,
        "duration_ms": 1000,
        **changes,
    }


def document(records=None):
    records = [record()] if records is None else records
    payload = json.dumps(records, sort_keys=True, ensure_ascii=True, allow_nan=False)
    return {
        "schema_version": 1,
        "record_type": "agent-batch/v1",
        "records": records,
        "batch_id": hashlib.sha256(payload.encode()).hexdigest(),
        "ingestion": {"github_run_id": "123"},
    }


def verification(data):
    return [
        {
            "verificationResult": {
                "statement": {
                    "predicateType": agents.PREDICATE,
                    "predicate": {"batch_id": data["batch_id"]},
                },
                "signature": {
                    "certificate": {
                        "sourceRepositoryVisibilityAtSigning": "public",
                        "runInvocationURI": f"https://github.com/{agents.SOURCE}/actions/runs/123/attempts/1",
                    }
                },
            }
        }
    ]


class AgentEvidenceTests(unittest.TestCase):
    def test_exact_predicate_main_ref_and_public_certificate_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            subject, bundle = (
                Path(directory) / "data.json",
                Path(directory) / "bundle.json",
            )
            data = document()
            subject.write_text(json.dumps(data))
            bundle.write_text("{}")
            proof = verification(data)
            with patch.object(agents, "gh", return_value=json.dumps(proof)) as cli:
                self.assertEqual(agents.verify(subject, bundle), data)
                self.assertIn("refs/heads/main", cli.call_args.args)
                self.assertIn(agents.SIGNER, cli.call_args.args)
                self.assertIn(agents.PREDICATE, cli.call_args.args)
            proof[0]["verificationResult"]["signature"]["certificate"][
                "sourceRepositoryVisibilityAtSigning"
            ] = "private"
            with (
                patch.object(agents, "gh", return_value=json.dumps(proof)),
                self.assertRaises(ValueError),
            ):
                agents.verify(subject, bundle)

    def test_tampered_batch_hash_is_rejected_after_signature_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            subject, bundle = (
                Path(directory) / "data.json",
                Path(directory) / "bundle.json",
            )
            data = document()
            proof = verification(data)
            data["records"][0]["outcome"] = "forged"
            subject.write_text(json.dumps(data))
            bundle.write_text("{}")
            with (
                patch.object(agents, "gh", return_value=json.dumps(proof)),
                self.assertRaises(ValueError),
            ):
                agents.verify(subject, bundle)

    def test_private_names_are_excluded_and_bad_batch_is_atomic(self):
        data = document([record(repo="Abdel-Abdelaal/private"), record()])
        self.assertEqual(
            len(agents.public_records(data, {"Abdel-Abdelaal/shortlink"})), 1
        )
        data["records"].append(record(stage="execute_shell"))
        with self.assertRaises(ValueError):
            agents.public_records(data, {"Abdel-Abdelaal/shortlink"})

    def test_collection_requires_signatures_and_deduplicates_retry_ids(self):
        name = "agent-batch-" + "a" * 64 + "-123.json"
        assets = [
            {"name": name, "browser_download_url": "trusted"},
            {"name": name[:-5] + ".sigstore.json", "browser_download_url": "trusted"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(agents, "api", return_value={"private": False}),
                patch.object(
                    agents,
                    "pages",
                    side_effect=[
                        [{"tag_name": "ai-telemetry-2026-10", "id": 1}],
                        assets,
                    ],
                ),
                patch.object(agents, "download"),
                patch.object(
                    agents, "verify", return_value=document([record(), record()])
                ),
            ):
                entries, errors = agents.collect(Path(directory), ["shortlink"])
                self.assertEqual(len(entries), 1)
                self.assertEqual(errors, [])
            with (
                patch.object(agents, "api", return_value={"private": False}),
                patch.object(
                    agents,
                    "pages",
                    side_effect=[
                        [{"tag_name": "ai-telemetry-2026-10", "id": 1}],
                        assets,
                    ],
                ),
                patch.object(agents, "download"),
                patch.object(
                    agents, "verify", side_effect=subprocess.CalledProcessError(1, "gh")
                ),
            ):
                entries, errors = agents.collect(Path(directory), ["shortlink"])
                self.assertEqual(entries, [])
                self.assertEqual(len(errors), 1)

    def test_unknown_usage_and_controller_events_stay_distinct_from_ci(self):
        summary = agents.metrics(
            [
                {"record": record()},
                {"record": record(record_id="b" * 36, usage={"input_tokens": 4})},
                {"record": record(stage="route", duration_ms=None)},
            ]
        )
        self.assertEqual(summary["records"], 3)
        planning = next(row for row in summary["rows"] if row["stage"] == "plan")
        self.assertEqual(planning["records_with_usage"], 1)
        self.assertEqual(planning["usage_totals"], {"input_tokens": 4})
        self.assertEqual(planning["mean_seconds"], 1)
        routing = next(row for row in summary["rows"] if row["stage"] == "route")
        self.assertIsNone(routing["mean_seconds"])
        self.assertIn("not local execution", summary["assurance"])

    def test_download_does_not_follow_arbitrary_input_hosts(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(agents.urllib.request, "urlopen") as opened,
        ):
            with self.assertRaises(ValueError):
                agents.download(
                    "https://attacker.invalid/data", Path(directory) / "data"
                )
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
