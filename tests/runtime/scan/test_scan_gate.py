"""Behaviour tests for runtime/scan/scan-gate.sh.

Each test names the behaviour the previous templates got wrong, so a regression
fails here rather than in a pipeline that goes green while scanning nothing.

The exit-code tables the wrapper encodes were measured against the pinned images
on 2026/09/15 (see the tool comments in scan-gate.sh); these tests lock the
tables in place. They do not run the scanners.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCAN_GATE = REPO_ROOT / "runtime" / "scan" / "scan-gate.sh"

PASS, GATE_FAILURE, EXECUTION_ERROR = 0, 1, 2


def write_scan(tmp_path: Path, severities: list[str], report: str = '{"ok": true}'):
    (tmp_path / "severities.txt").write_text("".join(f"{s}\n" for s in severities))
    (tmp_path / "report.json").write_text(report)


def evaluate(tmp_path: Path, tool: str, status: int, **flags) -> subprocess.CompletedProcess:
    argv = [
        "sh",
        str(SCAN_GATE),
        "evaluate",
        "--tool",
        tool,
        "--status",
        str(status),
        "--severities",
        "severities.txt",
        "--report",
        "report.json",
        "--threshold",
        flags.pop("threshold", "high"),
        "--policy-mode",
        flags.pop("policy_mode", "blocking"),
        "--out",
        "evidence/scan-result.json",
    ]
    if tool in ("trivy", "grype") and "db_snapshot" not in flags:
        flags["db_snapshot"] = "2026-09-15T07:08:16Z"
    for key, value in flags.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    return subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True)


def evidence(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "evidence" / "scan-result.json").read_text())


def test_severity_threshold_expands_to_a_trivy_severity_list():
    result = subprocess.run(
        ["sh", str(SCAN_GATE), "severities", "high"], capture_output=True, text=True
    )
    assert result.returncode == PASS
    assert result.stdout.strip() == "HIGH,CRITICAL"


def test_an_unknown_threshold_is_an_error_not_a_default():
    result = subprocess.run(
        ["sh", str(SCAN_GATE), "severities", "severe"], capture_output=True, text=True
    )
    assert result.returncode == EXECUTION_ERROR


def test_blocking_findings_fail_the_job(tmp_path):
    write_scan(tmp_path, ["HIGH", "MEDIUM"])
    result = evaluate(tmp_path, "trivy", 13)
    assert result.returncode == GATE_FAILURE
    assert evidence(tmp_path)["outcome"] == "findings"


def test_advisory_findings_are_recorded_and_pass(tmp_path):
    write_scan(tmp_path, ["CRITICAL"])
    result = evaluate(tmp_path, "trivy", 13, policy_mode="advisory")
    assert result.returncode == PASS
    record = evidence(tmp_path)
    assert record["outcome"] == "findings"
    assert record["counts"]["at_or_above_threshold"] == 1
    assert record["policy"]["mode"] == "advisory"


def test_advisory_mode_does_not_cover_an_execution_failure(tmp_path):
    """The defect TRIVY_EXIT_CODE=0 and `|| true` both had.

    Advisory mode used to make a crash and a clean scan indistinguishable. A
    non-finding exit code must fail in advisory mode too.
    """
    write_scan(tmp_path, [])
    result = evaluate(tmp_path, "trivy", 1, policy_mode="advisory")
    assert result.returncode == EXECUTION_ERROR
    assert evidence(tmp_path)["completion"] == "failed"


def test_grype_findings_use_exit_code_two_not_one(tmp_path):
    """Measured on grype 0.118.0: 2 is findings, 1 is an execution error.

    Reading the documentation instead would have produced the opposite table and
    turned every crash into a passing 'no findings' result.
    """
    write_scan(tmp_path, ["High"])
    findings = evaluate(tmp_path, "grype", 2)
    assert findings.returncode == GATE_FAILURE
    assert evidence(tmp_path)["outcome"] == "findings"

    write_scan(tmp_path, [])
    crash = evaluate(tmp_path, "grype", 1, policy_mode="advisory")
    assert crash.returncode == EXECUTION_ERROR


def test_gitleaks_exit_one_without_a_finding_is_an_execution_error(tmp_path):
    """gitleaks 8.21.2 returns 1 for a leak and for a source it cannot read.

    Measured: `gitleaks detect --source /no/such/path` exits 1 with an empty
    report. Treating that as 'a secret was found' would be wrong in the other
    direction, so the code is resolved against the report.
    """
    write_scan(tmp_path, [])
    result = evaluate(tmp_path, "gitleaks", 1, threshold="critical")
    assert result.returncode == EXECUTION_ERROR
    assert "execution failure" in evidence(tmp_path)["detail"]


def test_gitleaks_exit_one_with_a_finding_fails_the_gate(tmp_path):
    write_scan(tmp_path, ["secret"])
    result = evaluate(tmp_path, "gitleaks", 1, threshold="critical")
    assert result.returncode == GATE_FAILURE
    assert evidence(tmp_path)["counts"]["critical"] == 1


def test_a_clean_exit_alongside_findings_is_an_execution_error(tmp_path):
    """Positive control against a gate that silently did not run."""
    write_scan(tmp_path, ["CRITICAL"])
    result = evaluate(tmp_path, "trivy", 0)
    assert result.returncode == EXECUTION_ERROR
    assert "the gate did not run" in evidence(tmp_path)["detail"]


def test_findings_below_the_threshold_pass_and_are_still_counted(tmp_path):
    """semgrep --error exits 1 on any finding, whatever its severity."""
    write_scan(tmp_path, ["INFO", "WARNING"])
    result = evaluate(tmp_path, "semgrep", 1)
    assert result.returncode == PASS
    record = evidence(tmp_path)
    assert record["outcome"] == "below-threshold"
    assert record["counts"]["total"] == 2
    assert record["counts"]["at_or_above_threshold"] == 0
    assert record["counts"]["medium"] == 1
    assert record["counts"]["low"] == 1


def test_semgrep_rule_config_error_fails(tmp_path):
    """Exit 7 is semgrep's unusable-rules code, measured on 1.79.0."""
    write_scan(tmp_path, [])
    assert evaluate(tmp_path, "semgrep", 7).returncode == EXECUTION_ERROR


