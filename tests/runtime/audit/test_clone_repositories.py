"""Exit-code and coverage tests for runtime/audit/clone-repositories.sh.

The behaviour under test is section 11.6's rule, which both templates this
replaces broke: "A failure to clone an in-scope repository must report
incomplete coverage and fail the required audit; scanning zero repositories is
not success." security/group-scan.yml and security/terraform-audit.yml each
printed a SKIP line on a clone failure and exited 0, so an audit that reached
nothing reported success. Every test here fails against that old behaviour.

No network: repository sets are local bare repositories reached over file://,
and the group listing is exercised through a stub `curl` placed on PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "runtime" / "audit" / "clone-repositories.sh"

CLONED, FAILED_TO_CLONE, USAGE, NO_SCOPE = 0, 2, 1, 3


def make_bare_repo(root: Path, path: str) -> None:
    """Create <root>/<path>.git holding one commit."""
    work = root / "work" / path.replace("/", "_")
    work.mkdir(parents=True)
    (work / "main.tf").write_text('resource "null_resource" "x" {}\n')
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    git = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "-b", "main", str(work)], check=True, env=env)
    subprocess.run([*git, "-C", str(work), "add", "-A"], check=True, env=env)
    subprocess.run([*git, "-C", str(work), "commit", "-qm", "init"], check=True, env=env)
    bare = root / f"{path}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [*git, "clone", "-q", "--bare", str(work), str(bare)], check=True, env=env
    )


def run(tmp_path: Path, *, extra_env: dict | None = None, path_prefix: Path | None = None):
    destination = tmp_path / "repos"
    coverage = tmp_path / "coverage" / "audit-coverage.json"
    env = {
        "PATH": (f"{path_prefix}:" if path_prefix else "") + os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "CI_TPL_SERVER_URL": f"file://{tmp_path / 'server'}",
        "CI_TPL_COVERAGE_FILE": str(coverage),
    }
    env.update(extra_env or {})
    (tmp_path / "home").mkdir(exist_ok=True)
    completed = subprocess.run(
        ["bash", str(SCRIPT), str(destination)],
        capture_output=True,
        text=True,
        env=env,
    )
    return completed, destination, coverage


def read_coverage(path: Path) -> dict:
    return json.loads(path.read_text())


def test_explicit_set_clones_every_repository_and_records_coverage(tmp_path):
    server = tmp_path / "server"
    make_bare_repo(server, "platform/alpha")
    make_bare_repo(server, "platform/beta")

    result, destination, coverage = run(
        tmp_path, extra_env={"CI_TPL_REPOSITORIES": "platform/alpha platform/beta"}
    )

    assert result.returncode == CLONED, result.stderr
    assert (destination / "platform_alpha" / "main.tf").exists()
    assert (destination / "platform_beta" / "main.tf").exists()
    record = read_coverage(coverage)
    assert record["in_scope"] == 2
    assert record["cloned"] == 2
    assert record["source"] == "explicit"
    assert {entry["path"] for entry in record["repositories"]} == {
        "platform/alpha",
        "platform/beta",
    }


def test_one_unclonable_repository_fails_the_audit(tmp_path):
    """The old templates printed SKIP here and exited 0."""
    server = tmp_path / "server"
    make_bare_repo(server, "platform/alpha")

    result, _, coverage = run(
        tmp_path, extra_env={"CI_TPL_REPOSITORIES": "platform/alpha platform/gone"}
    )

    assert result.returncode == FAILED_TO_CLONE
    assert "coverage is incomplete" in result.stderr
    record = read_coverage(coverage)
    assert record["in_scope"] == 2 and record["cloned"] == 1
    outcomes = {entry["path"]: entry["outcome"] for entry in record["repositories"]}
    assert outcomes == {"platform/alpha": "cloned", "platform/gone": "failed"}


def test_an_empty_repository_set_is_not_a_pass(tmp_path):
    result, _, coverage = run(tmp_path, extra_env={"CI_TPL_REPOSITORIES": "   "})
    assert result.returncode == NO_SCOPE
    assert "scanning nothing is not a pass" in result.stderr
    assert not coverage.exists()


def test_no_selector_is_a_usage_error(tmp_path):
    result, _, _ = run(tmp_path)
    assert result.returncode == USAGE
    assert "CI_TPL_REPOSITORIES or CI_TPL_GROUP_PATH" in result.stderr


def test_both_selectors_is_a_usage_error(tmp_path):
    result, _, _ = run(
        tmp_path,
        extra_env={"CI_TPL_REPOSITORIES": "platform/alpha", "CI_TPL_GROUP_PATH": "platform"},
    )
    assert result.returncode == USAGE
    assert "not both" in result.stderr


def stub_curl(tmp_path: Path, body: str, *, exit_code: int = 0) -> Path:
    """A `curl` on PATH that answers the group listing with `body`.

    Page 2 onwards returns an empty array, which is how the real paging loop
    terminates.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        f"exit_code={exit_code}\n"
        '[ "$exit_code" -ne 0 ] && exit "$exit_code"\n'
        'case "$*" in *"&page=1"*) '
        f"cat <<'JSON'\n{body}\nJSON\n"
        ";; *) echo '[]' ;; esac\n"
    )
    curl.chmod(0o755)
    return bin_dir


