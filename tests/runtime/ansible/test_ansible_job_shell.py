"""Behaviour tests for the shell the ansible-* jobs execute.

The shell is extracted from the component templates and run as it ships, rather
than from a copy kept beside these tests. A copy would pass while the template
drifted; a lint of the YAML would prove the file parses and nothing about what
the job does with a traversal path or an unset credential.

What is exercised here is the part of each job that decides whether to run at
all: the checkout escape check, the path validation that defends the reserved
CI_TPL_ variables against a project variable of the same name, the Ansible Vault
password handling, and the argument assembly. The tools themselves are stubbed;
this says nothing about ansible-lint or ansible-playbook behaviour.

Run: python3 -m pytest -q tests/runtime/ansible/
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = REPO_ROOT / "templates"

STUB = """#!/bin/sh
printf '%s\\n' "$0" > "$ARGV_LOG"
for arg in "$@"; do printf '%s\\n' "$arg" >> "$ARGV_LOG"; done
"""

# ansible-lint is stubbed more closely than the others because its job now
# reads what the tool wrote. `--version` is answered without touching the argv
# log: the gate asks for it after the run, and recording it would overwrite the
# invocation under test. The SARIF file is written wherever --sarif-file points,
# empty of results, which is what the real tool does on a clean tree.
ANSIBLE_LINT_STUB = """#!/bin/sh
if [ "$1" = "--version" ]; then echo 'ansible-lint 26.4.0'; exit 0; fi
printf '%s\\n' "$0" > "$ARGV_LOG"
for arg in "$@"; do printf '%s\\n' "$arg" >> "$ARGV_LOG"; done
sarif=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = '--sarif-file' ]; then sarif=$2; fi
  shift
done
if [ -n "$sarif" ]; then
  printf '%s' '{"version": "2.1.0", "runs": [{"results": []}]}' > "$sarif"
fi
exit "${STUB_ANSIBLE_LINT_EXIT:-0}"
"""

# macOS ships shasum, not sha256sum, and the embedded gate refuses to write
# evidence without one.
SHA256SUM = """#!/bin/sh
if command -v /usr/bin/sha256sum >/dev/null 2>&1; then exec /usr/bin/sha256sum "$@"; fi
exec /usr/bin/shasum -a 256 "$@"
"""


def job_shell(component: str) -> str:
    """The job's before_script and script, concatenated as the runner does."""
    documents = list(
        yaml.safe_load_all((TEMPLATES / component / "template.yml").read_text())
    )
    jobs = documents[1]
    assert len(jobs) == 1, f"{component} emits more than one job"
    (job,) = jobs.values()
    return "\n".join(job.get("before_script", []) + job.get("script", []))


def job_before_script(component: str) -> str:
    """Only the part that runs before any tool is invoked."""
    documents = list(
        yaml.safe_load_all((TEMPLATES / component / "template.yml").read_text())
    )
    (job,) = documents[1].values()
    return "\n".join(job.get("before_script", []))


@pytest.fixture()
def checkout(tmp_path):
    """A minimal consumer checkout: a playbook, an inventory and a vars file."""
    root = tmp_path / "checkout"
    (root / "ansible" / "playbooks").mkdir(parents=True)
    (root / "ansible" / "inventories").mkdir(parents=True)
    (root / "ansible" / "playbooks" / "site.yml").write_text("- hosts: all\n")
    (root / "ansible" / "inventories" / "hosts.yml").write_text("all:\n")
    (root / "ansible" / "extra.yml").write_text("key: value\n")
    (tmp_path / "outside").mkdir()
    return root