@pytest.mark.parametrize("tool", ["trivy", "grype"])
def test_a_vulnerability_scan_without_a_database_snapshot_fails(tmp_path, tool):
    """A scanner with no database reports nothing and looks clean."""
    write_scan(tmp_path, [])
    result = evaluate(tmp_path, tool, 0, db_snapshot="")
    assert result.returncode == EXECUTION_ERROR
    assert "database" in result.stderr


def test_a_missing_report_fails(tmp_path):
    (tmp_path / "severities.txt").write_text("")
    result = evaluate(tmp_path, "trivy", 0)
    assert result.returncode == EXECUTION_ERROR
    assert "report not written" in result.stderr


def test_an_empty_report_fails(tmp_path):
    write_scan(tmp_path, [], report="")
    result = evaluate(tmp_path, "trivy", 0)
    assert result.returncode == EXECUTION_ERROR
    assert "report is empty" in result.stderr


def test_evidence_records_the_scan_that_ran(tmp_path):
    write_scan(tmp_path, ["HIGH", "LOW", "UNKNOWN"])
    evaluate(
        tmp_path,
        "trivy",
        13,
        scanner_version="0.69.3",
        scanner_image="harbor.example/trivy@sha256:" + "0" * 64,
        ignore_unfixed="true",
        working_directory="services/api",
    )
    record = evidence(tmp_path)
    assert record["schema_version"] == 1
    assert record["scanner"] == {
        "name": "trivy",
        "version": "0.69.3",
        "image": "harbor.example/trivy@sha256:" + "0" * 64,
        "exit_status": 13,
        "rules_ref": "",
    }
    assert record["database"]["snapshot"] == "2026-09-15T07:08:16Z"
    assert record["policy"]["threshold"] == "high"
    assert record["policy"]["ignore_unfixed"] is True
    assert record["policy"]["exceptions"] == []
    assert record["subject"]["working_directory"] == "services/api"
    assert record["counts"]["unknown"] == 1
    assert record["counts"]["at_or_above_threshold"] == 1
    assert len(record["reports"][0]["sha256"]) == 64


