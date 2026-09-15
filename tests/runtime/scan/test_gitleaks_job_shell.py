"""The gitleaks job must reach its gate on a CLEAN report, not abort before it.

platform/cluster-gitops MR !7, pipeline 5663, job `mgt:security-secrets-gitleaks`:
gitleaks logged "no leaks found" and wrote `[]`, and the job exited 1 without
writing scan-result.json. The counting line was

    findings=$(grep -o '"RuleID"' "$artifacts/gitleaks.json" | wc -l | tr -d ' ')

and the runner runs each script block under `set -eo pipefail`. grep exits 1 when
it matches nothing, pipefail makes that the pipeline's status, the assignment
fails and `set -e` kills the job before scan-gate.sh runs. The component passed
never: a clean tree aborted, a dirty tree gated.

These tests run the job's own before_script and script, joined as the runner
joins them, under `bash -eo pipefail`. The gitleaks binary is stubbed so a test
can name the outcome; everything else — the embedded scan-gate.sh, the counting,
the evidence record — is the shipped bytes. The clean case is the regression
test: it fails on the old line and passes on the new one.

Run: python3 -m pytest -q tests/runtime/scan/test_gitleaks_job_shell.py
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import string
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = REPO_ROOT / "templates" / "security-secrets-gitleaks" / "template.yml"

CLEAN_REPORT = "[]"
# One finding, in the shape gitleaks 8.21.2 writes.
DIRTY_REPORT = json.dumps(
    [{"RuleID": "generic-api-key", "File": "app/config.py", "Secret": "REDACTED"}]
)

# `detect` writes the report at --report-path and exits 1 when it found
# something; `version` answers without touching the report.
GITLEAKS_STUB = """#!/bin/sh
if [ "$1" = version ]; then echo '8.21.2'; exit 0; fi
report=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = '--report-path' ]; then report=$2; fi
  shift
