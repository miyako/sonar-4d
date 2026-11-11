"""Install one verified public tool4d archive without credentials or global changes."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import subprocess
import sys
import tarfile
import tempfile


SOURCE_URL = (
    "https://resources-download.4d.com/release/21.x/21.0/latest/mac/tool4d_arm64.tar.xz"
)
ARCHIVE_SHA256 = "4a7ef4b26091bf77761956f9b939c83f441902ce77c25952e0eda18a36497b07"
RUNTIME_VERSION = "21.0 build 21.100422"
EXECUTABLE = Path("tool4d.app/Contents/MacOS/tool4d")
SIGNATURE_REQUIREMENT = (
    '=anchor apple generic and certificate leaf[subject.OU] = "37UG5W39Z2" '
    'and identifier "com.4D.tool"'
)
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024


class ProvisionError(Exception):
    pass


def verify_archive(archive: Path) -> None:
    if not 0 < archive.stat().st_size <= MAX_ARCHIVE_BYTES:
        raise ProvisionError("tool4d archive has an invalid size")
    digest = hashlib.sha256()
    with archive.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != ARCHIVE_SHA256:
        raise ProvisionError(
            "tool4d archive SHA-256 does not match the reviewed pin; "
            "the upstream latest archive may have changed. No runtime was installed."
        )


def extract_archive(archive: Path, destination: Path) -> None:
    if not hasattr(tarfile, "data_filter"):
        raise ProvisionError("Safe extraction requires Python with tarfile data filters")
    with tarfile.open(archive, "r:xz") as source:
        members = source.getmembers()
        if not members or len(members) > 4096 or sum(m.size for m in members) > MAX_EXPANDED_BYTES:
            raise ProvisionError("tool4d archive exceeds the expected extraction bounds")
        for member in members:
            path = PurePosixPath(member.name)
            if not path.parts or path.is_absolute() or ".." in path.parts or path.parts[0] != "tool4d.app":
                raise ProvisionError("tool4d archive contains an unexpected path")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ProvisionError("tool4d archive contains an unsupported member type")
        source.extractall(destination, members=members, filter="data")


def install(destination: Path, archive: Path | None = None) -> dict:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ProvisionError("Automatic tool4d provisioning is verified only for macOS ARM64")
    destination = destination.resolve()
    if destination.exists():
        raise ProvisionError("tool4d destination already exists; choose a fresh directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sonar4d-install-", dir=destination.parent) as directory:
        staging = Path(directory).resolve()
        if archive is None:
            archive = staging / "tool4d.tar.xz"
            subprocess.run(
                ["curl", "--disable", "--fail", "--location", "--silent", "--show-error",
                 "--proto", "=https", "--proto-redir", "=https", "--max-time", "120",
                 "--max-filesize", str(MAX_ARCHIVE_BYTES), "--output", str(archive), SOURCE_URL],
                check=True, capture_output=True, text=True, timeout=125,
            )
        verify_archive(archive)
        runtime = staging / "runtime"
        extract_archive(archive, runtime)
        tool = runtime / EXECUTABLE
        if not tool.is_file() or tool.is_symlink() or not os.access(tool, os.X_OK):
            raise ProvisionError("Archive does not contain the expected executable tool4d")
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", "--test-requirement",
             SIGNATURE_REQUIREMENT, str(runtime / "tool4d.app")],
            check=True, capture_output=True, text=True, timeout=30,
        )
        version = subprocess.run(
            [str(tool), "--version"], check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        if version != RUNTIME_VERSION:
            raise ProvisionError(f"Unexpected tool4d version: {version!r}")
        provenance = {
            "sourceUrl": SOURCE_URL, "archiveSha256": ARCHIVE_SHA256, "version": version,
            "platform": "macos-arm64", "signatureRequirement": SIGNATURE_REQUIREMENT,
            "toolPath": str(destination / EXECUTABLE),
        }
        (runtime / "provision.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        if destination.exists():
            raise ProvisionError("tool4d destination was created concurrently; refusing replacement")
        runtime.rename(destination)
        return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision the pinned public tool4d runtime")
    parser.add_argument("--destination", type=Path, required=True, help="New installation directory")
    parser.add_argument("--archive", type=Path, help="Optional cached archive; the same digest pin applies")
    args = parser.parse_args(argv)
    try:
        provenance = install(args.destination, args.archive)
    except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError, ProvisionError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else ""
        print(f"sonar-4d provision: {type(error).__name__}: {error}" + (f"\n{detail}" if detail else ""),
              file=sys.stderr)
        return 2
    print(json.dumps(provenance, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
