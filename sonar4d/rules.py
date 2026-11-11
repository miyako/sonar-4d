"""A deliberately small, syntax-independent maintainability rule."""

import fnmatch
import json
from pathlib import Path

from .model import Finding


DEFAULT_MAX_LINES = 200
RULES = {
    "4D-M001": {
        "name": "Limit 4D source file length",
        "description": "Split source files exceeding the configured physical-line limit.",
        "category": "maintainability",
    },
    "4D-N001": {
        "name": "tool4d native diagnostic",
        "description": "A diagnostic observed from the local tool4d LSP server; not a security finding.",
        "category": "reliability",
    },
}


def load_config(path: Path | None) -> dict:
    config = {} if path is None else json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or set(config) - {"rules", "exclude"}:
        raise ValueError("Config must be an object with only rules and exclude")
    rules = config.get("rules", {})
    if not isinstance(rules, dict) or set(rules) - {"4D-M001"}:
        raise ValueError("Only custom rule 4D-M001 is configurable")
    options = rules.get("4D-M001", {})
    if not isinstance(options, dict) or set(options) - {"enabled", "maxLines"}:
        raise ValueError("4D-M001 supports only enabled and maxLines")
    enabled = options.get("enabled", True)
    maximum = options.get("maxLines", DEFAULT_MAX_LINES)
    if type(enabled) is not bool or type(maximum) is not int or not 1 <= maximum <= 100000:
        raise ValueError("enabled must be boolean; maxLines must be an integer in 1..100000")
    exclude = config.get("exclude", [])
    if not isinstance(exclude, list) or not all(isinstance(p, str) and p for p in exclude):
        raise ValueError("exclude must be an array of nonempty fnmatch patterns")
    return {"enabled": enabled, "maxLines": maximum, "exclude": exclude}


def sources(root: Path, config: dict) -> list[Path]:
    paths = []
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        relative = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatchcase(relative, pattern) for pattern in config["exclude"]):
            continue
        if path.is_symlink():
            raise ValueError(f"Source scope refuses symlinks: {relative}")
        if path.suffix.lower() != ".4dm":
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Source is not a regular non-symlink file: {relative}")
        path.resolve().relative_to(root)
        paths.append(path)
    if not paths:
        raise ValueError("No .4dm source files in the selected scope")
    return paths


def analyze_source(relative: str, text: str, config: dict) -> list[Finding]:
    # A final newline terminates the last physical line, not an extra empty line.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    count = normalized.count("\n") + int(bool(normalized) and not normalized.endswith("\n"))
    if not config["enabled"] or count <= config["maxLines"]:
        return []
    return [Finding(
        relative, 1, 0, 1, 0, "4D-M001",
        f"Source file has {count} physical lines; configured maximum is {config['maxLines']}.",
    )]
