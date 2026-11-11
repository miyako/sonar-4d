from urllib.parse import quote

from . import __version__
from .model import Finding
from .rules import RULES


def sarif(findings: list[Finding], complete: bool, errors: list[str]) -> dict:
    identifiers = sorted({finding.rule_id for finding in findings})
    indices = {rule: index for index, rule in enumerate(identifiers)}
    results = []
    for finding in sorted(set(findings)):
        region = {"startLine": finding.start_line}
        if finding.end_line != finding.start_line:
            region["endLine"] = finding.end_line
        if finding.location_precision == "range" and (
            finding.start_line, finding.start_column
        ) != (finding.end_line, finding.end_column):
            region.update({
                "startColumn": finding.start_column + 1,
                "endLine": finding.end_line,
                "endColumn": finding.end_column + 1,
            })
        results.append({
            "ruleId": finding.rule_id, "ruleIndex": indices[finding.rule_id],
            "level": finding.level, "message": {"text": finding.message},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": quote(finding.path, safe="/"), "uriBaseId": "%SRCROOT%"},
                "region": region,
            }}],
        })
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "sonar-4d", "version": __version__,
                "informationUri": "https://github.com/miyako/sonar-4d",
                "rules": [{
                    "id": rule, "name": rule.replace("-", "_"),
                    "shortDescription": {"text": RULES[rule]["name"]},
                    "fullDescription": {"text": RULES[rule]["description"]},
                    "properties": {"tags": [RULES[rule]["category"]]},
                } for rule in identifiers],
            }},
            "columnKind": "utf16CodeUnits",
            "invocations": [{
                "executionSuccessful": complete,
                "toolExecutionNotifications": [
                    {"level": "error", "message": {"text": error}} for error in errors
                ],
            }],
            "results": results,
        }],
    }


def sonar_generic(findings: list[Finding], complete: bool) -> dict:
    if not complete:
        raise ValueError("Refusing to export incomplete analysis as a Sonar generic report")
    rules = []
    for identifier in sorted({finding.rule_id for finding in findings}):
        categories = {f.category for f in findings if f.rule_id == identifier}
        if len(categories) != 1:
            raise ValueError("A Sonar rule must have a consistent category")
        category = categories.pop()
        rules.append({
            "id": identifier, "name": RULES[identifier]["name"],
            "description": RULES[identifier]["description"], "engineId": "sonar-4d",
            "cleanCodeAttribute": "FOCUSED" if category == "maintainability" else "LOGICAL",
            "type": {"maintainability": "CODE_SMELL", "reliability": "BUG",
                     "security": "VULNERABILITY"}[category],
            "severity": "MAJOR",
            "impacts": [{"softwareQuality": category.upper(), "severity": "MEDIUM"}],
        })
    issues = []
    for finding in sorted(set(findings)):
        text_range = {"startLine": finding.start_line}
        if finding.end_line != finding.start_line:
            text_range["endLine"] = finding.end_line
        if finding.location_precision == "range" and (
            finding.start_line, finding.start_column
        ) != (finding.end_line, finding.end_column):
            text_range.update({
                "startColumn": finding.start_column, "endLine": finding.end_line,
                "endColumn": finding.end_column,
            })
        issues.append({
            "ruleId": finding.rule_id,
            "primaryLocation": {"message": finding.message, "filePath": finding.path,
                                "textRange": text_range},
        })
    return {"rules": rules, "issues": issues}
