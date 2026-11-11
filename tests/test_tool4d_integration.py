"""Opt-in real dependency smoke test; it does not assert compiler coverage."""

import hashlib
import os
from pathlib import Path
import unittest

from sonar4d.native import probe
from sonar4d.native import VERIFIED_BUILDS
from sonar4d.model import native_findings


@unittest.skipUnless(os.environ.get("TOOL4D_NATIVE_INTEGRATION"), "Set TOOL4D_NATIVE_INTEGRATION to tool4d")
class Tool4DIntegrationTests(unittest.TestCase):
    def test_isolated_native_fixture_observation(self):
        root = (Path(__file__).parent / "fixtures/native").resolve()

        def hashes():
            return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in root.rglob("*") if p.is_file()}

        before = hashes()
        result = probe(
            root, root / "Project/Probe.4DProject",
            Path(os.environ["TOOL4D_NATIVE_INTEGRATION"]).resolve(),
            [root / "Project/Sources/Methods/Opened.4dm",
             root / "Project/Sources/Methods/Caller.4dm"], 8,
        )
        self.assertEqual(before, hashes())
        self.assertIsNotNone(result["initialize"], result["reason"])
        self.assertIn("diagnosticProvider", result["initialize"]["capabilities"])
        self.assertEqual(result["complete"], result["version"] in VERIFIED_BUILDS, result["reason"])
        self.assertIn("Project/Sources/Methods/Unopened.4dm", result["diagnostics"])
        self.assertNotIn("Project/Sources/Methods/Unopened.4dm", result["opened"])
        self.assertTrue(any("Text to Integer" in item["message"]
                            for item in result["diagnostics"]["Project/Sources/Methods/Caller.4dm"]))
        self.assertTrue(result["diagnostics"]["Project/Sources/Methods/Unopened.4dm"])
        if result["complete"]:
            findings = native_findings(root, result["diagnostics"])
            self.assertEqual(len(findings), 5)
            self.assertEqual({f.start_line for f in findings if f.path.endswith("Caller.4dm")}, {2})
            self.assertEqual({f.start_line for f in findings if f.path.endswith("Unopened.4dm")}, {2})

    def test_native_clean_fixture_requires_full_response(self):
        root = (Path(__file__).parent / "fixtures/native-clean").resolve()
        source = root / "Project/Sources/Methods/Clean.4dm"
        result = probe(root, root / "Project/Clean.4DProject",
                       Path(os.environ["TOOL4D_NATIVE_INTEGRATION"]).resolve(), [source], 15)
        self.assertIsNotNone(result["initialize"], result["reason"])
        self.assertEqual(result["complete"], result["version"] in VERIFIED_BUILDS, result["reason"])
        self.assertEqual(result["diagnostics"], {"Project/Sources/Methods/Clean.4dm": []})


if __name__ == "__main__":
    unittest.main()