def test_an_unsupported_tool_is_refused(tmp_path):
    write_scan(tmp_path, [])
    assert evaluate(tmp_path, "checkov", 0).returncode == EXECUTION_ERROR


# --------------------------------------------------------------------------
# ansible-lint
#
# Measured in the pinned image (ansible-lint 26.4.0) on 2026/09/15: 0 with no
# violations, 2 with violations, 1 on an unusable configuration, 5 on
# "Linter finished without analyzing any file". The component ran the linter
# bare before this, so blocking mode has to keep failing on any violation.
# --------------------------------------------------------------------------

ANSIBLE_LINT_THRESHOLD = "low"


def lint_run(tmp_path: Path, status: int, levels: list[str], **flags):
    write_scan(tmp_path, levels, report='{"runs": [{"results": []}]}')
    flags.setdefault("threshold", ANSIBLE_LINT_THRESHOLD)
    return evaluate(tmp_path, "ansible-lint", status, **flags)


def test_ansible_lint_violations_fail_in_blocking_mode(tmp_path):
    result = lint_run(tmp_path, 2, ["error", "warning"])
    assert result.returncode == GATE_FAILURE, result.stdout + result.stderr
    record = evidence(tmp_path)
    assert record["outcome"] == "findings"
    assert record["counts"]["at_or_above_threshold"] == 2


def test_ansible_lint_advisory_records_the_violations_and_passes(tmp_path):
    result = lint_run(tmp_path, 2, ["error", "warning", "note"], policy_mode="advisory")
    assert result.returncode == PASS, result.stdout + result.stderr
    record = evidence(tmp_path)
    assert record["completion"] == "completed"
    assert record["outcome"] == "findings"
    assert record["policy"]["mode"] == "advisory"
    assert record["counts"] == {
        "total": 3, "at_or_above_threshold": 3,
        "critical": 0, "high": 1, "medium": 1, "low": 1, "negligible": 0, "unknown": 0,
    }


@pytest.mark.parametrize("mode", ["blocking", "advisory"])
@pytest.mark.parametrize(
    "status, reason",
    [(5, "nothing analysed"), (1, "unusable configuration"), (137, "killed")],
)
def test_an_ansible_lint_run_that_did_not_complete_fails_in_both_modes(
    tmp_path, mode, status, reason
):
    """Exit 5 is the one that would otherwise read as success.

    `ansible-lint` exits 5 when it analysed no file at all — a mistyped
    working-directory, a checkout with the Ansible tree somewhere else. Nothing
    was linted, so nothing is clean, and advisory mode does not cover it.
    """
    result = lint_run(tmp_path, status, [], policy_mode=mode)
    assert result.returncode == EXECUTION_ERROR, reason
    assert evidence(tmp_path)["completion"] == "failed"


def test_ansible_lint_exit_zero_with_violations_is_an_execution_error(tmp_path):
    """The gate did not run: a clean exit code cannot coexist with findings."""
    result = lint_run(tmp_path, 0, ["error"])
    assert result.returncode == EXECUTION_ERROR
    assert "gate did not run" in evidence(tmp_path)["detail"]


def test_ansible_lint_sarif_levels_map_onto_the_canonical_scale(tmp_path):
    lint_run(tmp_path, 2, ["error", "warning", "note"], policy_mode="advisory")
    counts = evidence(tmp_path)["counts"]
    assert (counts["high"], counts["medium"], counts["low"]) == (1, 1, 1)


def test_an_unrated_ansible_lint_level_gates_rather_than_disappearing(tmp_path):
    """A level this table does not know must not rank below every threshold.

    `unknown` never reaches a threshold, so a change in the tool's output would
    turn every violation into a pass. An unrecognised level ranks critical.
    """
    result = lint_run(tmp_path, 2, ["surprise"])
    assert result.returncode == GATE_FAILURE
    assert evidence(tmp_path)["counts"]["critical"] == 1
