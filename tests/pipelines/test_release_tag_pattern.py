"""Which tags are release tags, and where that is decided.

GitLab protected-tag patterns are globs. `v*` and `*` are expressible; a version
number is not. With `build-sources: tags-only` the container compositions gate
the candidate chain on a protected tag, so a project that protects `*` -- which
is the easy thing to do -- builds, pushes, signs and promotes on every tag it
creates. platform/demo-app's legacy pipeline matched a semver regex; the composition
did not, and that is a widening nobody asked for.

`release-tag-pattern` closes it in the composition's `workflow:`, so a tag that
is not a release tag creates no pipeline at all rather than one whose jobs are
all gated off. One condition per composition, not one per job.

The rule is linted rather than reasoned about. The CI Lint API's `ref` decides
whether the dry run has branch or tag context, and the composition is rendered
locally first because the refs that make a good tag test are not the refs that
carry this branch's files.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import composition  # noqa: E402

REPO_ROOT = HERE.parent.parent
PIPELINES = REPO_ROOT / "pipelines"

# Every composition that gates a candidate chain on a tag.
COMPOSITIONS = ["container-buildkit", "container-ko", "container-jib"]

# A tag that exists on gitlab.example.com and matches the default pattern.
MATCHING_TAG = "1.0.0-rc.2"

BASE_INPUTS = {
    "container-buildkit": {
        "image-repository": "registry.example.com/dev/x/api",
        "semgrep-rules": "ci/rules.yml",
        "lockfiles": "go.sum",
    },
    "container-ko": {
        "image-repository": "registry.example.com/dev/x/api",
        "semgrep-rules": "ci/rules.yml",
        "lockfiles": "go.sum",
        "import-path": "./cmd/api",
    },
    "container-jib": {
        "image-repository": "registry.example.com/dev/x/api",
        "semgrep-rules": "ci/rules.yml",
        "lockfiles": "gradle.lockfile",
    },
}


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_module = load("ci_lint", "lint.py")

needs_token = pytest.mark.skipif(
    not os.environ.get("GITLAB_TOKEN"),
    reason="GITLAB_TOKEN is not set; see README.md, Running the tests",
)


def declared(name: str) -> dict:
    documents = list(yaml.safe_load_all((PIPELINES / f"{name}.yml").read_text()))
    return documents[0]["spec"]["inputs"]["release-tag-pattern"]


def render(name: str, **overrides) -> str:
    return composition.render(
        name, instance="api", **{**BASE_INPUTS[name], **overrides}
    )


def workflow_tag_rule(name: str, **overrides) -> str:
    config = composition.resolve(
        name, instance="api", **{**BASE_INPUTS[name], **overrides}
    )
    rules = [r["if"] for r in config["workflow"]["rules"] if "if" in r]
    return next(r for r in rules if "$CI_COMMIT_TAG" in r)


@pytest.mark.parametrize("name", COMPOSITIONS)
def test_the_input_is_declared_with_the_semver_default(name):
    spec = declared(name)
    assert spec["type"] == "string"
    assert spec["default"] == r"^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$"
    # The value lands inside `/.../`, so a slash would end the literal early.
    assert spec["regex"] == "^[^/]+$"


def test_all_three_compositions_ship_the_same_default():
    defaults = {name: declared(name)["default"] for name in COMPOSITIONS}
    assert len(set(defaults.values())) == 1, defaults


@pytest.mark.parametrize("name", COMPOSITIONS)
def test_the_pattern_reaches_the_workflow(name):
    assert "=~" in workflow_tag_rule(name)
    assert r"^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$" in workflow_tag_rule(name)
    supplied = workflow_tag_rule(name, **{"release-tag-pattern": "^v[0-9]+$"})
    assert "/^v[0-9]+$/" in supplied, supplied


@pytest.mark.parametrize("name", COMPOSITIONS)
def test_no_job_rule_repeats_the_tag_definition(name):
    """One condition. A second copy is a second thing to forget to change."""
    config = composition.resolve(name, instance="api", **BASE_INPUTS[name])
    for job_name, job in composition.jobs_only(config).items():
        for rule in job.get("rules", []):
            condition = rule.get("if", "")
            assert "=~" not in condition or "$CI_COMMIT_TAG" not in condition, (
                f"{name}: {job_name} defines a release tag of its own"
            )


@needs_token
@pytest.mark.parametrize("name", COMPOSITIONS)
def test_a_matching_tag_creates_the_candidate_chain(name):
    result = lint_module.lint(
        render(name, **{"build-sources": "tags-only"}), dry_run=True, ref=MATCHING_TAG
    )
    assert result["valid"], result.get("errors")
    emitted = lint_module.job_names(result)
    assert f"api:{name.replace('container-', 'container-build-')}" in emitted, emitted


@needs_token
@pytest.mark.parametrize("name", COMPOSITIONS)
def test_a_tag_the_pattern_rejects_creates_no_pipeline(name):
    """No non-semver tag exists on the server to lint against, so the pattern is
    narrowed instead: `^v[0-9]+$` cannot match 1.0.0-rc.2, which is the same
    question asked from the other side."""
    result = lint_module.lint(
        render(name, **{"build-sources": "tags-only", "release-tag-pattern": "^v[0-9]+$"}),
        dry_run=True,
        ref=MATCHING_TAG,
    )
    assert lint_module.job_names(result) == [], result.get("jobs")
    assert not result["valid"]
    # Not "the pipeline would have been empty": the workflow refused the ref, so
    # no pipeline is created at all. That is the difference the input buys.
    assert any(
        "workflow:rules" in message for message in result["errors"]
    ), result["errors"]


@needs_token
@pytest.mark.parametrize("name", COMPOSITIONS)
def test_a_merge_request_is_unaffected_by_the_pattern(name):
    """The positive control: the narrowed pattern must gate tags and nothing else."""
    narrow = render(name, **{"release-tag-pattern": "^v[0-9]+$"})
    result = lint_module.lint(narrow, dry_run=True, ref="main")
    assert result["valid"], result.get("errors")
    assert lint_module.job_names(result), "the default-branch path must still run"