def test_group_listing_resolves_and_clones(tmp_path):
    server = tmp_path / "server"
    make_bare_repo(server, "platform/alpha")
    make_bare_repo(server, "platform/nested/beta")
    body = json.dumps(
        [
            {"path_with_namespace": "platform/alpha"},
            {"path_with_namespace": "platform/nested/beta"},
        ]
    )

    result, destination, coverage = run(
        tmp_path,
        extra_env={
            "CI_TPL_GROUP_PATH": "platform",
            "CI_TPL_API_URL": "https://git.invalid/api/v4",
            "CI_TPL_AUDIT_TOKEN": "not-a-real-token",
        },
        path_prefix=stub_curl(tmp_path, body),
    )

    assert result.returncode == CLONED, result.stderr
    record = read_coverage(coverage)
    assert record["source"] == "group" and record["group_path"] == "platform"
    assert record["in_scope"] == 2 and record["cloned"] == 2
    assert (destination / "platform_nested_beta" / "main.tf").exists()


def test_a_group_that_lists_nothing_fails(tmp_path):
    """SCANNED=0 was the old job's green path."""
    result, _, coverage = run(
        tmp_path,
        extra_env={
            "CI_TPL_GROUP_PATH": "platform",
            "CI_TPL_API_URL": "https://git.invalid/api/v4",
            "CI_TPL_AUDIT_TOKEN": "not-a-real-token",
        },
        path_prefix=stub_curl(tmp_path, "[]"),
    )
    assert result.returncode == NO_SCOPE
    assert "scanning nothing is not a pass" in result.stderr
    assert not coverage.exists()


def test_a_failed_listing_call_fails_the_audit(tmp_path):
    """An error response used to yield an empty project list and a green job."""
    result, _, _ = run(
        tmp_path,
        extra_env={
            "CI_TPL_GROUP_PATH": "platform",
            "CI_TPL_API_URL": "https://git.invalid/api/v4",
            "CI_TPL_AUDIT_TOKEN": "not-a-real-token",
        },
        path_prefix=stub_curl(tmp_path, "[]", exit_code=22),
    )
    assert result.returncode == NO_SCOPE
    assert "listing projects" in result.stderr


def test_the_token_is_written_to_a_private_credential_file_not_the_clone_url(tmp_path):
    """The old jobs interpolated the token into the URL, where git echoes it."""
    server = tmp_path / "server"
    make_bare_repo(server, "platform/alpha")

    result, destination, _ = run(
        tmp_path,
        extra_env={
            "CI_TPL_REPOSITORIES": "platform/alpha",
            "CI_TPL_AUDIT_TOKEN": "not-a-real-token",
        },
    )

    assert result.returncode == CLONED, result.stderr
    credentials = destination / ".git-credentials"
    assert credentials.exists()
    assert credentials.stat().st_mode & 0o077 == 0
    assert "not-a-real-token" not in result.stdout + result.stderr


@pytest.mark.parametrize("missing", ["CI_TPL_COVERAGE_FILE", "CI_TPL_SERVER_URL"])
def test_missing_required_environment_is_a_usage_error(tmp_path, missing):
    result, _, _ = run(
        tmp_path, extra_env={"CI_TPL_REPOSITORIES": "platform/alpha", missing: ""}
    )
    assert result.returncode == USAGE
