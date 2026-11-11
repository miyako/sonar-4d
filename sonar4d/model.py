from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    rule_id: str
    message: str
    category: str = "maintainability"
    level: str = "warning"
    location_precision: str = "range"

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in (
            self.start_line, self.start_column, self.end_line, self.end_column,
        )):
            raise ValueError("Finding coordinates must be integers")
        if self.category not in {"reliability", "maintainability", "security"}:
            raise ValueError(f"Unknown category: {self.category}")
        if self.level not in {"error", "warning", "note"}:
            raise ValueError(f"Unknown level: {self.level}")
        if self.location_precision not in {"range", "line"}:
            raise ValueError("Location precision must be range or line")
        if Path(self.path).is_absolute() or ".." in Path(self.path).parts or "\\" in self.path:
            raise ValueError("Finding paths must be repository-relative POSIX paths")
        if not self.path or self.start_line < 1 or self.start_column < 0 or self.end_column < 0:
            raise ValueError("Invalid finding start")
        if (self.end_line, self.end_column) < (self.start_line, self.start_column):
            raise ValueError("Reversed finding range")

    def to_dict(self) -> dict:
        return asdict(self)


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def validate_position(text: str, line: int, column: int) -> None:
    lines = text.split("\n")
    if not 0 <= line < len(lines) or column < 0:
        raise ValueError("LSP position is outside source")
    source = lines[line].removesuffix("\r")
    offset = 0
    for character in source:
        if offset == column:
            return
        offset += 2 if ord(character) > 0xFFFF else 1
        if offset > column:
            break
    if offset != column:
        raise ValueError("LSP position is outside source or splits a UTF-16 surrogate pair")


def native_findings(root: Path, diagnostics: dict) -> list[Finding]:
    findings = []
    for relative, items in sorted(diagnostics.items()):
        source = (root / relative).resolve()
        source.relative_to(root)
        if not isinstance(items, list):
            raise ValueError("Native diagnostics must be an array")
        text = source.read_text(encoding="utf-8-sig")
        for item in items:
            start = item["range"]["start"]
            end = item["range"]["end"]
            for position in (start, end):
                if type(position["line"]) is not int or type(position["character"]) is not int:
                    raise ValueError("LSP coordinates must be integers")
            line_only = end["character"] == 2147483647 and start["character"] >= -1
            for position in (start, end):
                validate_position(text, position["line"], 0 if line_only else position["character"])
            if line_only and start["character"] >= 0:
                validate_position(text, start["line"], start["character"])
            severity = item.get("severity", 1)
            if type(severity) is not int or severity not in {1, 2, 3, 4}:
                raise ValueError("Unknown LSP diagnostic severity")
            message = item["message"]
            if not isinstance(message, str) or not message:
                raise ValueError("Native diagnostic message must be nonempty text")
            # Keep the native code as data in the message, not an unstable rule catalog.
            if "code" in item:
                message = f"[{item['code']}] {message}"
            findings.append(Finding(
                relative, start["line"] + 1, 0 if line_only else start["character"],
                end["line"] + 1, 0 if line_only else end["character"], "4D-N001", message,
                "reliability", {1: "error", 2: "warning", 3: "note", 4: "note"}[severity],
                "line" if line_only else "range",
            ))
    return findings
