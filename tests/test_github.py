import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.verify_code_scanning import EXPECTED_PATHS, verify
from sonar4d import ci


class CiTests(unittest.TestCase):
    def test_only_completed_findings_are_normalized(self):
        for status, expected in ((0, 0), (1, 0), (2, 2), (3, 3), (130, 130)):
            with self.subTest(status=status), patch("sonar4d.ci.analyze", return_value=status) as analyze:
                self.assertEqual(ci.main(["root", "--output", "report"]), expected)
                analyze.assert_called_once_with(["root", "--output", "report"])

    def test_repository_relative_sarif_and_complete_finding_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repository"
            source = root / "Project/Sources/Methods/space #é.4dm"
            source.parent.mkdir(parents=True)
            source.write_text("a\nb\nc\nd\n", encoding="utf-8")
            config = root / "config.json"
            config.write_text('{"rules":{"4D-M001":{"maxLines":3}}}')
            output = base / "report"
            with contextlib.redirect_stdout(io.StringIO()):
                code = ci.main([str(root), "--config", str(config), "--output", str(output)])
            self.assertEqual(code, 0)
            analysis = json.loads((output / "analysis.json").read_text())
            self.assertTrue(analysis["complete"])
            self.assertEqual(len(analysis["findings"]), 1)
            report = json.loads((output / "findings.sarif").read_text())
            run = report["runs"][0]
            self.assertTrue(run["invocations"][0]["executionSuccessful"])
            self.assertEqual(run["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
                             "Project/Sources/Methods/space%20%23%C3%A9.4dm")

    def test_actual_failure_is_not_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with contextlib.redirect_stderr(io.StringIO()):
                code = ci.main([str(base / "missing"), "--output", str(base / "report")])
            self.assertEqual(code, 2)
            self.assertFalse((base / "report/findings.sarif").exists())


class GitHubVerificationTests(unittest.TestCase):
    def setUp(self):
        self.analysis = {
            "id": 42, "sarif_id": "upload-1", "ref": "refs/heads/example", "commit_sha": "abc",
            "results_count": 2, "analysis_key": "fixture", "category": "sonar-4d-fixture/", "error": "",
        }
        self.alerts = [
            {
                "number": index, "html_url": f"https://github.com/miyako/sonar-4d/security/code-scanning/{index}",
                "state": "open", "rule": {"id": "4D-M001"},
                "most_recent_instance": {
                    "ref": "refs/heads/example", "commit_sha": "abc",
                    "analysis_key": "fixture", "category": "sonar-4d-fixture/",
                    "location": {"path": path, "start_line": 1},
                },
            }
            for index, path in enumerate(sorted(EXPECTED_PATHS), 1)
        ]

    def verify(self, processing=None, analyses=None, alerts=None):
        return verify(
            {"processing_status": "complete"} if processing is None else processing,
            [self.analysis] if analyses is None else analyses,
            self.alerts if alerts is None else alerts,
            "upload-1", "refs/heads/example", "abc",
        )

    def test_processed_upload_and_exact_source_alerts(self):
        result = self.verify()
        self.assertEqual(result["analysisId"], 42)
        self.assertEqual(set(result["alerts"]), EXPECTED_PATHS)

    def test_pending_failed_missing_or_duplicate_analysis_is_not_success(self):
        for status in ("pending", "failed"):
            with self.assertRaises(ValueError):
                self.verify(processing={"processing_status": status})
        for analyses in ([], [self.analysis, self.analysis]):
            with self.assertRaises(ValueError):
                self.verify(analyses=analyses)

    def test_wrong_commit_ref_result_count_or_analysis_error_is_rejected(self):
        for field, value in (("commit_sha", "other"), ("ref", "refs/heads/other"),
                             ("results_count", 0), ("error", "rejected")):
            analysis = {**self.analysis, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(analyses=[analysis])

    def test_alert_must_be_open_on_correct_ref_commit_category_rule_path_and_line(self):
        self.assertRaises(ValueError, self.verify, alerts=[])
        for field, value in (("ref", "refs/heads/other"), ("commit_sha", "other"),
                             ("category", "another/"), ("analysis_key", "another")):
            alerts = deepcopy(self.alerts)
            alerts[0]["most_recent_instance"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(alerts=alerts)
        for update in ("state", "rule", "line", "path"):
            alerts = deepcopy(self.alerts)
            if update == "state":
                alerts[0]["state"] = "dismissed"
            elif update == "rule":
                alerts[0]["rule"]["id"] = "another-rule"
            elif update == "line":
                alerts[0]["most_recent_instance"]["location"]["start_line"] = 2
            else:
                alerts[0]["most_recent_instance"]["location"]["path"] = "missing.4dm"
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.verify(alerts=alerts)
        with self.assertRaises(ValueError):
            self.verify(alerts=self.alerts + [self.alerts[0]])


if __name__ == "__main__":
    unittest.main()
