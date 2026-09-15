"""Exit-code tests for the two Terraform audit scripts in runtime/audit/.

Both shipped with no tests at all, which section 14.1 does not allow for a
script carrying a required gate ("Runtime unit tests: exit-code handling,
argument safety ..."). They are the gate: security/terraform-audit.yml runs them
with allow_failure: false, so their exit code is the audit verdict.

The cases below pin the verdict, not the wording of any individual check.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_SECRETS = REPO_ROOT / "runtime" / "audit" / "audit-secrets.sh"
SCAN_STATE = REPO_ROOT / "runtime" / "audit" / "scan-state.sh"

CLEAN, FINDING = 0, 1


def run(script: Path, base: Path, *repos: str):
    return subprocess.run(
        ["bash", str(script), *repos],
        capture_output=True,
        text=True,
        env={**os.environ, "TERRAFORM_BASE_PATH": str(base)},
    )


@pytest.fixture
def base(tmp_path: Path) -> Path:
    root = tmp_path / "repos"
    (root / "clean").mkdir(parents=True)
    (root / "clean" / "main.tf").write_text(
        'resource "null_resource" "example" {\n  triggers = { always = timestamp() }\n}\n'
    )
    return root


def test_audit_secrets_passes_a_clean_repository(base):
    result = run(AUDIT_SECRETS, base, "clean")
    assert result.returncode == CLEAN, result.stdout + result.stderr
    assert "AUDIT PASSED" in result.stdout


def test_audit_secrets_fails_on_a_state_persisting_vault_read(base):
    (base / "clean" / "vault.tf").write_text(
        'data "vault_kv_secret_v2" "creds" {\n  mount = "kv"\n  name  = "app"\n}\n'
    )
    result = run(AUDIT_SECRETS, base, "clean")
    assert result.returncode == FINDING
    assert "AUDIT FAILED" in result.stdout


def test_audit_secrets_treats_no_terraform_files_as_an_error(base):
    """A base path holding nothing must not report a passed audit."""
    (base / "empty").mkdir()
    result = run(AUDIT_SECRETS, base, "empty")
    assert result.returncode == FINDING
    assert "No .tf files found" in result.stdout + result.stderr


def test_audit_secrets_treats_a_missing_repository_as_an_error(base):
    """The repository set is the audit's scope; a member that is not there is
    incomplete coverage, which the caller must not read as a pass."""
    result = run(AUDIT_SECRETS, base, "not-cloned")
    assert result.returncode == FINDING


def test_scan_state_passes_when_there_is_no_state(base):
    result = run(SCAN_STATE, base, "clean")
    assert result.returncode == CLEAN
    assert "SCAN PASSED" in result.stdout


def test_scan_state_fails_on_a_plaintext_secret_attribute(base):
    (base / "clean" / "terraform.tfstate").write_text(
        '{"version": 4, "resources": [{"instances": [{"attributes": '
        '{"password": "hunter2-not-a-real-secret"}}]}]}\n'
    )
    result = run(SCAN_STATE, base, "clean")
    assert result.returncode == FINDING
    assert "STATE SCAN FAILED" in result.stdout


def test_scan_state_fails_on_an_output_that_is_not_marked_sensitive(base):
    (base / "clean" / "out.tfstate").write_text(
        '{"version": 4, "outputs": {"api_token": {"value": "x", "sensitive": false}}, '
        '"resources": []}\n'
    )
    result = run(SCAN_STATE, base, "clean")
    assert result.returncode == FINDING
