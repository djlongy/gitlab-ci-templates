"""Behaviour tests for runtime/scan/lockfiles.sh.

The two tests that matter most are the two the previous check failed: a lockfile
in a subdirectory that root-only detection never looked for, and a
requirements.txt where one line is pinned and the rest float.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCKFILES = REPO_ROOT / "runtime" / "scan" / "lockfiles.sh"

PASS, CHECK_FAILURE, USAGE_ERROR = 0, 1, 2


def run(tmp_path: Path, declared: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "sh",
            str(LOCKFILES),
            "--lockfiles",
            declared,
            "--out",
            "evidence/lockfile-result.json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def evidence(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "evidence" / "lockfile-result.json").read_text())


def test_a_declared_lockfile_in_a_subdirectory_is_checked(tmp_path):
    """Root-only detection reported a monorepo as having no Node project."""
    (tmp_path / "services" / "api").mkdir(parents=True)
    (tmp_path / "services" / "api" / "package.json").write_text("{}")

    result = run(tmp_path, "services/api/package-lock.json")
    assert result.returncode == CHECK_FAILURE
    check = evidence(tmp_path)["checks"][0]
    assert check["present"] is False
    assert check["detail"] == "declared lockfile is missing"


def test_a_present_lockfile_passes(tmp_path):
    (tmp_path / "go.sum").write_text("example.com/pkg v1.0.0 h1:abc=\n")
    result = run(tmp_path, "go.sum")
    assert result.returncode == PASS
    assert evidence(tmp_path)["status"] == "pass"


def test_an_empty_lockfile_fails(tmp_path):
    (tmp_path / "poetry.lock").write_text("")
    result = run(tmp_path, "poetry.lock")
    assert result.returncode == CHECK_FAILURE
    assert evidence(tmp_path)["checks"][0]["detail"] == "declared lockfile is empty"


def test_one_pinned_line_is_not_reproducibility(tmp_path):
    """`grep -q "=="` passed this file. Every requirement must be pinned."""
    (tmp_path / "requirements.txt").write_text(
        "# production dependencies\n"
        "requests==2.32.3\n"
        "urllib3>=2.0\n"
        "click\n"
    )
    result = run(tmp_path, "requirements.txt")
    assert result.returncode == CHECK_FAILURE
    check = evidence(tmp_path)["checks"][0]
    assert check["present"] is True
    assert check["pinned"] is False
    assert "2 requirement line(s)" in check["detail"]
    assert "urllib3>=2.0" in result.stderr


def test_a_fully_pinned_requirements_file_passes(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "-r base.txt\n"
        "requests==2.32.3 \\\n"
        "    --hash=sha256:0000\n"
        "pkg @ https://example.invalid/pkg-1.0.tar.gz\n"
        "\n"
        "# a comment\n"
    )
    assert run(tmp_path, "requirements.txt").returncode == PASS


def test_every_declared_lockfile_is_reported_not_just_the_first(tmp_path):
    (tmp_path / "go.sum").write_text("x\n")
    result = run(tmp_path, "go.sum,missing.lock")
    assert result.returncode == CHECK_FAILURE
    checks = evidence(tmp_path)["checks"]
    assert [check["path"] for check in checks] == ["go.sum", "missing.lock"]
    assert checks[0]["pinned"] is True


def test_an_absolute_path_is_refused(tmp_path):
    assert run(tmp_path, "/etc/passwd").returncode == USAGE_ERROR


def test_a_traversal_path_is_refused(tmp_path):
    assert run(tmp_path, "../other/go.sum").returncode == USAGE_ERROR


def test_declaring_nothing_is_a_usage_error(tmp_path):
    """The component validates a declared set; it does not guess one."""
    result = run(tmp_path, "")
    assert result.returncode == USAGE_ERROR
    assert "does not guess" in result.stderr


# ---------------------------------------------------------- the `none` sentinel
#
# A project with no third-party dependencies has no lockfile to declare, and the
# container compositions always instantiate this component, so before `none`
# existed such a project could not get a green pipeline at all: platform/demo-app is
# a stdlib-only Go module whose go.sum is empty. `none` says "there is nothing
# here"; an empty input still says "I did not fill this in", and those must stay
# different answers.


def test_declaring_none_passes_and_says_so(tmp_path):
    result = run(tmp_path, "none")
    assert result.returncode == PASS, result.stdout + result.stderr
    assert "declares no lockfile" in result.stdout

    record = evidence(tmp_path)
    assert record["declared"] == "none"
    assert record["checks"] == []
    assert record["status"] == "pass"


def test_declaring_a_real_set_is_recorded_as_a_set(tmp_path):
    (tmp_path / "go.sum").write_text("example.com/m v1.0.0 h1:abc=\n")
    result = run(tmp_path, "go.sum")
    assert result.returncode == PASS, result.stdout + result.stderr
    assert evidence(tmp_path)["declared"] == "set"


def test_none_does_not_look_for_a_file_called_none(tmp_path):
    """The sentinel is a declaration; it must not become a missing-path failure."""
    result = run(tmp_path, "none")
    assert result.returncode == PASS, result.stdout + result.stderr
    assert "none" not in [check["path"] for check in evidence(tmp_path)["checks"]]


def test_declaring_nothing_is_still_a_usage_error(tmp_path):
    """`none` must not have turned an unfilled input into a pass."""
    result = run(tmp_path, "")
    assert result.returncode == USAGE_ERROR, result.stdout + result.stderr
    assert "does not guess one" in result.stderr


def test_none_combined_with_a_path_is_ambiguous_and_refused(tmp_path):
    (tmp_path / "go.sum").write_text("example.com/m v1.0.0 h1:abc=\n")
    for declared in ("none,go.sum", "go.sum,none"):
        result = run(tmp_path, declared)
        assert result.returncode == USAGE_ERROR, declared + result.stdout + result.stderr
        assert "cannot be combined with a path" in result.stderr


def test_a_lockfile_named_none_in_a_directory_is_still_a_path(tmp_path):
    """`none` is the whole value or nothing: a path ending in it is a path."""
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "none").write_text("pinned\n")
    result = run(tmp_path, "vendor/none")
    assert result.returncode == PASS, result.stdout + result.stderr
    assert evidence(tmp_path)["declared"] == "set"
    assert evidence(tmp_path)["checks"][0]["path"] == "vendor/none"
