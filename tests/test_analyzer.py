import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from sonar4d.cli import main
from sonar4d.model import Finding, native_findings, utf16_length, validate_position
from sonar4d.reports import sarif, sonar_generic
from sonar4d.rules import analyze_source, load_config, sources


FIXTURES = Path(__file__).parent / "fixtures"


class RuleTests(unittest.TestCase):
    def test_exact_threshold_positive_negative(self):
        config = load_config(FIXTURES / "rules/config.json")
        for folder, expected in (("positive", 1), ("negative", 0)):
            path = next((FIXTURES / "rules" / folder).glob("*.4dm"))
            findings = analyze_source(path.name, path.read_text(), config)
            self.assertEqual(len(findings), expected)
            if findings:
                self.assertEqual(findings[0].rule_id, "4D-M001")
                self.assertEqual(findings[0].message,
                                 "Source file has 4 physical lines; configured maximum is 3.")

    def test_physical_lines(self):
        config = {"enabled": True, "maxLines": 1}
        for text in ("", "one", "one\n", "one\r\n", "one\r", "one\u2028two"):
            self.assertEqual(analyze_source("a.4dm", text, config), [])
        for text in ("one\ntwo", "one\n\n", "one\r\ntwo\r\n", "one\rtwo"):
            self.assertEqual(len(analyze_source("a.4dm", text, config)), 1)

    def test_disabled(self):
        self.assertEqual(analyze_source("a.4dm", "a\nb", {"enabled": False, "maxLines": 1}), [])

    def test_invalid_configs(self):
        for value in ([], {"other": 1}, {"rules": []}, {"rules": {"unknown": {}}},
                      {"rules": {"4D-M001": {"enabled": "false"}}},
                      {"rules": {"4D-M001": {"maxLines": True}}},
                      {"rules": {"4D-M001": {"maxLines": 0}}},
                      {"rules": {"4D-M001": {"maxLines": 100001}}},
                      {"rules": {"4D-M001": {"unknown": 1}}}, {"exclude": [1]}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_exclusion_and_no_sources(self):
        config = load_config(None)
        config["exclude"] = ["*Long.4dm"]
        with self.assertRaisesRegex(ValueError, "No .4dm"):
            sources((FIXTURES / "rules/positive").resolve(), config)

    def test_symlink_scope_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "linked").symlink_to(FIXTURES.resolve(), target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                sources(root, load_config(None))


class LocationTests(unittest.TestCase):
    def test_utf16_and_surrogate_boundaries(self):
        self.assertEqual(utf16_length("a😀b"), 4)
        validate_position("a😀b\n", 0, 3)
        validate_position("a😀b\n", 1, 0)
        for line, column in ((0, 2), (0, 5), (1, 1), (2, 0), (-1, 0)):
            with self.assertRaises(ValueError):
                validate_position("a😀b\n", line, column)

    def test_native_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "emoji.4dm").write_text("😀oops\n", encoding="utf-8")
            findings = native_findings(root, {"emoji.4dm": [{
                "range": {"start": {"line": 0, "character": 2},
                          "end": {"line": 0, "character": 6}},
                "message": "bad", "code": 42, "severity": 1,
            }]})
            finding = findings[0]
            self.assertEqual((finding.start_line, finding.start_column, finding.end_column), (1, 2, 6))
            self.assertEqual((finding.category, finding.level), ("reliability", "error"))
            self.assertEqual(finding.message, "[42] bad")
            with self.assertRaises(ValueError):
                native_findings(root, {"../outside.4dm": []})

    def test_native_whole_line_sentinels_are_not_fake_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "syntax.4dm").write_text("If (\n")
            for start in (-1, 0):
                finding = native_findings(root, {"syntax.4dm": [{
                    "range": {"start": {"line": 0, "character": start},
                              "end": {"line": 0, "character": 2147483647}},
                    "message": "Missing expression", "severity": 1,
                }]})[0]
                self.assertEqual(finding.location_precision, "line")
                self.assertEqual(sonar_generic([finding], True)["issues"][0]["primaryLocation"]["textRange"],
                                 {"startLine": 1})

    def test_invalid_common_model(self):
        for path, line, column, end_line, end_column in (
            ("/a", 1, 0, 1, 1), ("../a", 1, 0, 1, 1), ("a\\b", 1, 0, 1, 1),
            ("a", 0, 0, 1, 1), ("a", 1, -1, 1, 1), ("a", 2, 0, 1, 0),
            ("a", 1, 0, 2, -1), ("a", True, 0, 1, 1),
        ):
            with self.assertRaises(ValueError):
                Finding(path, line, column, end_line, end_column, "4D-M001", "message")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.finding = Finding("Project/space #é.4dm", 2, 3, 2, 7, "4D-N001", "bad",
                               "reliability", "error")

    def test_sarif_coordinates_and_uri(self):
        report = sarif([self.finding], True, [])
        self.assertEqual(report["version"], "2.1.0")
        run = report["runs"][0]
        self.assertEqual(run["columnKind"], "utf16CodeUnits")
        location = run["results"][0]["locations"][0]["physicalLocation"]
        self.assertEqual(location["artifactLocation"]["uri"], "Project/space%20%23%C3%A9.4dm")
        self.assertEqual(location["region"],
                         {"startLine": 2, "startColumn": 4, "endLine": 2, "endColumn": 8})
        self.assertTrue(run["invocations"][0]["executionSuccessful"])

    def test_sonar_classification(self):
        for category, expected_type in (("reliability", "BUG"), ("maintainability", "CODE_SMELL"),
                                        ("security", "VULNERABILITY")):
            finding = Finding("a.4dm", 1, 2, 1, 6, "4D-N001", "message", category)
            report = sonar_generic([finding], True)
            self.assertEqual(report["rules"][0]["type"], expected_type)
            self.assertEqual(report["rules"][0]["impacts"][0]["softwareQuality"], category.upper())
            self.assertEqual(report["issues"][0]["ruleId"], "4D-N001")
            self.assertEqual(report["issues"][0]["primaryLocation"]["textRange"]["startColumn"], 2)

    def test_empty_line_and_zero_width_locations(self):
        finding = Finding("empty.4dm", 1, 0, 1, 0, "4D-M001", "message")
        self.assertEqual(sonar_generic([finding], True)["issues"][0]["primaryLocation"]["textRange"],
                         {"startLine": 1})
        self.assertEqual(sarif([finding], True, [])["runs"][0]["results"][0]["locations"][0]
                         ["physicalLocation"]["region"], {"startLine": 1})

    def test_partial_never_sonar_success(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            sonar_generic([self.finding], False)
        invocation = sarif([], False, ["timeout"])["runs"][0]["invocations"][0]
        self.assertFalse(invocation["executionSuccessful"])
        self.assertEqual(invocation["toolExecutionNotifications"][0]["message"]["text"], "timeout")

    def test_determinism_and_deduplication(self):
        other = Finding("a.4dm", 1, 0, 1, 0, "4D-M001", "message")
        self.assertEqual(sarif([self.finding, other, other], True, []),
                         sarif([other, self.finding], True, []))
        self.assertEqual(sonar_generic([self.finding, other], True),
                         sonar_generic([other, self.finding, other], True))


class CliTests(unittest.TestCase):
    def run_cli(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(args)

    def test_exit_codes_and_report_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            for scope, code in (("positive", 1), ("negative", 0)):
                output = Path(directory) / scope
                args = [str(FIXTURES / "rules" / scope), "--output", str(output),
                        "--config", str(FIXTURES / "rules/config.json")]
                self.assertEqual(self.run_cli(args), code)
                self.assertEqual(sorted(p.name for p in output.iterdir()),
                                 ["analysis.json", "findings.sarif", "sonar-issues.json"])
                self.assertTrue(json.loads((output / "analysis.json").read_text())["complete"])
                self.assertEqual(self.run_cli(args), 2)

    def test_no_reports_inside_input_or_missing_root(self):
        root = FIXTURES / "rules/negative"
        self.assertEqual(self.run_cli([str(root), "--output", str(root / "reports")]), 2)
        self.assertFalse((root / "reports").exists())
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_cli([str(Path(directory) / "missing"),
                                           "--output", str(Path(directory) / "out")]), 2)

    def test_cli_misconfiguration(self):
        with tempfile.TemporaryDirectory() as directory:
            base = [str(FIXTURES / "rules/negative"), "--output", str(Path(directory) / "out")]
            for extra in (["--project", "x.4DProject"], ["--native-tool", "/missing"],
                          ["--leave-unopened", "Short.4dm"]):
                self.assertEqual(self.run_cli(base + extra), 2)


if __name__ == "__main__":
    unittest.main()