done
[ -n "${STUB_REPORT_OVERRIDE:-}" ] && report=$STUB_REPORT_OVERRIDE
[ -n "$report" ] || { echo 'stub: no --report-path' >&2; exit 2; }
cat "$STUB_REPORT" > "$report"
exit "${STUB_EXIT:-0}"
"""

# macOS ships shasum; scan-gate.sh refuses to write evidence without sha256sum.
SHA256SUM = """#!/bin/sh
if command -v /usr/bin/sha256sum >/dev/null 2>&1; then exec /usr/bin/sha256sum "$@"; fi
exec /usr/bin/shasum -a 256 "$@"
"""


def job_shell() -> str:
    documents = list(yaml.safe_load_all(TEMPLATE.read_text()))
    (job,) = documents[1].values()
    return "\n".join(job.get("before_script", []) + job.get("script", []))


@pytest.fixture()
def checkout(tmp_path):
    """A real git repository: the job counts commits with git rev-list."""
    root = tmp_path / "checkout"
    root.mkdir()
    environment = dict(
        os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null"
    )
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.name", "t"],
        ["git", "config", "user.email", "t@example.com"],
    ):
        subprocess.run(command, cwd=root, check=True, env=environment)
    (root / "app.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=environment)
    subprocess.run(
        ["git", "commit", "-qm", "init"], cwd=root, check=True, env=environment
    )
    return root


@pytest.fixture()
def stubs(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("gitleaks", GITLEAKS_STUB), ("sha256sum", SHA256SUM)):
        stub = bindir / name
        stub.write_text(body)
        stub.chmod(0o755)
    return bindir


def run(checkout, stubs, report, exit_code, policy_mode="blocking", report_path=None):
    report_file = Path(checkout).parent / "report.json"
    report_file.write_text(report)
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", job_shell()],
        env={
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "STUB_REPORT": str(report_file),
            # Where the stub writes, when a test needs the real report path to be
            # something the job cannot read.
            "STUB_REPORT_OVERRIDE": str(report_path or ""),
            "STUB_EXIT": str(exit_code),
            "CI_PROJECT_DIR": str(checkout),
            "CI_TPL_WORKING_DIRECTORY": ".",
            "CI_TPL_ARTIFACT_DIR": ".ci-artifacts/demo/security-secrets-gitleaks",
            "CI_TPL_POLICY_MODE": policy_mode,
            "CI_TPL_EXECUTION_IMAGE": "registry.example.com/x@sha256:" + "0" * 64,
            "GIT_DEPTH": "50",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
        cwd=str(checkout),
        capture_output=True,
        text=True,
    )


def evidence(checkout) -> dict:
    path = (
        Path(checkout)
        / ".ci-artifacts/demo/security-secrets-gitleaks/scan-result.json"
    )
    assert path.is_file(), "scan-gate.sh wrote no evidence record"
    return json.loads(path.read_text())


def test_a_clean_report_reaches_the_gate_and_passes(checkout, stubs):
    """The regression. `[]` means grep matches nothing; the job must still run
    the gate and write its evidence."""
    result = run(checkout, stubs, CLEAN_REPORT, 0)
    assert result.returncode == 0, result.stdout + result.stderr
    record = evidence(checkout)
    assert record["outcome"] == "clean", record
    assert record["counts"]["total"] == 0
    assert record["completion"] == "completed"


def test_a_dirty_report_gates(checkout, stubs):
    """The other half. A component that only ever aborted would also pass the
    test above if it never reached the gate at all."""
    result = run(checkout, stubs, DIRTY_REPORT, 1)
    assert result.returncode == 1, result.stdout + result.stderr
    record = evidence(checkout)
    assert record["outcome"] == "findings", record
    assert record["counts"]["at_or_above_threshold"] == 1


def test_advisory_mode_records_a_finding_without_failing(checkout, stubs):
    result = run(checkout, stubs, DIRTY_REPORT, 1, policy_mode="advisory")
    assert result.returncode == 0, result.stdout + result.stderr
    assert evidence(checkout)["outcome"] == "findings"


def test_an_unreadable_report_is_not_counted_as_zero_findings(checkout, stubs):
    """grep exit 1 is "no match"; 2 or more is a read failure. Collapsing both to
    zero would turn an unreadable report into a clean scan, which is the failure
    the fix must not introduce while removing the abort."""
    report_dir = Path(checkout) / ".ci-artifacts/demo/security-secrets-gitleaks"
    report_dir.mkdir(parents=True)
    # grep exits 2 on a directory. The scanner stub is pointed at a writable path
    # so the scan itself still succeeds and the read failure is the only fault.
    (report_dir / "gitleaks.json").mkdir()
    result = run(checkout, stubs, CLEAN_REPORT, 0, report_path=report_dir / "elsewhere.json")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "cannot read the gitleaks report" in result.stderr, result.stdout + result.stderr


# --------------------------------------------------------------------------
# The same job script, in the image it ships with, against the real scanner
# --------------------------------------------------------------------------
#
# Everything above stubs gitleaks, so it proves the wrapper's decisions and
# nothing about the report the tool actually writes. The production failure was
# the shell reacting to a real clean report, so the fix is also checked against
# the real binary in the pinned image.

IMAGE = next(yaml.safe_load_all(TEMPLATE.read_text()))["spec"]["inputs"]["execution-image"]["default"]

def planted_token() -> str:
    """A syntactically valid GitLab personal access token, generated per run.

    Measured against gitleaks 8.21.2 in the pinned image on 2026/09/16: this
    shape trips the `gitlab-pat` rule. AWS's documented example access key id
    does NOT trip `aws-access-token` in this version, so a test built on it
    would have asserted "no findings" and called the gate proven.

    Generated rather than written down, so no fixed token-shaped string lives in
    the repository. It grants nothing: the prefix and length are the whole of
    what the rule matches.
    """
    alphabet = string.ascii_letters + string.digits
    return "glpat-" + "".join(secrets.choice(alphabet) for _ in range(20))

def docker_is_usable() -> bool:
    """No docker binary at all is the CI runner's case, and it must skip, not
    error. `docker info` raises FileNotFoundError there rather than returning a
    status, which failed runtime-tests at collection in pipeline 5706."""
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


needs_docker = pytest.mark.skipif(
    not docker_is_usable(),
    reason="no usable docker; start colima locally to exercise the pinned image",
)


@pytest.fixture()
def home_checkout():
    """A git repository under $HOME.

    Docker on Colima only bind-mounts paths under the user's home directory; a
    scratch directory in /private/var mounts empty and the scan finds nothing,
    which would read as a clean tree.
    """
    import tempfile

    base = Path.home() / ".cache" / "gitlab-ci-templates-tests"
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(dir=base))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def commit(root: Path, files: dict[str, str]) -> None:
    environment = dict(
        os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null"
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, env=environment)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, env=environment)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True, env=environment)
    for name, body in files.items():
        (root / name).write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=environment)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True, env=environment)


def run_in_image(root: Path, policy_mode: str = "blocking"):
    return subprocess.run(
        [
            "docker", "run", "--rm", "--entrypoint", "bash",
            "-v", f"{root}:/src", "-w", "/src",
            "-e", "CI_PROJECT_DIR=/src",
            "-e", "CI_TPL_WORKING_DIRECTORY=.",
            "-e", "CI_TPL_ARTIFACT_DIR=.ci-artifacts/demo/security-secrets-gitleaks",
            "-e", f"CI_TPL_POLICY_MODE={policy_mode}",
            "-e", f"CI_TPL_EXECUTION_IMAGE={IMAGE}",
            "-e", "GIT_DEPTH=50",
            IMAGE, "-eo", "pipefail", "-c", job_shell(),
        ],
        capture_output=True, text=True, timeout=600,
    )


@needs_docker
def test_the_real_scanner_on_a_clean_tree_passes_in_the_pinned_image(home_checkout):
    """The production case: gitleaks finds nothing, writes `[]`, and the job must
    pass with its evidence written."""
    commit(home_checkout, {"app.py": "value = 1\n"})
    result = run_in_image(home_checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    record = evidence(home_checkout)
    assert record["outcome"] == "clean", record
    assert record["counts"]["total"] == 0
    assert record["scanner"]["version"].strip() == "v8.21.2"


@needs_docker
def test_the_real_scanner_on_a_planted_secret_gates_in_the_pinned_image(home_checkout):
    commit(home_checkout, {"config.py": f'token = "{planted_token()}"\n'})
    result = run_in_image(home_checkout)
    assert result.returncode == 1, result.stdout + result.stderr
    record = evidence(home_checkout)
    assert record["outcome"] == "findings", record
    assert record["counts"]["at_or_above_threshold"] >= 1
