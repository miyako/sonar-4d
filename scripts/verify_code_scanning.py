"""Verify an actual fixture upload and persist GitHub's analysis/alert evidence."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlencode

from sonar4d.provision import RUNTIME_VERSION

EXPECTED_PATHS = {
    "tests/fixtures/rules/positive/Long.4dm",
    "tests/fixtures/github/space #é.4dm",
}


def api(path: str):
    result = subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def processed_analysis(processing: dict, analyses: list, sarif_id: str,
                       ref: str, sha: str, count: int) -> dict:
    if processing.get("processing_status") != "complete":
        raise ValueError(f"SARIF processing is not complete: {processing}")
    matching = [analysis for analysis in analyses if analysis.get("sarif_id") == sarif_id]
    if len(matching) != 1:
        raise ValueError("Expected exactly one analysis for the uploaded SARIF identifier")
    analysis = matching[0]
    if analysis.get("ref") != ref or analysis.get("commit_sha") != sha:
        raise ValueError("The processed analysis does not match the workflow's ref and commit")
    if analysis.get("error") or analysis.get("results_count") != count:
        raise ValueError(f"Unexpected fixture analysis result: {analysis}")
    return analysis


def verify(processing: dict, analyses: list, alerts: list, sarif_id: str, ref: str, sha: str) -> dict:
    analysis = processed_analysis(processing, analyses, sarif_id, ref, sha, len(EXPECTED_PATHS))
    found = {}
    for alert in alerts:
        instance = alert.get("most_recent_instance", {})
        if instance.get("analysis_key") != analysis.get("analysis_key"):
            continue
        if instance.get("category") != analysis.get("category"):
            continue
        if instance.get("ref") != ref or instance.get("commit_sha") != sha:
            continue
        location = instance.get("location", {})
        path = location.get("path")
        if path not in EXPECTED_PATHS:
            continue
        if alert.get("state") != "open" or alert.get("rule", {}).get("id") != "4D-M001":
            raise ValueError(f"Unexpected alert state or rule: {alert}")
        if location.get("start_line") != 1:
            raise ValueError(f"Fixture alert is not mapped to source line 1: {alert}")
        if path in found:
            raise ValueError(f"Duplicate fixture alert: {path}")
        found[path] = {"number": alert["number"], "url": alert["html_url"], "location": location}
    if set(found) != EXPECTED_PATHS:
        raise ValueError(f"Missing fixture alerts: {sorted(EXPECTED_PATHS - set(found))}")
    return {"sarifId": sarif_id, "analysisId": analysis["id"], "ref": ref, "commit": sha,
            "category": analysis["category"], "alerts": found}


def verify_report(processing: dict, analyses: list, alerts: list, sarif_id: str,
                  ref: str, sha: str, report: dict) -> dict:
    if report.get("complete") is not True or report.get("errors") != []:
        raise ValueError("Only complete reports can verify a successful upload")
    findings = report["findings"]
    analysis = processed_analysis(processing, analyses, sarif_id, ref, sha, len(findings))
    expected = Counter((f["path"], f["start_line"], f["rule_id"], f["message"]) for f in findings)
    observed = Counter()
    found = []
    for alert in alerts:
        instance = alert.get("most_recent_instance", {})
        if any(instance.get(key) != value for key, value in {
            "analysis_key": analysis.get("analysis_key"), "category": analysis.get("category"),
            "ref": ref, "commit_sha": sha,
        }.items()):
            continue
        location = instance.get("location", {})
        message = instance.get("message", {}).get("text")
        key = (location.get("path"), location.get("start_line"), alert.get("rule", {}).get("id"), message)
        if alert.get("state") != "open" or key not in expected:
            raise ValueError("An uploaded alert has an unexpected rule, path, line, message or state")
        observed[key] += 1
        found.append({"number": alert["number"], "url": alert["html_url"],
                      "location": location, "message": message})
    if observed != expected:
        raise ValueError("GitHub alerts do not exactly match the complete analysis findings")
    return {"sarifId": sarif_id, "analysisId": analysis["id"], "ref": ref, "commit": sha,
            "category": analysis["category"], "alerts": found}


def verify_native_fixture(report: dict, observation: dict, fixture: str) -> None:
    if "tool4d-syntax" not in report.get("backends", []) or observation.get("complete") is not True:
        raise ValueError("Native fixture did not complete native syntax checking")
    if observation.get("version") != RUNTIME_VERSION:
        raise ValueError("Native fixture did not use the pinned public runtime")
    if fixture == "clean":
        if report["findings"] or report["scope"] != ["tests/fixtures/native-clean/Project/Sources/Methods/Clean.4dm"]:
            raise ValueError("Expected the complete clean native fixture")
        return
    prefix = "tests/fixtures/native/Project/Sources/Methods/"
    expected = Counter([
        ("Caller.4dm", 2, "Impossible to cast Text to Integer."),
        ("Opened.4dm", 3, "Missing expression"), ("Opened.4dm", 3, "if without end if"),
        ("Unopened.4dm", 2, "Missing expression"), ("Unopened.4dm", 2, "if without end if"),
    ])
    actual = Counter()
    for finding in report["findings"]:
        if finding["rule_id"] != "4D-N001" or not finding["path"].startswith(prefix):
            raise ValueError("Unexpected native fixture finding")
        actual[(finding["path"].removeprefix(prefix), finding["start_line"], finding["message"])] += 1
    if actual != expected:
        raise ValueError("Native fixture did not detect the expected cross-file/unopened errors and locations")
    if set(observation["opened"]) != {prefix + "Opened.4dm", prefix + "Caller.4dm"}:
        raise ValueError("Native coverage fixture did not leave the intended files unopened")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sarif-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--analysis-file", type=Path)
    parser.add_argument("--native-fixture", choices=["errors", "clean"])
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    ref = os.environ["GITHUB_REF"]
    sha = os.environ["GITHUB_SHA"]
    base = f"repos/{repository}/code-scanning"
    processing = api(f"{base}/sarifs/{args.sarif_id}")
    query = urlencode({"ref": ref, "tool_name": "sonar-4d", "per_page": 100})
    analyses = api(f"{base}/analyses?{query}")
    alerts = api(f"{base}/alerts?{query}")
    if args.analysis_file:
        report = json.loads(args.analysis_file.read_text(encoding="utf-8"))
        if args.native_fixture:
            observation = json.loads(
                (args.analysis_file.parent / "native-observation.json").read_text(encoding="utf-8")
            )
            verify_native_fixture(report, observation, args.native_fixture)
        evidence = verify_report(processing, analyses, alerts, args.sarif_id, ref, sha, report)
        links = [(alert["location"]["path"], alert["location"]["start_line"], alert["url"])
                 for alert in evidence["alerts"]]
    else:
        if args.native_fixture:
            raise ValueError("--native-fixture requires --analysis-file")
        evidence = verify(processing, analyses, alerts, args.sarif_id, ref, sha)
        links = [(path, 1, alert["url"]) for path, alert in evidence["alerts"].items()]
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = Path(os.environ["GITHUB_STEP_SUMMARY"])
    with summary.open("a", encoding="utf-8") as file:
        file.write("## Verified 4D code-scanning fixture\n\n")
        file.write(f"GitHub processed SARIF `{args.sarif_id}` for `{sha}`.\n\n")
        for path, line, url in sorted(links):
            file.write(f"- [{path}:{line}]({url})\n")


if __name__ == "__main__":
    main()
