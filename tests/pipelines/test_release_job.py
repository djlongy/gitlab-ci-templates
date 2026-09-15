"""The `release` job exists for a version tag and for nothing else.

Reference-repo review P3. Section 14.2 asks for the CI `release:` mechanism, and
the risk it brings is the opposite of the usual one: not that the job fails, but
that it runs somewhere it should not and publishes a release from a branch.

So the rule is linted for real against two contexts. The CI Lint API evaluates
`rules:` when `dry_run` is set, and `ref` decides whether that context is a
branch or a tag -- GitLab resolves the ref on the server, which is what sets
`CI_COMMIT_TAG`. Both refs below exist on gitlab.example.com.

The job is posted on its own rather than as part of `.gitlab-ci.yml`, because
that file's `include: local:` resolves against the ref being linted, and the
refs that make a good rule test are not the refs that carry `templates/`. The
job definition is read from the real file, so this cannot drift from it.

A lint is not an execution: nothing here publishes anything.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_FILE = REPO_ROOT / ".gitlab-ci.yml"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# Refs that exist on the server. The tag is protected by the `1.*` rule, which
# is what the job's second condition tests.
TAG_REF = "1.0.0-rc.2"
BRANCH_REF = "main"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parent / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_module = load("ci_lint", "lint.py")

needs_token = pytest.mark.skipif(
    not os.environ.get("GITLAB_TOKEN"),
    reason="GITLAB_TOKEN is not set; see README.md, Running the tests",
)


def ci_config() -> dict:
    return yaml.safe_load(CI_FILE.read_text())


def release_job() -> dict:
    job = ci_config().get("release")
    assert job is not None, ".gitlab-ci.yml declares no release job"
    return job


def release_only_config() -> str:
    """The real release job, alone, with the stage list it needs."""
    return yaml.safe_dump(
        {"stages": ["publish"], "release": release_job()}, sort_keys=False, width=10_000
    )


def extract_notes(tag: str) -> subprocess.CompletedProcess:
    """Run the job's own CHANGELOG extractor, read out of the job script."""
    script = "\n".join(release_job()["script"])
    return subprocess.run(
        ["sh", "-c", script],
        cwd=REPO_ROOT,
        env={**os.environ, "CI_COMMIT_TAG": tag},
        capture_output=True,
        text=True,
    )


@needs_token
def test_a_version_tag_creates_the_release_job():
    result = lint_module.lint(release_only_config(), dry_run=True, ref=TAG_REF)
    assert result["valid"], result.get("errors")
    assert [job["name"] for job in result["jobs"]] == ["release"], result["jobs"]


@needs_token
def test_a_branch_does_not():
    result = lint_module.lint(release_only_config(), dry_run=True, ref=BRANCH_REF)
    # An empty pipeline is not a valid one, which is the answer being asked for:
    # on a branch there is no release job and nothing else in this fragment.
    assert [job["name"] for job in result.get("jobs", [])] == [], result.get("jobs")
    assert not result["valid"], "a fragment whose only job is gated off is empty"
    assert any("empty" in message for message in result["errors"]), result["errors"]


def test_the_rule_is_one_condition_and_names_a_protected_tag():
    rules = release_job()["rules"]
    assert len(rules) == 1, rules
    condition = rules[0]["if"]
    assert "$CI_COMMIT_TAG =~ /" in condition
    assert '$CI_COMMIT_REF_PROTECTED == "true"' in condition
    assert "when" not in rules[0], "a release is never manual or on_failure here"


def test_the_image_is_pinned_by_digest():
    image = release_job()["image"]
    assert "@sha256:" in image, image
    assert ":latest" not in image and not image.endswith(":v0.24.0"), image


def test_the_release_description_is_the_changelog_section():
    release = release_job()["release"]
    assert release["tag_name"] == "$CI_COMMIT_TAG"
    assert release["description"] == "release-description.md"
    assert "CHANGELOG.md" in "\n".join(release_job()["script"])


def test_the_extractor_finds_a_real_changelog_section():
    result = extract_notes(TAG_REF)
    try:
        assert result.returncode == 0, result.stderr
        written = (REPO_ROOT / "release-description.md").read_text()
        assert written.strip(), "the extractor wrote an empty description"
        assert f"## {TAG_REF}" not in written, "the heading is not part of the notes"
        assert "corrections found by running rc.1" in written
    finally:
        (REPO_ROOT / "release-description.md").unlink(missing_ok=True)


def test_a_tag_with_no_changelog_section_fails_the_job():
    """The failure that matters: a release published with blank notes."""
    result = extract_notes("99.99.99")
    try:
        assert result.returncode != 0, result.stdout
        assert "no '## 99.99.99' section" in result.stderr, result.stderr
    finally:
        (REPO_ROOT / "release-description.md").unlink(missing_ok=True)


def test_every_changelog_heading_this_job_could_be_asked_for_has_a_section():
    """A released tag whose notes are empty would fail its own release job."""
    headings = [
        line[3:].split(" ")[0]
        for line in CHANGELOG.read_text().splitlines()
        if line.startswith("## ")
    ]
    versions = [h for h in headings if h[0].isdigit()]
    assert versions, headings
    for version in versions:
        result = extract_notes(version)
        assert result.returncode == 0, f"{version}: {result.stderr}"
    (REPO_ROOT / "release-description.md").unlink(missing_ok=True)


def test_the_release_tag_pattern_is_one_expression():
    """This repository's own release job and the compositions must agree.

    Two copies of "which tag is a release tag" is two things to change and one
    that gets forgotten. There is no YAML construct that shares a scalar across
    files, so the sharing is enforced here instead.
    """
    composition_defaults = {
        name: yaml.safe_load(
            (REPO_ROOT / "pipelines" / f"{name}.yml").read_text().split("\n---\n")[0]
        )["spec"]["inputs"]["release-tag-pattern"]["default"]
        for name in ("container-buildkit", "container-ko", "container-jib")
    }
    assert len(set(composition_defaults.values())) == 1, composition_defaults
    pattern = next(iter(composition_defaults.values()))

    condition = release_job()["rules"][0]["if"]
    assert f"$CI_COMMIT_TAG =~ /{pattern}/" in condition, condition
