"""Response-bound workspace syntax checking with explicit coverage validation."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit

from .model import native_findings


VERIFIED_BUILDS = {"21.0 build 21.100301"}
VERIFIED_PLATFORM = sys.platform == "darwin" and platform.machine() == "arm64"

class NativeError(Exception):
    pass


class Wire:
    def __init__(self, stream: socket.socket, deadline: float):
        self.stream = stream
        self.deadline = deadline
        self.buffer = b""

    def send(self, message: dict) -> None:
        body = json.dumps({"jsonrpc": "2.0", **message}).encode()
        self.stream.settimeout(max(0.001, self.deadline - time.monotonic()))
        self.stream.sendall(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)

    def receive(self) -> dict:
        while True:
            if b"\r\n\r\n" in self.buffer:
                header, body = self.buffer.split(b"\r\n\r\n", 1)
                if len(header) > 8192:
                    raise NativeError("LSP header exceeds 8 KiB")
                lengths = [
                    line.split(b":", 1)[1].strip()
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                ]
                if len(lengths) != 1 or not lengths[0].isdigit():
                    raise NativeError("Malformed LSP Content-Length")
                size = int(lengths[0])
                if size > 16 * 1024 * 1024:
                    raise NativeError("LSP message exceeds 16 MiB")
                if len(body) >= size:
                    payload, self.buffer = body[:size], body[size:]
                    message = json.loads(payload)
                    if not isinstance(message, dict):
                        raise NativeError("LSP message must be an object")
                    return message
            elif len(self.buffer) > 8192:
                raise NativeError("LSP header exceeds 8 KiB")
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("LSP observation deadline reached")
            self.stream.settimeout(remaining)
            chunk = self.stream.recv(65536)
            if not chunk:
                raise NativeError("tool4d disconnected before a complete workspace syntax response")
            self.buffer += chunk


def _copy_workspace(root: Path, destination: Path) -> None:
    # Never follow links out of the selected workspace or copy live data files.
    for path in root.rglob("*"):
        if path.is_symlink():
            raise NativeError(f"Native isolation refuses symlinks: {path.relative_to(root)}")
    excluded = {".git", "Data", "DerivedData", "Logs", "node_modules", ".venv", "reports"}
    shutil.copytree(
        root, destination,
        ignore=lambda _directory, names: [
            name for name in names
            if name in excluded or Path(name).suffix.lower() in {".4dd", ".4dindx", ".journal"}
        ],
    )


def probe(
    root: Path, project: Path, tool: Path, opened: list[Path], seconds: float,
    scope: list[Path] | None = None,
) -> dict:
    """Use a full custom workspace response, never silence, to establish coverage."""
    result = {
        "complete": False,
        "reason": "No complete workspace syntax response received",
        "version": None,
        "initialize": None,
        "diagnostics": {},
        "events": [],
        "opened": [p.relative_to(root).as_posix() for p in opened],
        "pullResponses": {},
        "syntaxResponse": None,
        "databaseResponse": None,
        "filesStatusResponse": None,
    }
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 300:
        raise NativeError("Native timeout must be greater than 0 and at most 300 seconds")
    project_relative = project.relative_to(root)
    if not project.is_file() or project.suffix.lower() != ".4dproject":
        raise NativeError("--project must identify an existing .4DProject under the root")
    try:
        selected_sources = scope if scope is not None else [
            source for source in root.rglob("*")
            if source.is_file() and source.suffix.lower() == ".4dm"
        ]
        expected = {source.relative_to(root).as_posix() for source in selected_sources}
        dependency_blocker = None
        dependencies = project.parent / "Sources/dependencies.json"
        if dependencies.is_file():
            manifest = json.loads(dependencies.read_text(encoding="utf-8-sig"))
            if not isinstance(manifest, dict) or not isinstance(manifest.get("dependencies", {}), dict):
                raise NativeError("Invalid 4D dependencies.json")
            if manifest.get("dependencies"):
                dependency_blocker = "Declared 4D dependencies are not validated by this foundation"
        for folder in ("Components", "Plugins"):
            directory = project.parent.parent / folder
            if directory.is_dir() and any(directory.iterdir()):
                dependency_blocker = f"Native {folder} dependencies are not validated by this foundation"
        version = subprocess.run(
            [str(tool), "--version"], capture_output=True, text=True, timeout=min(seconds, 10),
            check=True,
        ).stdout.strip()
        result["version"] = version
        major = re.match(r"(\d+)", version)
        if not major or int(major[1]) < 21:
            raise NativeError(f"tool4d 21+ required; received {version!r}")
        with tempfile.TemporaryDirectory(prefix="sonar4d-") as directory:
            temporary = Path(directory).resolve()
            workspace = temporary / "workspace"
            _copy_workspace(root, workspace)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                deadline = time.monotonic() + seconds
                listener.settimeout(seconds)
                with (temporary / "stdout.log").open("wb") as stdout, (
                    temporary / "stderr.log"
                ).open("wb") as stderr:
                    child = subprocess.Popen(
                        [str(tool), f"--project={workspace / project_relative}",
                         f"--lsp={listener.getsockname()[1]}", "--dataless", "--skip-onstartup"],
                        cwd=workspace, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    )
                    try:
                        # Poll only the owned child while awaiting its reverse TCP connection.
                        listener.settimeout(min(0.1, seconds))
                        while True:
                            if child.poll() is not None:
                                raise NativeError(f"tool4d exited before connection ({child.returncode})")
                            if time.monotonic() >= deadline:
                                raise TimeoutError("tool4d connection timed out")
                            try:
                                connection, address = listener.accept()
                                break
                            except socket.timeout:
                                continue
                        if address[0] != "127.0.0.1":
                            connection.close()
                            raise NativeError("Rejected non-loopback LSP peer")
                        with connection:
                            wire = Wire(connection, deadline)
                            wire.send({"id": 1, "method": "initialize", "params": {
                                "processId": os.getpid(), "rootUri": workspace.as_uri(),
                                "rootPath": str(workspace),
                                "clientInfo": {"name": "sonar-4d", "version": "0.1.0"},
                                "capabilities": {
                                    "general": {"positionEncodings": ["utf-16"]},
                                    "textDocument": {"publishDiagnostics": {"relatedInformation": True}},
                                },
                                "initializationOptions": {
                                    "diagnostics": {"enable": True, "scope": "Document"},
                                    "dependencies": {"enable": False},
                                },
                                "workspaceFolders": [{"uri": workspace.as_uri(), "name": root.name}],
                            }})
                            initialized = False
                            pull_requests = {}
                            documents_started = False

                            def start_documents() -> None:
                                nonlocal documents_started
                                if documents_started:
                                    return
                                documents_started = True
                                for source in opened:
                                    copy = workspace / source.relative_to(root)
                                    wire.send({"method": "textDocument/didOpen", "params": {
                                        "textDocument": {
                                            "uri": copy.as_uri(), "languageId": "4d", "version": 1,
                                            "text": source.read_text(encoding="utf-8-sig"),
                                        },
                                    }})
                                syntax_source = next(iter(opened), next(iter(selected_sources), None))
                                if syntax_source is not None:
                                    document = {"uri": (
                                        workspace / syntax_source.relative_to(root)
                                    ).as_uri()}
                                    wire.send({"method": "experimental/didChangeActiveTextEditor",
                                               "params": document})
                                    wire.send({"id": 2, "method": "experimental/checkSyntax",
                                               "params": document})
                                wire.send({"id": 4, "method": "experimental/filesStatus", "params": {}})
                                if capabilities.get("diagnosticProvider"):
                                    selected = [workspace / source.relative_to(root)
                                                for source in selected_sources]
                                    for request_id, source in enumerate(
                                        sorted(selected), start=1000
                                    ):
                                        relative = source.relative_to(workspace).as_posix()
                                        pull_requests[request_id] = relative
                                        wire.send({"id": request_id, "method": "textDocument/diagnostic",
                                                   "params": {"textDocument": {"uri": source.as_uri()}}})

                            while True:
                                message = wire.receive()
                                method = message.get("method")
                                if method is not None and not isinstance(method, str):
                                    raise NativeError("Invalid JSON-RPC method")
                                if not isinstance(message.get("id"), (int, str, type(None))):
                                    raise NativeError("Invalid JSON-RPC response id")
                                if message.get("id") == 1 and not method:
                                    if "error" in message:
                                        raise NativeError(f"initialize failed: {message['error']}")
                                    initialization = message.get("result")
                                    if not isinstance(initialization, dict) or not isinstance(
                                        initialization.get("capabilities"), dict
                                    ):
                                        raise NativeError("Malformed initialize result")
                                    result["initialize"] = initialization
                                    capabilities = initialization["capabilities"]
                                    if capabilities.get("positionEncoding", "utf-16") != "utf-16":
                                        raise NativeError("tool4d selected unsupported position encoding")
                                    wire.send({"method": "initialized", "params": {}})
                                    initialized = True
                                    wire.send({"id": 3, "method": "experimental/databaseInitialize",
                                               "params": {"uri": (workspace / project_relative).as_uri()}})
                                elif message.get("id") in {3, 4} and not method:
                                    key = "databaseResponse" if message["id"] == 3 else "filesStatusResponse"
                                    result[key] = {
                                        key: value for key, value in message.items() if key != "jsonrpc"
                                    }
                                    if message["id"] == 4 and isinstance(message.get("result"), list):
                                        result[key]["result"] = [
                                            {name: value for name, value in item.items() if name not in {"text", "base"}}
                                            for item in message["result"] if isinstance(item, dict)
                                        ]
                                    if message["id"] == 3:
                                        if message.get("error", {}).get("code") == -32601:
                                            result["events"].append(
                                                "databaseInitialize unavailable; checking the launched project"
                                            )
                                            start_documents()
                                            continue
                                        database = message.get("result")
                                        if not isinstance(database, dict) or database.get("valid") is not True:
                                            raise NativeError("tool4d database initialization did not succeed")
                                        wire.send({"method": "experimental/prepareComponents",
                                                   "params": database["id"]})
                                elif method == "dependency/installComponents/before":
                                    result["events"].append(method)
                                    wire.send({"method": "dependency/installComponents",
                                               "params": message["params"]})
                                elif method == "dependency/installComponents/done":
                                    result["events"].append(method)
                                    start_documents()
                                elif method == "dependency/fetch":
                                    raise NativeError("Project requires dependency fetching; network fetch is disabled")
                                elif message.get("id") == 2 and not method:
                                    result["syntaxResponse"] = {
                                        key: value for key, value in message.items() if key != "jsonrpc"
                                    }
                                    report = message.get("result")
                                    if isinstance(report, dict) and isinstance(report.get("items"), list):
                                        covered = set()
                                        for document in report["items"]:
                                            uri = urlsplit(document["uri"])
                                            if uri.scheme != "file" or uri.netloc not in {"", "localhost"}:
                                                raise NativeError("Non-local syntax diagnostic URI")
                                            path = Path(unquote(uri.path)).resolve()
                                            relative = path.relative_to(workspace.resolve()).as_posix()
                                            if document.get("kind") != "full":
                                                raise NativeError("Non-full workspace syntax report")
                                            if relative in covered:
                                                raise NativeError("Duplicate workspace syntax document report")
                                            covered.add(relative)
                                            result["diagnostics"][relative] = document["items"]
                                        native_findings(root, {
                                            path: items for path, items in result["diagnostics"].items()
                                            if path in expected
                                        })
                                        missing = sorted(expected - covered)
                                        if not expected:
                                            result["reason"] = "No source files selected for native syntax checking"
                                        elif missing:
                                            result["reason"] = f"Workspace syntax response omitted files: {missing}"
                                        elif dependency_blocker:
                                            result["reason"] = dependency_blocker
                                        elif version not in VERIFIED_BUILDS:
                                            result["reason"] = f"Native completion is not validated for build: {version}"
                                        elif not VERIFIED_PLATFORM:
                                            result["reason"] = "Native completion is validated only on macOS ARM64"
                                        else:
                                            result["complete"] = True
                                            result["reason"] = None
                                        break
                                    else:
                                        result["events"].append("Non-standard experimental/checkSyntax response")
                                elif message.get("id") in pull_requests and not method:
                                    relative = pull_requests[message["id"]]
                                    result["pullResponses"][relative] = {
                                        key: value for key, value in message.items() if key != "jsonrpc"
                                    }
                                    if "error" in message:
                                        result["events"].append(f"pull error: {relative}")
                                    else:
                                        report = message.get("result", {})
                                        if isinstance(report, dict) and report.get("kind") == "full":
                                            result["diagnostics"][relative] = report["items"]
                                        else:
                                            result["events"].append(
                                                f"Non-standard initial diagnostic report: {relative}"
                                            )
                                elif method == "textDocument/publishDiagnostics":
                                    params = message["params"]
                                    uri = urlsplit(params["uri"])
                                    if uri.scheme != "file" or uri.netloc not in {"", "localhost"}:
                                        raise NativeError("Non-local diagnostic URI")
                                    path = Path(unquote(uri.path)).resolve()
                                    try:
                                        relative = path.relative_to(workspace.resolve()).as_posix()
                                    except ValueError as error:
                                        raise NativeError("Diagnostic URI is outside isolated workspace") from error
                                    result["diagnostics"][relative] = params["diagnostics"]
                                elif method and "id" in message:
                                    if method == "workspace/configuration":
                                        wire.send({"id": message["id"], "result": [
                                            None for _ in message.get("params", {}).get("items", [])
                                        ]})
                                    else:
                                        wire.send({"id": message["id"], "error": {
                                            "code": -32601, "message": f"Unsupported client request: {method}",
                                        }})
                                    result["events"].append(method)
                                elif method:
                                    result["events"].append(method)
                                if not initialized and child.poll() is not None:
                                    raise NativeError("tool4d exited during initialization")
                    finally:
                        if child.poll() is None:
                            child.terminate()
                            try:
                                child.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                child.kill()
                                child.wait(timeout=3)
                        result["processExitCode"] = child.returncode
                        if child.returncode not in {0, -15, -9}:
                            result["complete"] = False
                            result["reason"] = f"tool4d exited unexpectedly ({child.returncode})"
                        for name in ("stdout", "stderr"):
                            with (temporary / f"{name}.log").open("rb") as log:
                                log.seek(0, os.SEEK_END)
                                size = log.tell()
                                log.seek(max(0, size - 16384))
                                result[f"{name}Tail"] = log.read().decode("utf-8", errors="replace")
    except TimeoutError:
        result["reason"] = (
            "Observation deadline reached before a complete workspace syntax response"
        )
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, NativeError) as error:
        result["reason"] = f"{type(error).__name__}: {error}"
    return result
