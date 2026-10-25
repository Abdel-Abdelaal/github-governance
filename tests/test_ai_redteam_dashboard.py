"""Test signed scoreboard validation, privacy and durable evidence selection."""

import contextlib
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import ai_redteam_dashboard as redteam
import ai_telemetry_dashboard as dashboard


def summary():
    return {
        "generated_at": "2026-10-25T15:17:07+00:00",
        "attacks": 2,
        "defended": 1,
        "useful": 1,
        "results": [
            {"owasp": "LLM01", "defended": True, "useful": False},
            {"owasp": "LLM02", "defended": False, "useful": True},
        ],
    }


def verified():
    return [
        {
            "verificationResult": {
                "statement": {
                    "predicateType": redteam.PREDICATE,
                    "predicate": summary(),
                },
                "signature": {
                    "certificate": {
                        "sourceRepositoryVisibilityAtSigning": "public",
                        "runInvocationURI": f"https://github.com/{redteam.SOURCE}/actions/runs/123/attempts/1",
                    }
                },
            }
        }
    ]


class RedteamTests(unittest.TestCase):
    def test_signed_counts_are_consistent_and_unknown_is_not_zero(self):
        self.assertEqual(redteam.validate_summary(summary())["defended"], 1)
        for field, value in (
            ("attacks", True),
            ("defended", 2),
            ("useful", -1),
            ("results", []),
            ("generated_at", "2026-10-25T00:00:00"),
        ):
            with self.subTest(field=field):
                data = summary()
                data[field] = value
                with self.assertRaises((ValueError, TypeError)):
                    redteam.validate_summary(data)
        for field, value in (("defended", 1), ("owasp", "untrusted category")):
            data = summary()
            data["results"][0][field] = value
            with self.assertRaises(ValueError):
                redteam.validate_summary(data)

    def test_verification_uses_signed_predicate_and_certificate_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            subject = Path(temporary) / "results.json"
            bundle = Path(temporary) / "bundle.json"
            # This unsigned file has no authority over the displayed counts.
            subject.write_text('{"attacks":999,"defended":999}')
            bundle.write_text("bundle")
            cli = Mock(return_value=json.dumps(verified()))
            with patch.object(redteam, "gh", cli):
                measured = redteam.verify(subject, bundle)
            self.assertEqual(measured["attacks"], 2)
            self.assertEqual(measured["run_id"], 123)
            arguments = cli.call_args.args
            for required in (
                "--source-ref",
                "refs/heads/main",
                "--bundle",
                redteam.PREDICATE,
                redteam.SIGNER,
                "--deny-self-hosted-runners",
            ):
                self.assertIn(required, arguments)
            for change in (
                "private",
                "foreign-run",
                "predicate",
                "empty",
                "bad-signature",
            ):
                output = verified()
                if change == "private":
                    output[0]["verificationResult"]["signature"]["certificate"][
                        "sourceRepositoryVisibilityAtSigning"
                    ] = "private"
                elif change == "foreign-run":
                    output[0]["verificationResult"]["signature"]["certificate"][
                        "runInvocationURI"
                    ] = "https://attacker.example/run/123"
                elif change == "predicate":
                    output[0]["verificationResult"]["statement"]["predicateType"] = (
                        "wrong-type"
                    )
                elif change == "empty":
                    output = []
                mock = (
                    Mock(side_effect=subprocess.CalledProcessError(1, "gh"))
                    if change == "bad-signature"
                    else Mock(return_value=json.dumps(output))
                )
                with (
                    patch.object(redteam, "gh", mock),
                    self.assertRaises((ValueError, subprocess.CalledProcessError)),
                ):
                    redteam.verify(subject, bundle)

    def test_private_source_is_rejected_before_any_download(self):
        for metadata in ({"private": True}, {}):
            with (
                patch.object(redteam, "api", return_value=metadata),
                patch.object(redteam, "gh") as cli,
            ):
                with self.assertRaises(ValueError):
                    redteam.archive(Path("unused"))
                cli.assert_not_called()

    def test_existing_release_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "archive-plan.json").write_text(
                json.dumps(
                    {
                        "tag": "ai-redteam-2026-10",
                        "subject": "redteam-123.json",
                        "bundle": "redteam-123.sigstore.json",
                    }
                )
            )
            with (
                patch.object(
                    redteam, "api", side_effect=[{"private": False}, {"id": 7}]
                ),
                patch.object(
                    redteam,
                    "pages",
                    return_value=[
                        {"name": "redteam-123.json"},
                        {"name": "redteam-123.sigstore.json"},
                    ],
                ),
                patch.object(redteam, "gh") as cli,
            ):
                redteam.upload(directory)
                cli.assert_not_called()

    def test_missing_archive_remains_unknown(self):
        with (
            patch.object(redteam, "require_public_source"),
            patch.object(redteam, "pages", return_value=[]),
        ):
            self.assertFalse(redteam.latest()["available"])

    def test_staging_ignores_unsigned_summary_and_requires_the_signed_run_id(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.chdir(temporary):
            directory = Path(temporary) / "stage"

            def cli(*arguments):
                if arguments[:2] == ("run", "download"):
                    (directory / "redteam-results.json").write_text("signed subject")
                    (directory / "redteam-summary.json").write_text('{"attacks":999}')
                else:
                    digest = hashlib.sha256(b"signed subject").hexdigest()
                    Path(f"sha256-{digest}.jsonl").write_text("signed bundle")
                return ""

            measured = {**redteam.validate_summary(summary()), "run_id": 123}
            with (
                patch.object(
                    redteam,
                    "api",
                    side_effect=[
                        {"private": False},
                        {
                            "workflow_runs": [
                                {
                                    "id": 123,
                                    "head_branch": "main",
                                    "event": "workflow_dispatch",
                                }
                            ]
                        },
                    ],
                ),
                patch.object(redteam, "gh", side_effect=cli),
                patch.object(redteam, "verify", return_value=measured),
            ):
                counts, subject, bundle = redteam.archive(directory)
            self.assertEqual(counts["attacks"], 2)
            self.assertEqual(subject.read_text(), "signed subject")
            self.assertTrue(bundle.exists())

    def test_latest_archive_is_bound_to_its_signed_invocation(self):
        assets = [{"name": "redteam-123.json"}, {"name": "redteam-123.sigstore.json"}]
        for signed_id in (123, 999):
            with (
                patch.object(redteam, "require_public_source"),
                patch.object(
                    redteam,
                    "pages",
                    side_effect=[[{"id": 7, "tag_name": "ai-redteam-2026-10"}], assets],
                ),
                patch.object(redteam, "gh"),
                patch.object(
                    redteam, "verify", return_value={**summary(), "run_id": signed_id}
                ),
            ):
                if signed_id == 123:
                    self.assertTrue(redteam.latest()["available"])
                else:
                    with self.assertRaises(ValueError):
                        redteam.latest()

    def test_aggregate_keeps_redteam_collection_failure_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(dashboard, "collect", return_value=([], [])),
                patch.object(
                    redteam, "latest", side_effect=ValueError("untrusted remote output")
                ),
            ):
                dashboard.build(
                    ["public-repository"], Path(temporary), include_redteam=True
                )
            metrics = json.loads((Path(temporary) / "metrics.json").read_text())
            self.assertFalse(metrics["redteam"]["available"])
            self.assertEqual(len(metrics["collection_errors"]), 1)
            self.assertNotIn("untrusted remote output", json.dumps(metrics))


if __name__ == "__main__":
    unittest.main()
