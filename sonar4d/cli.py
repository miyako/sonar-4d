from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__
from .dependencies import TOKEN_ENV
from .model import native_findings
from .native import NativeError, VERIFIED_BUILDS, VERIFIED_PLATFORM, probe
from .reports import sarif, sonar_generic
from .rules import analyze_source, load_config, sources


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="4D Community Plugin for SonarQube headless analyzer")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("root", type=Path, help="Repository/project package root")
    parser.add_argument("--output", required=True, type=Path, help="New report directory (must not exist)")
    parser.add_argument("--config", type=Path, help="Explicit JSON config; no implicit config discovery")
    parser.add_argument("--native-tool", type=Path, help="Opt in to local tool4d workspace syntax checking")
    parser.add_argument("--project", type=Path, help=".4DProject path relative to root")
    parser.add_argument("--native-timeout", type=float, default=15)
    parser.add_argument("--leave-unopened", action="append", default=[],
                        help="Root-relative source path not sent didOpen (still receives pull request)")
    parser.add_argument("--no-dependencies", action="store_true",
                        help="Do not install declared dependencies; a project that declares any is incomplete")
    parser.add_argument("--offline-dependencies", action="store_true",
                        help="Install dependencies from verified archives only, without network access")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Root must be a directory")
        if bool(args.native_tool) != bool(args.project):
            raise ValueError("--native-tool and --project must be supplied together")
        if args.leave_unopened and not args.native_tool:
            raise ValueError("--leave-unopened requires --native-tool")
        if (args.no_dependencies or args.offline_dependencies) and not args.native_tool:
            raise ValueError("Dependency options require --native-tool")
        if args.no_dependencies and args.offline_dependencies:
            raise ValueError("Choose --no-dependencies or --offline-dependencies, not both")
        if output == root or root in output.parents:
            raise ValueError("Report directory must be outside the analyzed root")
        if output.exists():
            raise ValueError("Output directory already exists; choose a fresh directory")
        config = load_config(args.config)
        paths = sources(root, config)
        unknown = set(args.leave_unopened) - {p.relative_to(root).as_posix() for p in paths}
        if unknown:
            raise ValueError(f"--leave-unopened paths are not in source scope: {sorted(unknown)}")
        findings = []
        for path in paths:
            findings.extend(analyze_source(
                path.relative_to(root).as_posix(), path.read_text(encoding="utf-8-sig"), config,
            ))
        native = None
        errors = []
        if args.native_tool:
            project = (root / args.project).resolve()
            project.relative_to(root)
            native = probe(
                root, project, args.native_tool.resolve(),
                [p for p in paths if p.relative_to(root).as_posix() not in args.leave_unopened],
                args.native_timeout, scope=paths,
                dependency_options=None if args.no_dependencies else {
                    "offline": args.offline_dependencies, "token": os.environ.get(TOKEN_ENV) or None,
                },
            )
            if not native["complete"]:
                errors.append(native["reason"])
            try:
                selected = {path.relative_to(root).as_posix() for path in paths}
                if native["version"] in VERIFIED_BUILDS and VERIFIED_PLATFORM:
                    findings.extend(native_findings(root, {
                        path: items for path, items in native["diagnostics"].items() if path in selected
                    }))
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(f"Invalid native diagnostics: {type(error).__name__}: {error}")
        complete = not errors and (native is None or native["complete"])
        findings = sorted(set(findings))
        output.mkdir(parents=True, exist_ok=False)
        _write(output / "analysis.json", {
            "schemaVersion": 1, "analyzerVersion": __version__,
            "complete": complete, "backends": ["custom"] + (["tool4d-syntax"] if native else []),
            "configuration": config,
            "scope": [p.relative_to(root).as_posix() for p in paths],
            "errors": errors, "findings": [finding.to_dict() for finding in findings],
        })
        _write(output / "findings.sarif", sarif(findings, complete, errors))
        if native is not None:
            _write(output / "native-observation.json", native)
        if complete:
            _write(output / "sonar-issues.json", sonar_generic(findings, complete))
        print(f"{'complete' if complete else 'INCOMPLETE'}: {len(findings)} finding(s); {output}")
        for error in errors:
            print(error, file=sys.stderr)
        return 2 if not complete else (1 if findings else 0)
    except (OSError, ValueError, KeyError, TypeError, NativeError) as error:
        print(f"sonar-4d: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
