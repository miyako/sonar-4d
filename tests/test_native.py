import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from sonar4d.cli import main
from sonar4d.model import native_findings
from sonar4d.native import NativeError, Wire, probe


class WireTests(unittest.TestCase):
    def test_fragmentation_multiframe_and_utf8_content_length(self):
        first, second = socket.socketpair()
        with first, second:
            wire = Wire(first, time.monotonic() + 1)
            body = json.dumps({"message": "😀"}, ensure_ascii=False).encode()
            frame = f"Content-Length:{len(body)}\r\n\r\n".encode() + body
            second.sendall(frame[:8])
            second.sendall(frame[8:] + frame)
            self.assertEqual(wire.receive(), {"message": "😀"})
            self.assertEqual(wire.receive(), {"message": "😀"})
            wire.send({"id": 1, "method": "test"})
            self.assertIn(b'"jsonrpc": "2.0"', second.recv(1000))

    def test_malformed_and_oversized_frames(self):
        for frame in (b"Content-Length:no\r\n\r\n", b"Content-Length:999999999\r\n\r\n",
                      b"Content-Length:1\r\nContent-Length:1\r\n\r\n0",
                      b"X" * 8193, b"X" * 8193 + b"\r\nContent-Length:2\r\n\r\n{}",
                      b"Content-Length:2\r\n\r\n[]"):
            first, second = socket.socketpair()
            with first, second:
                second.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
                second.sendall(frame)
                with self.assertRaises(NativeError):
                    Wire(first, time.monotonic() + 1).receive()


