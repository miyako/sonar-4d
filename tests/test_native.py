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

from sonar4d import dependencies
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
            return probe(self.root, self.project, self.tool, [self.source], 2)

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

    def fake_dependencies(self, report, code=0):
        log = self.directory / "dependencies-log.json"
        environment = patch.dict(os.environ, {"SONAR4D_FAKE_DEPENDENCIES": json.dumps(report),
                                              "SONAR4D_FAKE_DEPENDENCIES_EXIT": str(code),
                                              "SONAR4D_FAKE_DEPENDENCIES_LOG": str(log),
                                              "GITHUB_OUTPUT": str(self.directory / "github-output"),
                                              "DEPENDENCIES_GITHUB_TOKEN": "ambient"})
        environment.start()
        self.addCleanup(environment.stop)
        cache = patch("sonar4d.dependencies.cache_directory", return_value=self.directory / "sonar4d-cache")
        cache.start()
        self.addCleanup(cache.stop)
        return Path(__file__).parent / "helpers/fake_dependencies.py", log

    def cached_package(self, name):
        folder = self.directory / "cache" / name
        (folder / "Project").mkdir(parents=True)
        (folder / "Project" / f"{name}.4DProject").write_text("{}")
        return str(folder)

    def test_install_copies_resolved_packages_and_keeps_existing_components(self):
        script, log = self.fake_dependencies({
            "resolved": {"A": "1.0.0"}, "incomplete": [], "dependencies": {
                "A": {"source": "github", "path": self.cached_package("A"), "reuse": "verified archive"},
                "B": {"source": "github", "path": self.cached_package("B"), "overloadedBy": "Components"}}})
        package = self.directory / "package"
        package.mkdir()
        summary, blocker = dependencies.install(package, "21.0 contract-test", self.directory, token="secret",
                                                script=script)
        self.assertIsNone(blocker)
        self.assertTrue((package / "Components/A.4dbase/Project/A.4DProject").is_file())
        self.assertFalse((package / "Components/B.4dbase").exists())
        self.assertEqual((summary["installed"], summary["providedByComponents"]), (["A"], ["B"]))
        self.assertNotIn("secret", json.dumps(summary))
        invocation = json.loads(log.read_text())
        self.assertEqual(invocation["token"], "secret")
        self.assertFalse(invocation["githubOutput"])
        self.assertEqual(invocation["argv"][invocation["argv"].index("--runtime-version") + 1], "21.0")
        (package / "other").mkdir()
        dependencies.install(package / "other", "21.0 contract-test", self.directory, script=script)
        self.assertIsNone(json.loads(log.read_text())["token"])

    def test_lock_is_kept_per_project_for_offline_runs(self):
        script, log = self.fake_dependencies({"incomplete": [], "dependencies": {}})
        packages = [self.directory / f"lock{index}" for index in range(3)]
        for package in packages:
            package.mkdir()
        with patch.dict(os.environ, {"SONAR4D_FAKE_DEPENDENCIES_LOCK": '{"dependencies":{"A":{"tag":"1.2"}}}'}):
            dependencies.install(packages[0], "21.0 contract-test", self.directory, script=script, lock_key="/p")
        self.assertIsNone(json.loads(log.read_text())["lock"])
        dependencies.install(packages[1], "21.0 contract-test", self.directory, script=script, lock_key="/p",
                             offline=True)
        self.assertIn('"1.2"', json.loads(log.read_text())["lock"])
        dependencies.install(packages[2], "21.0 contract-test", self.directory, script=script, lock_key="/other")
        self.assertIsNone(json.loads(log.read_text())["lock"])

    def test_install_blockers(self):
        cases = (({"incomplete": [{"name": "A", "reason": "no release"}], "dependencies": {}}, 3, "A: no release"),
                 ({"incomplete": [], "dependencies": {"L": {"source": "local", "path": None}}}, 0,
                  "L: local dependencies"),
                 ({"incomplete": [], "dependencies": {"M": {"source": "github", "path": None}}}, 0,
                  "M: not installed"),
                 ({}, 2, "Dependency resolution failed: fetch_dependencies: usage error"))
        for index, (report, code, reason) in enumerate(cases):
            with self.subTest(reason=reason):
                script, _ = self.fake_dependencies(report, code)
                package = self.directory / f"package{index}"
                package.mkdir()
                _, blocker = dependencies.install(package, "21.0 contract-test", self.directory, script=script)
                self.assertIn(reason, blocker)

    def test_script_pin_mismatch_is_rejected(self):
        cache = self.directory / "script-cache"
        with patch("sonar4d.dependencies.cache_directory", return_value=cache), \
             patch("sonar4d.dependencies.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b"tampered"
            with self.assertRaises(dependencies.DependencyError):
                dependencies.script_path()
        self.assertFalse(any(cache.rglob("*.py")))

    def test_probe_installs_declared_dependencies(self):
        sources = self.root / "Project/Sources"
        sources.mkdir()
        (sources / "dependencies.json").write_text('{"dependencies":{"A":{"github":"o/A"}}}')
        script, log = self.fake_dependencies({"resolved": {"A": "1.0.0"}, "incomplete": [], "dependencies": {
            "A": {"source": "github", "path": self.cached_package("A"), "reuse": "downloaded"}}})
        with patch.dict(os.environ, {"SONAR4D_FAKE_MODE": "workspace-full",
                                     "SONAR4D_FAKE_VERSION": "21.0 contract-test"}):
            result = probe(self.root, self.project, self.tool, [self.source], 2,
                           dependency_options={"script": script})
        self.assertTrue(result["complete"], result["reason"])
        self.assertEqual(result["dependencies"]["installed"], ["A"])
        package = Path(json.loads(log.read_text())["argv"][2])
        self.assertNotEqual(package, self.root)
        self.assertFalse((self.root / "Components").exists())
        (sources / "dependencies.json").write_text('{"dependencies":{"L":{}}}')
        self.fake_dependencies({"incomplete": [], "dependencies": {"L": {"source": "local", "path": None}}})
        with patch.dict(os.environ, {"SONAR4D_FAKE_MODE": "workspace-full",
                                     "SONAR4D_FAKE_VERSION": "21.0 contract-test"}):
            result = probe(self.root, self.project, self.tool, [self.source], 2,
                           dependency_options={"script": script})
        self.assertFalse(result["complete"])
        self.assertIn("L: local dependencies", result["reason"])

    def test_probe_applies_the_environment_4d_would_use(self):
        # 4D uses environment4d.json in the package folder or the nearest parent folder. The analysis copy must
        # see the same file: one above the analysis root is not copied with the workspace, and the emulator
        # must not pick up an unrelated file above the temporary folder.
        sources = self.root / "Project/Sources"
        sources.mkdir()
        (sources / "dependencies.json").write_text('{"dependencies":{"A":{"github":"o/A"}}}')
        script, log = self.fake_dependencies({"incomplete": [], "dependencies": {}})
        above = self.directory / "environment4d.json"
        inside = self.root / "environment4d.json"
        override = '{"dependencies":{"A":{"github":"o/A","tag":"1.0.0"}}}'

        def run():
            with patch.dict(os.environ, {"SONAR4D_FAKE_MODE": "workspace-full",
                                         "SONAR4D_FAKE_VERSION": "21.0 contract-test"}):
                result = probe(self.root, self.project, self.tool, [self.source], 2,
                               dependency_options={"script": script})
            self.assertTrue(result["complete"], result["reason"])
            invocation = json.loads(log.read_text())
            copy = Path(invocation["argv"][invocation["argv"].index("--project") + 1])
            self.assertIsNotNone(invocation["environment"])
            self.assertTrue(Path(invocation["environment"]).is_relative_to(copy.parent),
                            "the emulator looked above the temporary folder")
            return result, invocation

        above.write_text(override)
        result, invocation = run()
        self.assertEqual(invocation["environmentText"], override)
        self.assertIn("environment4d.json above the analysis root", " ".join(result["dependencies"]["warnings"]))
        self.assertFalse(inside.exists())

        inside.write_text('{"dependencies":{}}')
        result, invocation = run()
        self.assertEqual(invocation["environmentText"], '{"dependencies":{}}')
        self.assertEqual(result["dependencies"]["warnings"], [])

        inside.unlink()
        above.unlink()
        result, invocation = run()
        self.assertEqual(json.loads(invocation["environmentText"]), {"dependencies": {}})
        self.assertEqual(result["dependencies"]["warnings"], [])
        self.assertFalse(inside.exists())

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
                         "--project", "Project/Probe.4DProject", "--native-timeout", "2",
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
            errors = io.StringIO()
            with patch.dict(os.environ, {"SONAR4D_FAKE_MODE": mode}), (
                contextlib.redirect_stdout(io.StringIO())
            ), contextlib.redirect_stderr(errors):
                code = main([str(self.root), "--output", str(output), "--native-tool", str(self.tool),
                             "--project", "Project/Probe.4DProject", "--native-timeout", "2"])
            self.assertEqual(code, expected_code, errors.getvalue())
            self.assertTrue(json.loads((output / "analysis.json").read_text())["complete"])
            report = json.loads((output / "sonar-issues.json").read_text())
            self.assertEqual(len(report["issues"]), count)


if __name__ == "__main__":
    unittest.main()
