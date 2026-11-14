"""Install declared 4D dependencies into the isolated workspace copy.

Resolution, download and verification are done by the 4D dependency manager emulation in miyako/4D
(`.github/actions/dependencies/fetch_dependencies.py`), fetched at a pinned commit and checked against a
recorded SHA-256. tool4d's LSP mode does not load components from the dependency cache, so each resolved
package is copied into the copy's `Components/` folder. Existing `Components/` entries keep precedence.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request


SCRIPT_COMMIT = "6ab718ea695ef043f568552a685d53eff8d3226c"
SCRIPT_SHA256 = "67b5c178a8c004f50d22a5d51b0514c983e2a02f76f2d0b6f7b3ba597246fb0a"
SCRIPT_URL = (
    f"https://raw.githubusercontent.com/miyako/4D/{SCRIPT_COMMIT}"
    "/.github/actions/dependencies/fetch_dependencies.py"
)
TOKEN_ENV = "SONAR4D_GITHUB_TOKEN"
LOCK_USER = "sonar4d"
MAX_SCRIPT_BYTES = 1024 * 1024
TIMEOUT = 600


class DependencyError(Exception):
    pass


def declared(project: Path) -> bool:
    """True when dependencies.json declares at least one dependency."""
    manifest_path = project.parent / "Sources/dependencies.json"
    if not manifest_path.is_file():
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("dependencies", {}), dict):
        raise DependencyError("Invalid 4D dependencies.json")
    return bool(manifest.get("dependencies"))


def carry_environment(package: Path, root: Path, workspace: Path) -> str | None:
    """Make the analysis copy use the environment4d.json that 4D would use for `package`.

    4D reads environment4d.json from the package folder or the nearest parent folder. A file at or below
    `root` is part of the copy already. A nearest file above `root` is copied to the copy's root (`workspace`);
    otherwise an empty one is written there, so the emulator never reaches an unrelated file above the
    temporary folder. Returns a warning when an outside file is applied.
    """
    target = workspace / "environment4d.json"
    if target.exists():
        return None
    for folder in [package, *package.parents]:
        candidate = folder / "environment4d.json"
        if candidate.is_file():
            if folder.is_relative_to(root):
                return None
            shutil.copyfile(candidate, target)
            return ("environment4d.json above the analysis root was applied to the analysis copy; "
                    "its relative paths resolve from the analysis root")
    target.write_text('{"dependencies": {}}\n', encoding="utf-8")
    return None


def cache_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches/sonar-4d"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "sonar-4d"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "sonar-4d"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def script_path() -> Path:
    """Return the pinned emulator script, downloading it once; it is re-hashed on every use."""
    path = cache_directory() / "scripts" / f"fetch_dependencies-{SCRIPT_COMMIT[:12]}.py"
    if path.is_file() and _sha256(path.read_bytes()) == SCRIPT_SHA256:
        return path
    with urllib.request.urlopen(SCRIPT_URL, timeout=60) as response:
        data = response.read(MAX_SCRIPT_BYTES + 1)
    if len(data) > MAX_SCRIPT_BYTES or _sha256(data) != SCRIPT_SHA256:
        raise DependencyError("The dependency manager script does not match the reviewed pin")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(data)
    os.replace(temporary.name, path)
    return path


def runtime_version(version: str) -> str:
    match = re.match(r"(\d+(?:R\d+)?(?:\.\d+)?)", version)
    if not match:
        raise DependencyError(f"Cannot read the tool4d version from {version!r}")
    return match[1]


def install(package: Path, version: str, temporary: Path, *, offline: bool = False,
            token: str | None = None, script: Path | None = None,
            lock_key: str | None = None) -> tuple[dict, str | None]:
    """Resolve and install dependencies for the workspace copy `package`.

    Returns a summary for the observation and a blocker reason, or None when every dependency is installed.
    Like 4D, offline runs resolve version anchors from a lock file; sonar-4d keeps the last lock written
    for `lock_key` (the analyzed project) in its cache, because each analysis uses a fresh copy.
    """
    script = script or script_path()
    output = temporary / "dependencies-report.json"
    lock = package / f"userPreferences.{LOCK_USER}" / "dependencies-lock.json"
    saved = cache_directory() / "locks" / f"{_sha256(lock_key.encode())[:32]}.json" if lock_key else None
    if saved and saved.is_file() and not lock.exists():
        lock.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(saved, lock)
    command = [sys.executable, str(script), "--project", str(package), "--runtime", "tool4d",
               "--runtime-version", runtime_version(version), "--home", str(temporary / "home"),
               "--user", LOCK_USER, "--output", str(output)]
    if offline:
        command.append("--offline")
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"GITHUB_OUTPUT", "DEPENDENCIES_GITHUB_TOKEN"}}
    if token:
        environment["DEPENDENCIES_GITHUB_TOKEN"] = token
    completed = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=TIMEOUT)
    if completed.returncode not in {0, 3} or not output.is_file():
        detail = (completed.stderr.strip().splitlines() or [f"exit {completed.returncode}"])[-1]
        return {"token": bool(token)}, f"Dependency resolution failed: {detail}"
    report = json.loads(output.read_text(encoding="utf-8"))
    if saved and lock.is_file() and not report.get("incomplete"):
        saved.parent.mkdir(parents=True, exist_ok=True)
        staging = saved.with_suffix(f".{os.getpid()}.tmp")
        shutil.copyfile(lock, staging)
        os.replace(staging, saved)
    summary = {
        "resolved": report.get("resolved", {}), "token": bool(token), "offline": offline,
        "archiveStore": report.get("archiveStore"), "warnings": report.get("warnings", []),
        "incomplete": report.get("incomplete", []), "installed": [], "providedByComponents": [],
        "reuse": {},
    }
    problems = [f"{entry['name']}: {entry['reason']}" for entry in summary["incomplete"]]
    components = package / "Components"
    for name, entry in sorted(report.get("dependencies", {}).items()):
        if entry.get("overloadedBy"):
            summary["providedByComponents"].append(name)
            continue
        if entry.get("source") == "local":
            problems.append(f"{name}: local dependencies are not installed by sonar-4d")
            continue
        source = entry.get("path")
        if not source or not Path(source).is_dir():
            if not any(problem.startswith(f"{name}:") for problem in problems):
                problems.append(f"{name}: not installed")
            continue
        components.mkdir(exist_ok=True)
        shutil.copytree(source, components / f"{name}.4dbase")
        summary["installed"].append(name)
        summary["reuse"][name] = entry.get("reuse")
    if problems:
        return summary, "Declared 4D dependencies are incomplete: " + "; ".join(problems)
    return summary, None