class NativeContractTests(unittest.TestCase):
    def setUp(self):
        supported = patch("sonar4d.native.VERIFIED_BUILDS", {"21.0 contract-test"})
        supported.start()
        self.addCleanup(supported.stop)
        cli_supported = patch("sonar4d.cli.VERIFIED_BUILDS", {"21.0 contract-test"})
        cli_supported.start()
        self.addCleanup(cli_supported.stop)
        for module in ("sonar4d.native", "sonar4d.cli"):
            supported_platform = patch(f"{module}.VERIFIED_PLATFORM", True)
            supported_platform.start()
            self.addCleanup(supported_platform.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.root = self.directory / "input"
        self.project = self.root / "Project/Probe.4DProject"
        self.project.parent.mkdir(parents=True)
        self.project.write_text("{}")
        self.source = self.root / "Project/space #é.4dm"
        self.source.write_text("😀oops\n", encoding="utf-8")
        self.tool = self.directory / "fake-tool4d"
        shutil.copyfile(Path(__file__).parent / "helpers/fake_tool4d.py", self.tool)
        self.tool.chmod(0o700)

    def call_probe(self, mode, version="21.0 contract-test"):
        with patch.dict(os.environ, {"SONAR4D_FAKE_MODE": mode, "SONAR4D_FAKE_VERSION": version}):
            return probe(self.root, self.project, self.tool, [self.source], 0.6)

    def test_real_transport_partial_diagnostic_is_not_complete(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.call_probe("push")
        self.assertFalse(result["complete"])
        self.assertEqual(len(native_findings(self.root, result["diagnostics"])), 1)
        self.assertIn("deadline", result["reason"])
        self.assertEqual(result["initialize"]["capabilities"]["diagnosticProvider"]["workspaceDiagnostics"],
                         False)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes()
                                  for p in self.root.rglob("*") if p.is_file()})

    def test_null_is_not_rewritten_as_clean(self):
        result = self.call_probe("null")
        self.assertFalse(result["complete"])
        self.assertEqual(result["diagnostics"], {})
        self.assertTrue(any("Non-standard" in event for event in result["events"]))
        self.assertIsNone(next(iter(result["pullResponses"].values()))["result"])

    def test_workspace_response_is_the_completion_boundary(self):
        result = self.call_probe("workspace-full")
        self.assertTrue(result["complete"], result["reason"])
        self.assertEqual(len(native_findings(self.root, result["diagnostics"])), 1)
        result = self.call_probe("workspace-zero")
        self.assertTrue(result["complete"], result["reason"])
        self.assertEqual(native_findings(self.root, result["diagnostics"]), [])

    def test_coverage_invalid_range_duplicate_and_build_fail_closed(self):
        for mode, reason in (("workspace-partial", "omitted files"),
                             ("workspace-invalid-range", "outside source"),
                             ("workspace-duplicate", "Duplicate")):
            with self.subTest(mode=mode):
                result = self.call_probe(mode)
                self.assertFalse(result["complete"])
                self.assertIn(reason, result["reason"])
        result = self.call_probe("workspace-full", "22.0 unknown-build")
        self.assertFalse(result["complete"])
        self.assertIn("not validated", result["reason"])

    def test_unvalidated_dependencies_prevent_complete_result(self):
        sources = self.root / "Project/Sources"
        sources.mkdir()
        (sources / "dependencies.json").write_text('{"dependencies":{"Absent":{}}}')
        result = self.call_probe("workspace-full")
        self.assertFalse(result["complete"])
        self.assertIn("dependencies are not validated", result["reason"])

    def test_failure_modes(self):
        for mode, reason in (("exit", "exited"), ("no-connect", "deadline"),
                             ("initialize-error", "initialize failed"), ("malformed", "Malformed"),
                             ("disconnect", ""), ("foreign-uri", "outside isolated"),
                             ("dependency-fetch", "dependency fetching")):
            with self.subTest(mode=mode):
                result = self.call_probe(mode)
                self.assertFalse(result["complete"])
                self.assertIn(reason, result["reason"])
                if mode == "disconnect":
                    self.assertTrue(any(word in result["reason"] for word in
                                        ("disconnected", "BrokenPipeError", "ConnectionResetError")))

    def test_version_missing_executable_and_invalid_timeout(self):
        result = self.call_probe("diagnostics", "20.6 old")
        self.assertIn("21+ required", result["reason"])
        result = probe(self.root, self.project, self.directory / "missing", [], 0.5)
        self.assertIn("FileNotFoundError", result["reason"])
        for seconds in (0, -1, 301, float("nan"), float("inf")):
            with self.assertRaises(NativeError):
                probe(self.root, self.project, self.tool, [], seconds)
        with patch("sonar4d.native.subprocess.run", side_effect=subprocess.CalledProcessError(
            -11, [str(self.tool), "--version"]
        )):
            result = probe(self.root, self.project, self.tool, [], 0.5)
        self.assertFalse(result["complete"])
        self.assertIn("SIGSEGV", result["reason"])

    def test_incomplete_cli_outputs_no_sonar_report(self):
        output = self.directory / "out"
        with patch.dict(os.environ, {"SONAR4D_FAKE_MODE": "diagnostics"}), (
            contextlib.redirect_stdout(io.StringIO())
        ), contextlib.redirect_stderr(io.StringIO()):
            code = main([str(self.root), "--output", str(output), "--native-tool", str(self.tool),
                         "--project", "Project/Probe.4DProject", "--native-timeout", "0.6",
                         "--leave-unopened", "Project/space #é.4dm"])
        self.assertEqual(code, 2)
        self.assertFalse((output / "sonar-issues.json").exists())
        analysis = json.loads((output / "analysis.json").read_text())
        self.assertFalse(analysis["complete"])
        self.assertEqual(len(analysis["findings"]), 1)
        observation = json.loads((output / "native-observation.json").read_text())
        self.assertEqual(observation["opened"], [])
        self.assertFalse(json.loads((output / "findings.sarif").read_text())
                         ["runs"][0]["invocations"][0]["executionSuccessful"])

    def test_complete_native_cli_exports_and_exit_codes(self):
        for mode, expected_code, count in (("workspace-full", 1, 1), ("workspace-zero", 0, 0)):
            output = self.directory / mode
            with patch.dict(os.environ, {"SONAR4D_FAKE_MODE": mode}), (
                contextlib.redirect_stdout(io.StringIO())
            ), contextlib.redirect_stderr(io.StringIO()):
                code = main([str(self.root), "--output", str(output), "--native-tool", str(self.tool),
                             "--project", "Project/Probe.4DProject", "--native-timeout", "0.6"])
            self.assertEqual(code, expected_code)
            self.assertTrue(json.loads((output / "analysis.json").read_text())["complete"])
            report = json.loads((output / "sonar-issues.json").read_text())
            self.assertEqual(len(report["issues"]), count)


if __name__ == "__main__":
    unittest.main()
