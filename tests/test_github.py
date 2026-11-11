import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.verify_code_scanning import EXPECTED_PATHS, verify, verify_native_fixture, verify_report
from sonar4d import ci
from sonar4d.provision import RUNTIME_VERSION


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
        self.report = {"complete": True, "errors": [], "findings": []}
        for index, alert in enumerate(self.alerts):
            instance = alert["most_recent_instance"]
            instance["message"] = {"text": f"diagnostic {index}"}
            self.report["findings"].append({
                "path": instance["location"]["path"], "start_line": 1,
                "rule_id": "4D-M001", "message": instance["message"]["text"],
            })

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

    def check_report(self, alerts=None, report=None, analysis=None):
        return verify_report(
            {"processing_status": "complete"}, [self.analysis if analysis is None else analysis],
            self.alerts if alerts is None else alerts,
            "upload-1", "refs/heads/example", "abc", self.report if report is None else report,
        )

    def test_report_verifier_preserves_multiple_messages_on_one_source_line(self):
        path = self.report["findings"][0]["path"]
        self.report["findings"][1]["path"] = path
        self.alerts[1]["most_recent_instance"]["location"]["path"] = path
        self.assertEqual(len(self.check_report()["alerts"]), 2)

    def test_report_verifier_rejects_missing_duplicate_wrong_and_incomplete_results(self):
        for alerts in ([], self.alerts[:1], self.alerts + [self.alerts[0]]):
            with self.assertRaises(ValueError):
                self.check_report(alerts=alerts)
        for update in ("message", "rule", "line", "state"):
            alerts = deepcopy(self.alerts)
            if update == "message":
                alerts[0]["most_recent_instance"]["message"]["text"] = "different"
            elif update == "rule":
                alerts[0]["rule"]["id"] = "different"
            elif update == "line":
                alerts[0]["most_recent_instance"]["location"]["start_line"] = 2
            else:
                alerts[0]["state"] = "fixed"
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.check_report(alerts=alerts)
        for report in ({**self.report, "complete": False}, {**self.report, "errors": ["failure"]}):
            with self.assertRaises(ValueError):
                self.check_report(report=report)

    def test_empty_report_requires_zero_processed_results_and_no_current_alerts(self):
        report = {"complete": True, "errors": [], "findings": []}
        analysis = {**self.analysis, "results_count": 0}
        self.assertEqual(self.check_report(alerts=[], report=report, analysis=analysis)["alerts"], [])
        with self.assertRaises(ValueError):
            self.check_report(report=report, analysis=analysis)
        with self.assertRaises(ValueError):
            self.check_report(alerts=[], report=report)


class NativeFixtureVerificationTests(unittest.TestCase):
    def setUp(self):
        prefix = "tests/fixtures/native/Project/Sources/Methods/"
        self.report = {
            "backends": ["custom", "tool4d-syntax"],
            "findings": [
                {"path": prefix + file, "start_line": line, "rule_id": "4D-N001", "message": message}
                for file, line, message in [
                    ("Caller.4dm", 2, "Impossible to cast Text to Integer."),
                    ("Opened.4dm", 3, "Missing expression"), ("Opened.4dm", 3, "if without end if"),
                    ("Unopened.4dm", 2, "Missing expression"), ("Unopened.4dm", 2, "if without end if"),
                ]
            ],
        }
        self.observation = {
            "complete": True, "version": RUNTIME_VERSION,
            "opened": [prefix + "Opened.4dm", prefix + "Caller.4dm"],
        }

    def test_exact_native_errors_and_clean_scope(self):
        verify_native_fixture(self.report, self.observation, "errors")
        verify_native_fixture(
            {"backends": ["tool4d-syntax"], "findings": [],
             "scope": ["tests/fixtures/native-clean/Project/Sources/Methods/Clean.4dm"]},
            self.observation, "clean",
        )

    def test_shifted_missing_errors_wrong_build_and_accidentally_opened_files_fail(self):
        report = deepcopy(self.report)
        report["findings"][0]["start_line"] = 1
        for changed in (report, {**self.report, "findings": self.report["findings"][:-1]},
                        {**self.report, "backends": ["custom"]}):
            with self.assertRaises(ValueError):
                verify_native_fixture(changed, self.observation, "errors")
        for observation in (
            {**self.observation, "complete": False},
            {**self.observation, "version": "21 R3 build 21R3.100164"},
            {**self.observation, "opened": self.observation["opened"] + ["Unopened.4dm"]},
        ):
            with self.assertRaises(ValueError):
                verify_native_fixture(self.report, observation, "errors")


if __name__ == "__main__":
    unittest.main()
