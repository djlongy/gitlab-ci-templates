"""Behaviour of the shell the terraform-validate job runs, per lockfile-mode.

The shell is extracted from templates/terraform-validate/template.yml and run as
it ships. A structural test can read the file and see both flags in a case
statement; only running it says which branch a given lockfile-mode takes, which
is the whole of what this input decides.

terraform is stubbed. Nothing here says anything about what Terraform does with
the flags, only about which flags reach it.

Run: python3 -m pytest -q tests/runtime/terraform/test_terraform_validate_job_shell.py
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = REPO_ROOT / "templates" / "terraform-validate" / "template.yml"

STUB = """#!/bin/sh
printf '%s' terraform >>"$ARGV_LOG"
for arg in "$@"; do printf ' %s' "$arg" >>"$ARGV_LOG"; done
printf '\\n' >>"$ARGV_LOG"
exit 0
"""


def job_shell() -> str:
    """The job's before_script and script, concatenated as the runner runs them."""
    documents = list(yaml.safe_load_all(TEMPLATE.read_text()))
    jobs = documents[1]
    assert len(jobs) == 1, "terraform-validate emits more than one job"
    (job,) = jobs.values()
    return "\n".join(job.get("before_script", []) + job.get("script", []))


@pytest.fixture()
def checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / "modules" / "vm").mkdir(parents=True)
    (root / "modules" / "vm" / "main.tf").write_text("# module\n")
    (tmp_path / "outside").mkdir()
    return root


@pytest.fixture()
def stubs(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "terraform"
    stub.write_text(STUB)
    stub.chmod(0o755)
    return bindir, tmp_path / "argv.log"


def run(checkout, stubs, mode, working_directory="modules/vm"):
    bindir, log = stubs
    return subprocess.run(
        ["sh", "-c", job_shell()],
        env={
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "ARGV_LOG": str(log),
            "CI_PROJECT_DIR": str(checkout),
            "CI_TPL_WORKING_DIRECTORY": working_directory,
            "CI_TPL_LOCKFILE_MODE": mode,
            "TF_IN_AUTOMATION": "true",
        },
        cwd=str(checkout),
        capture_output=True,
        text=True,
    )


def init_line(stubs) -> str:
    _, log = stubs
    lines = [line for line in log.read_text().splitlines() if " init " in f"{line} "]
    assert len(lines) == 1, f"expected exactly one init invocation, saw {lines}"
    return lines[0]


def test_readonly_mode_holds_the_committed_lockfile(checkout, stubs):
    """The default. Section 11.2 item 2: CI may not rewrite a committed lockfile."""
    result = run(checkout, stubs, "readonly")
    assert result.returncode == 0, result.stderr
    assert "-lockfile=readonly" in init_line(stubs)


def test_module_mode_omits_the_readonly_lockfile_flag(checkout, stubs):
    """A module repository commits no .terraform.lock.hcl, so readonly init fails
    with "Provider dependency changes detected" (seen on a consumer merge request,
    a consumer pipeline). This is the branch that mode exists for."""
    result = run(checkout, stubs, "module")
    assert result.returncode == 0, result.stderr
    assert "-lockfile=readonly" not in init_line(stubs)


@pytest.mark.parametrize("mode", ["readonly", "module"])
def test_neither_mode_upgrades_providers(checkout, stubs, mode):
    """`module` relaxes the lockfile check. It must not also move versions."""
    run(checkout, stubs, mode)
    assert "-upgrade" not in init_line(stubs)


@pytest.mark.parametrize("mode", ["readonly", "module"])
def test_both_modes_keep_init_off_the_backend_and_non_interactive(checkout, stubs, mode):
    run(checkout, stubs, mode)
    line = init_line(stubs)
    assert "-backend=false" in line
    assert "-input=false" in line


def test_an_unrecognised_mode_fails_rather_than_choosing_one(checkout, stubs):
    """`options:` constrains the input, but CI_TPL_LOCKFILE_MODE is a job
    variable and a project variable of the same name outranks it."""
    result = run(checkout, stubs, "whatever")
    assert result.returncode == 1
    assert "unknown lockfile-mode" in result.stdout
    assert not stubs[1].exists(), "terraform ran despite an unrecognised mode"


def test_a_working_directory_outside_the_checkout_fails(checkout, stubs):
    result = run(checkout, stubs, "readonly", working_directory="../outside")
    assert result.returncode == 1
    assert "escapes the checkout" in result.stdout
    assert not stubs[1].exists(), "terraform ran despite the escape"
