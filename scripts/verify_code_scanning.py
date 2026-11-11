"""Verify an actual fixture upload and persist GitHub's analysis/alert evidence."""

import argparse
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlencode


EXPECTED_PATHS = {
    "tests/fixtures/rules/positive/Long.4dm",
    "tests/fixtures/github/space #é.4dm",
}


def api(path: str):
    result = subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def verify(processing: dict, analyses: list, alerts: list, sarif_id: str, ref: str, sha: str) -> dict:
    if processing.get("processing_status") != "complete":
        raise ValueError(f"SARIF processing is not complete: {processing}")
    matching = [analysis for analysis in analyses if analysis.get("sarif_id") == sarif_id]
    if len(matching) != 1:
        raise ValueError("Expected exactly one analysis for the uploaded SARIF identifier")
    analysis = matching[0]
    if analysis.get("ref") != ref or analysis.get("commit_sha") != sha:
        raise ValueError("The processed analysis does not match the workflow's ref and commit")
    if analysis.get("error") or analysis.get("results_count") != len(EXPECTED_PATHS):
        raise ValueError(f"Unexpected fixture analysis result: {analysis}")
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sarif-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    ref = os.environ["GITHUB_REF"]
    sha = os.environ["GITHUB_SHA"]
    base = f"repos/{repository}/code-scanning"
    processing = api(f"{base}/sarifs/{args.sarif_id}")
    query = urlencode({"ref": ref, "tool_name": "sonar-4d", "per_page": 100})
    analyses = api(f"{base}/analyses?{query}")
    alerts = api(f"{base}/alerts?{query}")
    evidence = verify(processing, analyses, alerts, args.sarif_id, ref, sha)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = Path(os.environ["GITHUB_STEP_SUMMARY"])
    with summary.open("a", encoding="utf-8") as file:
        file.write("## Verified 4D code-scanning fixture\n\n")
        file.write(f"GitHub processed SARIF `{args.sarif_id}` for `{sha}`.\n\n")
        for path, alert in sorted(evidence["alerts"].items()):
            file.write(f"- [{path}:1]({alert['url']}) — `4D-M001` maintainability fixture\n")


if __name__ == "__main__":
    main()
