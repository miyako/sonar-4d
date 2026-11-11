#!/usr/bin/env python3
"""Contract server, never a substitute for tool4d integration evidence."""
import json
import os
from pathlib import Path
import socket
import sys
import time


if "--version" in sys.argv:
    print(os.environ.get("SONAR4D_FAKE_VERSION", "21.0 contract-test"))
    sys.exit(0)

mode = os.environ.get("SONAR4D_FAKE_MODE", "diagnostics")
if mode == "exit":
    sys.exit(7)
if mode == "no-connect":
    time.sleep(20)
    sys.exit(0)

port = int(next(arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--lsp=")))
assert "--dataless" in sys.argv and "--skip-onstartup" in sys.argv
with socket.create_connection(("127.0.0.1", port)) as connection:
    reader = connection.makefile("rb")

    def receive():
        length = None
        while (line := reader.readline()) != b"\r\n":
            if not line:
                sys.exit(0)
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1])
        return json.loads(reader.read(length))

    def send(message):
        body = json.dumps({"jsonrpc": "2.0", **message}).encode()
        connection.sendall(f"Content-Length:{len(body)}\r\n\r\n".encode() + body)

    initialize = receive()
    if mode == "initialize-error":
        send({"id": initialize["id"], "error": {"code": -32603, "message": "test failure"}})
        time.sleep(20)
    if mode == "malformed":
        connection.sendall(b"Content-Length:no\r\n\r\n")
        time.sleep(20)
    send({"id": initialize["id"], "result": {"capabilities": {
        "diagnosticProvider": {"interFileDependencies": True, "workspaceDiagnostics": False}
    }}})
    while True:
        message = receive()
        if message.get("method") == "initialized" and mode == "disconnect":
            break
        if message.get("method") == "experimental/databaseInitialize":
            send({"id": message["id"], "result": {"valid": True, "id": message["params"]}})
        if message.get("method") == "experimental/prepareComponents":
            if mode == "dependency-fetch":
                send({"method": "dependency/fetch", "params": {}})
            else:
                send({"method": "dependency/installComponents/done", "params": message["params"]})
        if message.get("method") == "experimental/checkSyntax":
            if mode.startswith("workspace-"):
                project = Path(next(arg.split("=", 1)[1] for arg in sys.argv
                                    if arg.startswith("--project=")))
                items = []
                for source in sorted(project.parent.parent.rglob("*.4dm")):
                    diagnostics = [] if mode == "workspace-zero" else [{
                        "range": {"start": {"line": 0, "character": 2},
                                  "end": {"line": 0, "character": 6}},
                        "severity": 1, "message": "Workspace contract diagnostic",
                    }]
                    if mode == "workspace-invalid-range":
                        diagnostics[0]["range"]["start"]["character"] = -2
                    items.append({"uri": source.as_uri(), "kind": "full", "items": diagnostics})
                if mode == "workspace-partial":
                    items = []
                if mode == "workspace-duplicate":
                    items += items
                send({"id": message["id"], "result": {"items": items}})
            else:
                send({"id": message["id"], "result": None})
        if message.get("method") == "textDocument/diagnostic":
            if mode == "null":
                send({"id": message["id"], "result": None})
            else:
                diagnostic = {
                    "range": {"start": {"line": 0, "character": 2},
                              "end": {"line": 0, "character": 6}},
                    "severity": 1, "code": "TEST001", "message": "Contract diagnostic",
                }
                send({"id": message["id"], "result": {"kind": "full", "items": [diagnostic]}})
                if mode == "push":
                    send({"method": "textDocument/publishDiagnostics", "params": {
                        "uri": message["params"]["textDocument"]["uri"],
                        "diagnostics": [diagnostic],
                    }})
                if mode == "foreign-uri":
                    send({"method": "textDocument/publishDiagnostics", "params": {
                        "uri": Path("/outside.4dm").as_uri(), "diagnostics": [diagnostic],
                    }})
