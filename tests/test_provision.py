import contextlib
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sonar4d import provision


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.destination = self.root / "installed"
        for target, value in (("sys.platform", "darwin"), ("platform.machine", "arm64")):
            mocked = patch(f"sonar4d.provision.{target}", return_value=value) if target.endswith(
                "machine"
            ) else patch(f"sonar4d.provision.{target}", value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def archive(self, name="tool4d.app/Contents/MacOS/tool4d", link=None, member_type=None):
        archive = self.root / "archive.tar.xz"
        with tarfile.open(archive, "w:xz") as output:
            member = tarfile.TarInfo(name)
            member.mode = 0o755
            data = b"fixture-runtime"
            if link:
                member.type = tarfile.SYMTYPE
                member.linkname = link
                output.addfile(member)
            elif member_type:
                member.type = member_type
                output.addfile(member)
            else:
                member.size = len(data)
                output.addfile(member, io.BytesIO(data))
        return archive

    def run_install(self, archive, version=None):
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        result = SimpleNamespace(stdout=version or provision.RUNTIME_VERSION, stderr="")
        with (
            patch.object(provision, "ARCHIVE_SHA256", digest),
            patch.object(provision.subprocess, "run", return_value=result) as run,
        ):
            result = provision.install(self.destination, archive)
        return result, run

    def test_verified_install_and_provenance(self):
        archive = self.archive()
        result, run = self.run_install(archive)
        self.assertEqual(result["version"], provision.RUNTIME_VERSION)
        self.assertTrue((self.destination / provision.EXECUTABLE).is_file())
        self.assertTrue((self.destination / "provision.json").is_file())
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][0], "codesign")
        self.assertIn(provision.SIGNATURE_REQUIREMENT, commands[0])
        self.assertEqual(commands[1][-1], "--version")
        self.assertEqual(len(commands), 2)

    def test_digest_mismatch_and_html_stub_fail_before_execution(self):
        for content in (b"<html>login redirect</html>", b"wrong archive"):
            archive = self.root / "download"
            archive.write_bytes(content)
            with patch.object(provision.subprocess, "run") as run:
                with self.assertRaisesRegex(provision.ProvisionError, "SHA-256"):
                    provision.install(self.destination, archive)
                run.assert_not_called()
            self.assertFalse(self.destination.exists())

    def test_empty_and_oversized_downloads_are_rejected(self):
        archive = self.root / "download"
        archive.touch()
        with self.assertRaisesRegex(provision.ProvisionError, "size"):
            provision.verify_archive(archive)
        archive.write_bytes(b"large")
        with patch.object(provision, "MAX_ARCHIVE_BYTES", 1):
            with self.assertRaisesRegex(provision.ProvisionError, "size"):
                provision.verify_archive(archive)

    def test_unsafe_archive_paths_and_devices_are_rejected(self):
        for name in ("../escape", "/tool4d.app/escape", ".", "other/file"):
            archive = self.archive(name)
            with self.subTest(name=name), self.assertRaises(provision.ProvisionError):
                provision.extract_archive(archive, self.root / "staging")
        archive = self.archive("tool4d.app/device", member_type=tarfile.CHRTYPE)
        with self.assertRaisesRegex(provision.ProvisionError, "member type"):
            provision.extract_archive(archive, self.root / "staging")

    def test_escaping_symlink_is_rejected(self):
        archive = self.archive("tool4d.app/escape", link="../../outside")
        with self.assertRaises(tarfile.FilterError):
            provision.extract_archive(archive, self.root / "staging")
        self.assertFalse((self.root / "outside").exists())

    def test_unexpected_executable_and_version_are_rejected(self):
        archive = self.archive("tool4d.app/not-the-runtime")
        with self.assertRaisesRegex(provision.ProvisionError, "expected executable"):
            self.run_install(archive)
        archive = self.archive()
        with self.assertRaisesRegex(provision.ProvisionError, "Unexpected tool4d version"):
            self.run_install(archive, "21.2 unreviewed")
        self.assertFalse(self.destination.exists())

    def test_signature_failure_keeps_destination_absent(self):
        archive = self.archive()
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        with patch.object(provision, "ARCHIVE_SHA256", digest), patch.object(
            provision.subprocess, "run", side_effect=subprocess.CalledProcessError(
                1, ["codesign"], stderr="signature invalid"
            )
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                provision.install(self.destination, archive)
        self.assertFalse(self.destination.exists())
        self.assertEqual({path.name for path in self.root.iterdir()}, {"archive.tar.xz"})

    def test_existing_installation_is_not_overwritten(self):
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_text("unchanged")
        with patch.object(provision.subprocess, "run") as run:
            with self.assertRaisesRegex(provision.ProvisionError, "already exists"):
                provision.install(self.destination)
            run.assert_not_called()
        self.assertEqual(marker.read_text(), "unchanged")

    def test_unsupported_host_does_not_download(self):
        with (
            patch("sonar4d.provision.platform.machine", return_value="x86_64"),
            patch.object(provision.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(provision.ProvisionError, "macOS ARM64"):
                provision.install(self.destination)
            run.assert_not_called()

    def test_download_is_https_only_credential_free_and_bounded(self):
        archive = self.archive()
        data = archive.read_bytes()

        def execute(command, **_kwargs):
            if command[0] == "curl":
                Path(command[command.index("--output") + 1]).write_bytes(data)
            return SimpleNamespace(stdout=provision.RUNTIME_VERSION, stderr="")

        with patch.object(provision, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest()), patch.object(
            provision.subprocess, "run", side_effect=execute
        ) as run:
            provision.install(self.destination)
        command = run.call_args_list[0].args[0]
        self.assertEqual(command[:2], ["curl", "--disable"])
        self.assertIn("--fail", command)
        self.assertIn("--proto-redir", command)
        self.assertIn("--max-time", command)
        self.assertIn("--max-filesize", command)
        self.assertEqual(command[-1], provision.SOURCE_URL)
        self.assertNotIn("--user", command)
        self.assertNotIn("--retry", command)

    def test_cli_reports_download_failure_without_success(self):
        errors = io.StringIO()
        with patch.object(provision.subprocess, "run", side_effect=subprocess.CalledProcessError(
            22, ["curl"], stderr="404 download failed"
        )), contextlib.redirect_stderr(errors):
            self.assertEqual(provision.main(["--destination", str(self.destination)]), 2)
        self.assertIn("404 download failed", errors.getvalue())
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