@pytest.fixture()
def stubs(tmp_path):
    """ansible-lint and ansible-playbook stubs that record the argv they saw."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "argv.log"
    for name, body in (
        ("ansible-lint", ANSIBLE_LINT_STUB),
        ("ansible-playbook", STUB),
        ("sha256sum", SHA256SUM),
    ):
        stub = bindir / name
        stub.write_text(body)
        stub.chmod(0o755)
    return bindir, log


def run(component, checkout, stubs, env=None, cwd=None):
    bindir, log = stubs
    environment = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "ARGV_LOG": str(log),
        "CI_PROJECT_DIR": str(checkout),
        "CI_TPL_WORKING_DIRECTORY": "ansible",
        "CI_TPL_VAULT_PASSWORD_SECRET": "",
        "CI_TPL_ARTIFACT_DIR": ".ci-artifacts/demo/ansible-lint",
        # The job writes the gate wrapper outside the checkout on purpose; see
        # test_the_gate_wrapper_is_not_written_into_the_tree_being_linted.
        "CI_TPL_RUNTIME_DIR": str(Path(str(checkout)).parent / "runtime"),
        "CI_TPL_POLICY_MODE": "blocking",
        "CI_TPL_EXECUTION_IMAGE": "docker.io/example@sha256:" + "0" * 64,
        "CI_TPL_PLAYBOOK": "playbooks/site.yml",
        "CI_TPL_INVENTORY": "inventories/hosts.yml",
        "CI_TPL_EXTRA_VARS_FILE": "",
        "CI_TPL_LIMIT": "",
        "CI_TPL_DIFF": "false",
        "ANSIBLE_HOST_KEY_CHECKING": "True",
    }
    environment.update(env or {})
    return subprocess.run(
        ["sh", "-c", job_shell(component)],
        env=environment,
        cwd=cwd or str(checkout),
        capture_output=True,
        text=True,
    )


def argv(stubs):
    _, log = stubs
    return log.read_text().splitlines()


ALL = ("ansible-lint", "ansible-syntax", "ansible-check")


@pytest.mark.parametrize("component", ALL)
def test_the_happy_path_invokes_the_tool(component, checkout, stubs):
    result = run(component, checkout, stubs)
    assert result.returncode == 0, result.stderr
    assert argv(stubs), "the tool was never reached"


@pytest.mark.parametrize("component", ALL)
def test_a_working_directory_outside_the_checkout_fails(component, checkout, stubs):
    result = run(
        component, checkout, stubs, env={"CI_TPL_WORKING_DIRECTORY": "../outside"}
    )
    assert result.returncode == 1
    assert "escapes the checkout" in result.stdout
    assert not (stubs[1]).exists(), "the tool ran despite the escape"


@pytest.mark.parametrize("component", ALL)
def test_a_missing_working_directory_fails(component, checkout, stubs):
    result = run(component, checkout, stubs, env={"CI_TPL_WORKING_DIRECTORY": "nope"})
    assert result.returncode == 1
    assert "does not exist" in result.stdout
    assert not (stubs[1]).exists()


@pytest.mark.parametrize("component", ("ansible-syntax", "ansible-check"))
def test_a_traversing_playbook_path_fails(component, checkout, stubs):
    """The input regex cannot express "no ..", so the job checks at runtime.

    This is also the defence against a project variable overriding CI_TPL_PLAYBOOK,
    which outranks the job variable the component sets.
    """
    result = run(
        component, checkout, stubs, env={"CI_TPL_PLAYBOOK": "../../etc/passwd.yml"}
    )
    assert result.returncode == 1
    assert "must not traverse upwards" in result.stdout
    assert not (stubs[1]).exists()


@pytest.mark.parametrize("component", ("ansible-syntax", "ansible-check"))
def test_an_absolute_playbook_path_fails(component, checkout, stubs):
    result = run(component, checkout, stubs, env={"CI_TPL_PLAYBOOK": "/etc/site.yml"})
    assert result.returncode == 1
    assert "must be relative to working-directory" in result.stdout


@pytest.mark.parametrize("component", ("ansible-syntax", "ansible-check"))
def test_a_playbook_that_does_not_exist_fails(component, checkout, stubs):
    result = run(component, checkout, stubs, env={"CI_TPL_PLAYBOOK": "absent.yml"})
    assert result.returncode == 1
    assert "playbook not found" in result.stdout
    assert not (stubs[1]).exists()


def test_ansible_check_refuses_an_empty_inventory(checkout, stubs):
    """Without an inventory the run silently checks the implicit localhost."""
    result = run("ansible-check", checkout, stubs, env={"CI_TPL_INVENTORY": ""})
    assert result.returncode == 1
    assert "playbook and inventory are both required" in result.stdout


def test_ansible_check_refuses_disabled_host_key_checking(checkout, stubs):
    """A project variable outranks the job variable, so the value is re-checked."""
    result = run(
        "ansible-check", checkout, stubs, env={"ANSIBLE_HOST_KEY_CHECKING": "False"}
    )
    assert result.returncode == 1
    assert "host key checking must stay on" in result.stdout
    assert not (stubs[1]).exists()


@pytest.mark.parametrize("component", ALL)
def test_a_named_vault_variable_that_is_unset_fails(component, checkout, stubs):
    """Missing credentials fail the job; they do not produce a silent clean run."""
    result = run(
        component,
        checkout,
        stubs,
        env={"CI_TPL_VAULT_PASSWORD_SECRET": "ANSIBLE_VAULT_PASSWORD"},
    )
    assert result.returncode == 1
    assert "is not set" in result.stdout
    assert not (stubs[1]).exists()


@pytest.mark.parametrize("component", ALL)
def test_an_empty_vault_variable_fails(component, checkout, stubs):
    result = run(
        component,
        checkout,
        stubs,
        env={
            "CI_TPL_VAULT_PASSWORD_SECRET": "ANSIBLE_VAULT_PASSWORD",
            "ANSIBLE_VAULT_PASSWORD": "",
        },
    )
    assert result.returncode == 1
    assert "is empty" in result.stdout


@pytest.mark.parametrize("component", ALL)
def test_the_vault_password_reaches_a_private_file_and_not_the_log(
    component, checkout, stubs, tmp_path
):
    """The value is written to a 0600 file and never printed.

    The job log is the transcript an incident reviewer reads, so the check is
    that the password appears nowhere in stdout or stderr, and that the file
    Ansible is pointed at is readable only by the job's own user.
    """
    password = "correct-horse-battery-staple"
    probe = tmp_path / "probe.sh"
    probe.write_text(
        job_before_script(component)
        + "\n"
        + 'printf "%s %s\\n" "$ANSIBLE_VAULT_PASSWORD_FILE" '
        + '"$(stat -c %a "$ANSIBLE_VAULT_PASSWORD_FILE" 2>/dev/null '
        + '|| stat -f %Lp "$ANSIBLE_VAULT_PASSWORD_FILE")" > '
        + str(tmp_path / "vault-file.txt")
        + "\n"
    )
    result = run(
        component,
        checkout,
        stubs,
        env={
            "CI_TPL_VAULT_PASSWORD_SECRET": "ANSIBLE_VAULT_PASSWORD",
            "ANSIBLE_VAULT_PASSWORD": password,
        },
    )
    assert result.returncode == 0, result.stderr
    assert password not in result.stdout
    assert password not in result.stderr

    # Positive control: the job's own before_script, which is everything that
    # runs before a tool is invoked, has to show
    # that the file was actually created with mode 600 and holds the password.
    # Without this the assertions above would pass on a job that wrote nothing.
    control = subprocess.run(
        ["sh", "-c", probe.read_text()],
        env={
            "PATH": os.environ["PATH"],
            "CI_PROJECT_DIR": str(checkout),
            "CI_TPL_WORKING_DIRECTORY": "ansible",
            "CI_TPL_VAULT_PASSWORD_SECRET": "ANSIBLE_VAULT_PASSWORD",
            "ANSIBLE_VAULT_PASSWORD": password,
            "CI_TPL_ARTIFACT_DIR": ".ci-artifacts/demo/ansible-lint",
            "CI_TPL_RUNTIME_DIR": str(tmp_path / "runtime"),
            "CI_TPL_POLICY_MODE": "blocking",
            "CI_TPL_EXECUTION_IMAGE": "docker.io/example@sha256:" + "0" * 64,
            "CI_TPL_PLAYBOOK": "playbooks/site.yml",
            "CI_TPL_INVENTORY": "inventories/hosts.yml",
            "CI_TPL_EXTRA_VARS_FILE": "",
            "CI_TPL_LIMIT": "",
            "CI_TPL_DIFF": "false",
            "ANSIBLE_HOST_KEY_CHECKING": "True",
        },
        cwd=str(checkout),
        capture_output=True,
        text=True,
    )
    assert control.returncode == 0, control.stderr
    recorded = (tmp_path / "vault-file.txt").read_text().split()
    assert recorded[1] == "600", f"vault password file mode is {recorded[1]}"
    assert Path(recorded[0]).read_text().strip() == password


def test_ansible_syntax_builds_the_expected_arguments(checkout, stubs):
    run(
        "ansible-syntax",
        checkout,
        stubs,
        env={"CI_TPL_EXTRA_VARS_FILE": "extra.yml"},
    )
    assert argv(stubs)[1:] == [
        "--syntax-check",
        "--inventory",
        "inventories/hosts.yml",
        "playbooks/site.yml",
        "--extra-vars",
        "@extra.yml",
    ]


def test_ansible_syntax_omits_the_inventory_when_it_is_empty(checkout, stubs):
    run("ansible-syntax", checkout, stubs, env={"CI_TPL_INVENTORY": ""})
    assert "--inventory" not in argv(stubs)


def test_ansible_check_builds_the_expected_arguments(checkout, stubs):
    run(
        "ansible-check",
        checkout,
        stubs,
        env={"CI_TPL_LIMIT": "webservers:!db-01", "CI_TPL_EXTRA_VARS_FILE": "extra.yml"},
    )
    assert argv(stubs)[1:] == [
        "--inventory",
        "inventories/hosts.yml",
        "playbooks/site.yml",
        "--check",
        "--limit",
        "webservers:!db-01",
        "--extra-vars",
        "@extra.yml",
    ]


def test_ansible_check_never_drops_check_mode(checkout, stubs):
    """--check is not behind a condition. A check job that mutates is the failure."""
    run("ansible-check", checkout, stubs)
    assert "--check" in argv(stubs)


def test_ansible_check_leaves_diff_off_by_default(checkout, stubs):
    run("ansible-check", checkout, stubs)
    assert "--diff" not in argv(stubs)


def test_ansible_check_passes_diff_when_asked(checkout, stubs):
    run("ansible-check", checkout, stubs, env={"CI_TPL_DIFF": "true"})
    assert "--diff" in argv(stubs)


def test_ansible_lint_runs_offline(checkout, stubs):
    """Section 12: a lint that installs collections mid-run is not pinned."""
    run("ansible-lint", checkout, stubs)
    assert "--offline" in argv(stubs)


def test_ansible_lint_passes_no_path_argument(checkout, stubs):
    """A path argument turns the lint into a silent no-op.

    Measured against ansible-lint 26.4.0 in the pinned image on 2026/09/15:
    `ansible-lint --offline --nocolor .` on a tree whose playbook has five
    violations reports "0 files processed of 1 encountered" and exits 0, while
    the same command without the `.` exits 2. Anyone tempted to add a path back,
    for tidiness or to scope the lint, breaks the gate without turning it red.
    Scope it with working-directory instead.
    """
    result = run("ansible-lint", checkout, stubs)
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = argv(stubs)[1:]
    assert arguments[:2] == ["--offline", "--nocolor"]
    assert arguments[2] == "--sarif-file"
    assert arguments[3].endswith("/.ci-artifacts/demo/ansible-lint/ansible-lint.sarif")
    assert len(arguments) == 4, arguments
