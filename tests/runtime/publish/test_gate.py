"""Behaviour tests for runtime/publish/gate.sh.

`gate-jobs: []` is the one way a publishing component goes green with nothing
having checked the image: lint sees no missing producer, the stage order says
nothing, and the job publishes. The audit of the predecessor templates caught
the same class of defect as an exit 0 on missing evidence.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[3] / "runtime" / "publish" / "gate.sh"


def run(gates: str | None, allow: str | None = None, extra: str = "") -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    if gates is not None:
        env["CI_TPL_GATE_JOBS"] = gates
    if allow is not None:
        env["CI_TPL_ALLOW_UNGATED"] = allow
    return subprocess.run(
        ["/bin/sh", "-c", f". '{HELPER}'\nci_tpl_require_gate {extra}"],
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("gates", ['json []', 'json [ ]', '', 'json'])
def test_an_empty_gate_list_fails_the_job(gates):
    result = run(gates)
    assert result.returncode != 0
    assert "gate-jobs is empty" in result.stderr
    assert "allow-ungated" in result.stderr


def test_an_absent_variable_fails_the_job():
    """Unset is not gated either; a missing variable must not read as one."""
    result = run(None)
    assert result.returncode != 0


def test_a_named_gate_passes_silently():
    result = run('json [{"job": "api:security-image-trivy", "artifacts": false}]')
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_allow_ungated_publishes_and_says_so():
    result = run("json []", allow="true")
    assert result.returncode == 0
    assert "WARNING" in result.stdout
    assert "allow-ungated is set" in result.stdout


def test_a_value_other_than_true_does_not_allow_it():
    for value in ("false", "TRUE", "yes", "1", ""):
        assert run("json []", allow=value).returncode != 0, value


def test_a_further_scalar_gate_counts():
    """container-export-skopeo still accepts the deprecated mirror-job."""
    result = run("json []", extra="'mirror:container-mirror-skopeo'")
    assert result.returncode == 0, result.stderr
