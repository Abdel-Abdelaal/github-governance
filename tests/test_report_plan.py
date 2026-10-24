"""Test idempotent plan reporting without touching GitHub."""

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("report_plan", Path(__file__).parents[1] / "tools/report_plan.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class PlanReportTests(unittest.TestCase):
    def run_report(self, environment, output):
        with patch.dict(os.environ, environment), patch.object(Path, "read_text", return_value="No secrets in plan"), patch.object(Path, "write_text"), patch.object(report, "gh", side_effect=output) as gh:
            report.main()
            return gh.call_args_list

    def test_drift_creates_one_issue(self):
        calls = self.run_report({"EVENT_NAME": "schedule", "PLAN_RESULT": "2"}, ["[]", ""])
        self.assertEqual(calls[-1].args[:3], ("issue", "create", "--title"))

    def test_clean_plan_closes_existing_drift(self):
        calls = self.run_report({"EVENT_NAME": "schedule", "PLAN_RESULT": "0"}, ['[{"number": 7, "title": "Drift detected"}]', ""])
        self.assertEqual(calls[-1].args[:3], ("issue", "close", "7"))

    def test_pr_reuses_only_its_own_comment(self):
        comments = '[[{"id": 9, "body": "<!-- terraform-plan --> old", "user": {"login": "github-actions[bot]"}}]]'
        calls = self.run_report({"EVENT_NAME": "pull_request", "PR_NUMBER": "3", "GH_REPO": "owner/repo", "PLAN_RESULT": "2"}, [comments, ""])
        self.assertEqual(calls[-1].args[:4], ("api", "-X", "PATCH", "repos/owner/repo/issues/comments/9"))


if __name__ == "__main__":
    unittest.main()
