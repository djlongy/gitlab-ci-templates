"""A step of the wiki sync that fails must fail the job.

wiki-deploy.sh sets `set -euo pipefail`, but `cycle` is called from an `if`, and
bash turns `set -e` off for the whole call tree of a command tested that way. A
function then returns the status of its LAST command, so a dead `python3 ...`
followed by `after=$(git rev-parse HEAD)` returned success and the cycle carried
on. platform/wiki-tpl-flat job 47477 (pipeline 5665) went green while its trace
showed wiki-pull.py raising FileNotFoundError for wiki-import.py; the wiki-to-repo
pull was skipped and the job reported "synced 3 pages ... wiki already current".

Each test runs wiki-deploy.sh from a COPY of runtime/wiki with one step replaced
by a stub that fails, which is the only way to fail a step without breaking the
thing under test. The first test is the positive control: the same copy, nothing
stubbed, must succeed. Without it, a copy that was broken for some unrelated
reason would make every assertion below pass for the wrong reason.

Run: python3 -m pytest -q tests/runtime/wiki/test_step_failures_propagate.py
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_wiki_pull import HERE, estate  # noqa: F401  (estate is a fixture)
from test_scenarios_races import DEPLOY_ENV_VARS

# ruff: noqa: F811

FAILING_STUB = "#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n"


@pytest.fixture()
def runtime_copy(tmp_path):
    """A working copy of runtime/wiki, so one file can be replaced by a stub."""
    destination = tmp_path / "runtime-wiki"
    shutil.copytree(HERE, destination, ignore=shutil.ignore_patterns("__pycache__"))
    return destination


def deploy(runtime_dir, repo, wiki_bare, **extra):
    env = {k: v for k, v in os.environ.items() if k not in DEPLOY_ENV_VARS}
    env.update(
        WIKI_URL=str(wiki_bare),
        REPO_PUSH_URL="origin",
        CI_PROJECT_PATH="group/docs-site",
        CI_SERVER_FQDN="example.com",
        CI_DEFAULT_BRANCH="main",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_SYSTEM="/dev/null",
        # One attempt: the retry loop exists for a wiki edited mid-cycle, and a
        # stub that always fails would only make the test three times slower.
        SYNC_ATTEMPTS="1",
    )
    env.update(extra)
    return subprocess.run(
        ["bash", str(runtime_dir / "wiki-deploy.sh"), "docs",
         str(Path(repo).parent / "wiki-stage")],
        cwd=repo, env=env, capture_output=True, text=True,
    )


def test_the_unmodified_copy_succeeds(runtime_copy, estate):
    """Positive control. Every assertion below is about a non-zero exit, which a
    copy broken for any other reason would also produce."""
    repo, _, _, wiki_bare = estate
    result = deploy(runtime_copy, repo, wiki_bare)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("step", ["wiki-pull.py", "wiki-sync.py"])
def test_a_failing_step_fails_the_run(runtime_copy, estate, step):
    """RC2-2. The pull is the step that failed in production; the sync sits in
    the same function shape and would have swallowed a failure the same way,
    then committed whatever half-written tree it left."""
    (runtime_copy / step).write_text(FAILING_STUB)
    repo, _, _, wiki_bare = estate
    result = deploy(runtime_copy, repo, wiki_bare)
    assert result.returncode != 0, (
        f"{step} exited 1 and wiki-deploy.sh reported success:\n"
        + result.stdout + result.stderr
    )
