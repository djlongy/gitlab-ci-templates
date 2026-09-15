"""Exit-code tests for runtime/audit/gitleaks-scan.sh.

Section 10.1: "Scanner crash, timeout, malformed output or missing required
database — Job fails in both blocking and advisory modes", and that outcome must
be distinguishable from a finding. security/group-scan.yml could not make the
distinction: it branched on `if ! gitleaks detect ...`, so a gitleaks that
failed to start was indistinguishable from a repository with leaked secrets, and
neither was distinguishable from a report it never managed to write.

gitleaks is stubbed here. These tests pin the wrapper's reading of the pinned
tool's documented exit codes (0 clean, 1 findings), not gitleaks itself.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "runtime" / "audit" / "gitleaks-scan.sh"

CLEAN, FINDINGS, SCAN_ERROR, USAGE = 0, 1, 2, 3


def stub_gitleaks(tmp_path: Path, body: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "gitleaks"
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(0o755)
    return bin_dir


def write_report_stub(findings: int, exit_code: int) -> str:
    """A gitleaks that writes `findings` results and exits `exit_code`."""
    report = json.dumps([{"RuleID": "generic-api-key"}] * findings)
    return (
        'report=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in --report-path) report="$2"; shift 2 ;; *) shift ;; esac\n'
        "done\n"
        f"printf '%s' '{report}' > \"$report\"\n"
        f"exit {exit_code}\n"
    )


def run(tmp_path: Path, stub_body: str, *, repositories: int = 1):
    repos = tmp_path / "repos"
    for index in range(repositories):
        (repos / f"platform_repo{index}").mkdir(parents=True)
    repos.mkdir(exist_ok=True)
    reports = tmp_path / "reports"
    return (
        subprocess.run(
            ["bash", str(SCRIPT), str(repos), str(reports)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{stub_gitleaks(tmp_path, stub_body)}:{os.environ['PATH']}",
            },
        ),
        reports,
    )


def test_a_clean_repository_passes(tmp_path):
    result, reports = run(tmp_path, write_report_stub(0, 0))
    assert result.returncode == CLEAN, result.stderr
    assert json.loads((reports / "platform_repo0.json").read_text()) == []


def test_findings_fail_the_scan(tmp_path):
    result, _ = run(tmp_path, write_report_stub(3, 1))
    assert result.returncode == FINDINGS
    assert "3 finding(s)" in result.stdout


def test_a_crashed_scanner_is_not_reported_as_findings(tmp_path):
    """The distinction section 10.1 requires and the old job could not make."""
    result, _ = run(tmp_path, "echo 'boom' >&2\nexit 2\n")
    assert result.returncode == SCAN_ERROR
    assert "no parsable report" in result.stderr


def test_an_unparsable_report_fails_even_on_a_clean_exit(tmp_path):
    result, _ = run(
        tmp_path,
        'while [ $# -gt 0 ]; do case "$1" in --report-path) report="$2"; shift 2 ;; '
        '*) shift ;; esac; done\nprintf "not json" > "$report"\nexit 0\n',
    )
    assert result.returncode == SCAN_ERROR


def test_findings_reported_with_a_clean_exit_are_treated_as_an_error(tmp_path):
    """Exit 0 and a non-empty report contradict each other; do not guess."""
    result, _ = run(tmp_path, write_report_stub(2, 0))
    assert result.returncode == SCAN_ERROR
    assert "with exit 0" in result.stderr


def test_one_failed_repository_fails_the_whole_scan(tmp_path):
    """Every in-scope repository counts; a partial scan is not a pass."""
    body = (
        'source=""; report=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in --source) source="$2"; shift 2 ;;'
        ' --report-path) report="$2"; shift 2 ;; *) shift ;; esac\n'
        "done\n"
        'case "$source" in *repo1) exit 3 ;; esac\n'
        "printf '[]' > \"$report\"\nexit 0\n"
    )
    result, _ = run(tmp_path, body, repositories=2)
    assert result.returncode == SCAN_ERROR


def test_scanning_no_repositories_is_not_a_pass(tmp_path):
    result, _ = run(tmp_path, write_report_stub(0, 0), repositories=0)
    assert result.returncode == USAGE
    assert "nothing was scanned" in result.stderr


def test_a_missing_scanner_fails(tmp_path):
    """No gitleaks on PATH is an execution failure, not a clean estate."""
    (tmp_path / "repos" / "platform_repo0").mkdir(parents=True)
    bin_dir = tmp_path / "bin-without-gitleaks"
    bin_dir.mkdir()
    for tool in ("jq", "basename", "mkdir"):
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT), str(tmp_path / "repos"), str(tmp_path / "reports")],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": str(bin_dir)},
    )
    assert result.returncode == SCAN_ERROR
    assert "gitleaks is not on PATH" in result.stderr
