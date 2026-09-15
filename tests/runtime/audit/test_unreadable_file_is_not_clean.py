"""A repository the audit could not read is not a repository with no findings.

TE's rc.2 review left this open alongside the scan.sh counts. Every search step
in audit-secrets.sh ended `2>/dev/null || true`, so three different answers
arrived as one: grep matched nothing (exit 1), grep could not look (exit 2 or
more), awk could not open the file. The script printed PASS for all three and
exited 0, which is an audit reporting a clean verdict on files it never opened.

An error is fatal here rather than a counted FAIL. A FAIL says the Terraform is
wrong; this says the audit did not run, and the caller has to be able to tell
those apart. Exit 2 is neither CLEAN (0) nor FINDING (1).

Both routes are covered for the reason the scan.sh suite gives: a mode does
nothing to root, and the audit job runs as root.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_SECRETS = REPO_ROOT / "runtime" / "audit" / "audit-secrets.sh"

CLEAN, FINDING, DID_NOT_RUN = 0, 1, 2

STUB = """#!/bin/sh
exit {code}
"""

skip_as_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="a file mode does not stop root from reading it"
)


@pytest.fixture
def base(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    (root / "clean").mkdir(parents=True)
    (root / "clean" / "main.tf").write_text(
        'resource "null_resource" "example" {\n  triggers = { always = timestamp() }\n}\n'
    )
    return root


def run(base: Path, *repos: str, stub: tuple[str, int] | None = None):
    env = {**os.environ, "TERRAFORM_BASE_PATH": str(base)}
    if stub is not None:
        name, code = stub
        bin_dir = base.parent / "stub-bin"
        bin_dir.mkdir(exist_ok=True)
        tool = bin_dir / name
        tool.write_text(STUB.format(code=code))
        tool.chmod(0o755)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(AUDIT_SECRETS), *repos], capture_output=True, text=True, env=env
    )


def test_the_fixture_audits_clean_before_anything_is_broken(base):
    """The positive control: without it, every assertion below could pass for
    the wrong reason."""
    result = run(base, "clean")
    assert result.returncode == CLEAN, result.stdout + result.stderr
    assert "AUDIT PASSED" in result.stdout


def test_a_grep_that_cannot_look_stops_the_audit(base):
    result = run(base, "clean", stub=("grep", 2))
    assert result.returncode == DID_NOT_RUN, result.stdout + result.stderr
    assert "grep exited 2" in result.stderr
    assert "AUDIT PASSED" not in result.stdout


def test_an_awk_that_fails_stops_the_audit(base):
    """awk has no "did not match" exit code, so any non-zero status is an error."""
    result = run(base, "clean", stub=("awk", 1))
    assert result.returncode == DID_NOT_RUN, result.stdout + result.stderr
    assert "awk exited 1" in result.stderr
    assert "AUDIT PASSED" not in result.stdout


@skip_as_root
def test_an_unreadable_terraform_file_stops_the_audit(base):
    (base / "clean" / "main.tf").chmod(0o000)
    result = run(base, "clean")
    assert result.returncode == DID_NOT_RUN, result.stdout + result.stderr
    assert "did not complete" in result.stderr
    assert "AUDIT PASSED" not in result.stdout


@skip_as_root
def test_an_unwalkable_repository_stops_the_audit(base):
    """The file list is part of the verdict: a tree that cannot be walked
    contributes no files, and no files means no findings."""
    (base / "clean" / "modules").mkdir()
    (base / "clean" / "modules" / "inner.tf").write_text('provider "vault" {}\n')
    (base / "clean" / "modules").chmod(0o000)
    try:
        result = run(base, "clean")
        assert result.returncode == DID_NOT_RUN, result.stdout + result.stderr
        assert "incomplete file list is not a clean audit" in result.stderr
    finally:
        (base / "clean" / "modules").chmod(0o755)
