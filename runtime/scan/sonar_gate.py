#!/usr/bin/env python3
"""SARIF collection and quality-gate evaluation for quality-sonarqube.

Two things the template this replaces could not do.

1. It imported whatever `*.sarif` it happened to find. A consumer that
   overrode `needs:` lost the scanner artifacts and the job still went green
   with a log line, because an absent report and a clean report look the same.
   Here the component declares the reports it must import and a missing one is
   a failure.

2. It treated a successful upload as a result. Section 10.3: "SonarQube
   analysis, external-report import and the SonarQube quality gate are distinct
   outcomes. A successful upload does not establish a passed quality gate."
   Here the scanner waits on the gate and this module reads the decided result
   back from the server, writing gate-result.json as the section 9.2 evidence.
   It also disambiguates sonar-scanner's exit code 1, which means an analysis
   error AND a failed gate: a red gate is a finding, anything else is an
   execution failure that fails in advisory mode too.

Delivery: embedded verbatim in the component job script, because an `include:`
imports YAML and never checks out this repository.
tests/runtime/scan/test_embedded_runtime.py fails when the copy drifts.

Usage:
    sonar_gate.py sarif-list [--required PATH]... [--search-root DIR]
    sonar_gate.py gate --report-task PATH --out FILE --status N
                       --policy-mode blocking|advisory [--report PATH]...

Exit codes: 0 pass, 1 red quality gate under a blocking policy, 2 execution
error (in both policy modes).
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 60


class ExecutionError(RuntimeError):
    """The analysis or the gate lookup failed, which is not a finding."""


def collect_sarif(required: list[str], search_root: str) -> list[str]:
    """Return the SARIF reports to import, failing when a declared one is absent.

    With no declared paths the reports are discovered, which is the behaviour a
    standalone include needs. With declared paths the set is exact: that is what
    makes the import survive a consumer overriding `needs:`.
    """
    if required:
        missing = [path for path in required if not os.path.isfile(path)]
        if missing:
            raise ExecutionError(
                "declared SARIF report(s) not present: "
                + ", ".join(missing)
                + ". The producing job did not run or its artifacts were not "
                "fetched; a consumer `needs:` override REPLACES the component's "
                "list, it does not merge with it."
            )
        files = list(required)
    else:
        patterns = (
            os.path.join(search_root, "*.sarif"),
            os.path.join(search_root, "*", "*.sarif"),
            os.path.join(search_root, ".ci-artifacts", "*", "*", "*.sarif"),
        )
        files = sorted(
            {
                os.path.normpath(path)
                for pattern in patterns
                for path in glob.glob(pattern)
            }
        )
        files = [path for path in files if ".sonar/" not in path]

    keep: list[str] = []
    dropped = 0
    for path in files:
        try:
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as error:
            raise ExecutionError(f"{path} is not readable SARIF: {error}") from error
        total = bad = 0
        for run in document.get("runs", []):
            for result in run.get("results", []):
                for location in result.get("locations", []):
                    uri = (
                        location.get("physicalLocation", {})
                        .get("artifactLocation", {})
                        .get("uri", "")
                    )
                    total += 1
                    # SonarQube's SARIF importer cannot resolve a path
                    # containing a space: raw spaces are dropped outright and
                    # %20 lands the issue on the project root with no file or
                    # line. Verified on Community Build 26.4, 2026/09/09.
                    if " " in uri or "%20" in uri:
                        bad += 1
                        if bad <= 5:
                            print(f"WARNING:   unimportable path: {uri}", file=sys.stderr)
                    break
        keep.append(path)
        dropped += bad
        note = f" ({bad} will be dropped: space in path)" if bad else ""
        print(f"SARIF {path}: {total} finding(s){note}", file=sys.stderr)
    if dropped:
        print(
            f"WARNING: {dropped} finding(s) will NOT reach SonarQube. "
            "Rename the offending directories to remove spaces.",
            file=sys.stderr,
        )
    return keep


def read_report_task(path: str) -> dict:
    """Parse the key=value report-task.txt the scanner writes."""
    if not os.path.isfile(path):
        raise ExecutionError(
            f"{path} was not written; the analysis did not reach the server, so "
            "there is no gate result to read."
        )
    values = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            key, separator, value = line.strip().partition("=")
            if separator:
                values[key] = value
    for key in ("ceTaskUrl", "serverUrl", "projectKey"):
        if not values.get(key):
            raise ExecutionError(f"{path} has no {key}")
    return values


def api_get(url: str, token: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")[:300]
        raise ExecutionError(f"{url} returned HTTP {error.code}: {body}") from error
    except urllib.error.URLError as error:
        raise ExecutionError(f"{url} unreachable: {error.reason}") from error


def evaluate_gate(task: dict, token: str) -> tuple[dict, dict]:
    """Return (compute-engine task, quality gate status) from the server."""
    ce_task = api_get(task["ceTaskUrl"], token).get("task", {})
    status = ce_task.get("status")
    if status != "SUCCESS":
        raise ExecutionError(
            f"the compute-engine task finished with status {status!r}; no quality "
            "gate was decided."
        )
    analysis_id = ce_task.get("analysisId")
    if not analysis_id:
        raise ExecutionError("the compute-engine task reported no analysisId")
    url = (
        task["serverUrl"].rstrip("/")
        + "/api/qualitygates/project_status?"
        + urllib.parse.urlencode({"analysisId": analysis_id})
    )
    gate = api_get(url, token).get("projectStatus", {})
    if not gate.get("status"):
        raise ExecutionError(f"{url} returned no projectStatus.status")
    return ce_task, gate


def write_evidence(path: str, payload: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")
    print(f"sonar-gate: evidence written to {path}")


def command_sarif_list(args: argparse.Namespace) -> int:
    files = collect_sarif(args.required, args.search_root)
    if files:
        print(",".join(files), end="")
    return 0


def command_gate(args: argparse.Namespace) -> int:
    token = os.environ.get("SONAR_TOKEN")
    if not token:
        raise ExecutionError("SONAR_TOKEN is not set; the gate result cannot be read")

    task = read_report_task(args.report_task)
    ce_task, gate = evaluate_gate(task, token)
    gate_status = gate["status"]

    evidence = {
        "schema_version": 1,
        "subject": {
            "project_key": task["projectKey"],
            "project": os.environ.get("CI_PROJECT_PATH", "unresolved"),
            "commit": os.environ.get("CI_COMMIT_SHA", "unresolved"),
        },
        "analysis": {
            "server_url": task["serverUrl"],
            "task_id": ce_task.get("id", "unresolved"),
            "analysis_id": ce_task.get("analysisId", "unresolved"),
            "dashboard_url": task.get("dashboardUrl", "unresolved"),
            "scanner_exit_status": args.status,
        },
        "gate": {
            "status": gate_status,
            "conditions": gate.get("conditions", []),
        },
        "reports_imported": args.report,
        "policy": {"mode": args.policy_mode},
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "pipeline_id": os.environ.get("CI_PIPELINE_ID", "unresolved"),
        "job_id": os.environ.get("CI_JOB_ID", "unresolved"),
    }
    write_evidence(args.out, evidence)

    print(f"sonar-gate: quality gate {gate_status} (scanner exit {args.status})")
    for condition in gate.get("conditions", []):
        if condition.get("status") == "ERROR":
            print(
                "sonar-gate:   failed condition: "
                f"{condition.get('metricKey')} {condition.get('comparator')} "
                f"{condition.get('errorThreshold')} (actual {condition.get('actualValue')})"
            )

    if gate_status == "OK":
        # A green gate cannot coexist with a non-zero scanner: that is an
        # execution failure wearing a passing gate.
        if args.status != 0:
            raise ExecutionError(
                f"the quality gate is OK but sonar-scanner exited {args.status}; "
                "the analysis did not complete correctly."
            )
        return 0

    if args.policy_mode == "advisory":
        print(
            f"sonar-gate: advisory mode: quality gate {gate_status} recorded, "
            "analysis completed correctly."
        )
        return 0
    print(f"sonar-gate: FAIL: quality gate {gate_status}.", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    sarif = subparsers.add_parser("sarif-list")
    sarif.add_argument("--required", action="append", default=[])
    sarif.add_argument("--search-root", default=".")
    sarif.set_defaults(handler=command_sarif_list)

    gate = subparsers.add_parser("gate")
    gate.add_argument("--report-task", required=True)
    gate.add_argument("--out", required=True)
    gate.add_argument("--status", type=int, required=True)
    gate.add_argument("--policy-mode", choices=("blocking", "advisory"), required=True)
    gate.add_argument("--report", action="append", default=[])
    gate.set_defaults(handler=command_gate)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except ExecutionError as error:
        print(f"sonar-gate: ERROR: {error}", file=sys.stderr)
        print(
            "sonar-gate: advisory mode does not cover execution failures.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
