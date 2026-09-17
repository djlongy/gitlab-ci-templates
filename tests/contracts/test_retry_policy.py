"""Retry is for runner infrastructure, and for nothing else.

Reference-repo review P2: a component that dies because its runner did should
run again, and no other failure should. `retry:` is the one GitLab keyword that
can turn a failed gate into a passing pipeline without any of the shapes
section 10.1 already forbids: there is no `|| true` to grep for, no
`allow_failure: true`, no `exit 0`. A later hand widening `when:` to `always`
would retry a gitleaks hit until the runner happened to lose the finding.

So the condition is pinned here rather than left to review. Every component
declares the same typed `max-retries` input, every job carries the same
`retry:` block, and no configuration anywhere in the repository retries on
anything but `runner_system_failure`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"

# The only condition that is not a job outcome. `stuck_or_timeout_failure` is
# arguably also infrastructure, but a job that hangs and a job that is killed by
# its own timeout are indistinguishable from here, so it stays out.
ALLOWED_CONDITIONS = {"runner_system_failure"}

COMPONENTS = sorted(path.parent.name for path in TEMPLATES_DIR.glob("*/template.yml"))
# Every YAML that can carry a job: the components, the compositions and this
# repository's own pipeline.
CONFIGURATIONS = sorted(
    [*TEMPLATES_DIR.glob("*/template.yml"), *(REPO_ROOT / "pipelines").glob("*.yml")]
) + [REPO_ROOT / ".gitlab-ci.yml"]


def documents(path: Path) -> list:
    return [document for document in yaml.safe_load_all(path.read_text()) if document]


def jobs_document(component: str) -> dict:
    parsed = documents(TEMPLATES_DIR / component / "template.yml")
    assert len(parsed) == 2, "a component is exactly two documents: spec, then jobs"
    # `include:` is not a job. A component that chooses its ordering from its
    # inputs includes one of the files under templates/_needs/ to carry it.
    return {name: job for name, job in parsed[1].items() if name != "include"}


def inputs(component: str) -> dict:
    return documents(TEMPLATES_DIR / component / "template.yml")[0]["spec"]["inputs"]


def find_retries(node, trail=("",)):
    """Every `retry:` value in a parsed configuration, with the path to it."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "retry":
                yield "/".join(trail), value
            yield from find_retries(value, (*trail, str(key)))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from find_retries(item, (*trail, str(index)))


def test_there_are_components_to_check():
    """A glob that matched nothing would make every test below vacuous."""
    assert len(COMPONENTS) >= 36, COMPONENTS


@pytest.mark.parametrize("component", COMPONENTS)
def test_component_declares_the_max_retries_input(component):
    declaration = inputs(component).get("max-retries")
    assert declaration is not None, f"{component} declares no max-retries input"
    assert declaration["type"] == "number", declaration
    assert declaration["default"] == 1, declaration
    assert declaration["options"] == [0, 1, 2], declaration


@pytest.mark.parametrize("component", COMPONENTS)
def test_every_component_job_retries_infrastructure_only(component):
    for job_name, job in jobs_document(component).items():
        assert "retry" in job, f"{component}: job {job_name} declares no retry"
        retry = job["retry"]
        assert retry["max"] == "$[[ inputs.max-retries ]]", (
            f"{component}: retry max must come from the input, not a literal"
        )
        assert retry["when"] == ["runner_system_failure"], (
            f"{component}: retry when is {retry['when']!r}"
        )


@pytest.mark.parametrize(
    "path", CONFIGURATIONS, ids=lambda path: str(path.relative_to(REPO_ROOT))
)
def test_no_configuration_retries_on_any_other_condition(path):
    """The repository-wide sweep, so a new file cannot opt out of the rule."""
    for document in documents(path):
        for location, retry in find_retries(document):
            assert isinstance(retry, dict), (
                f"{path}: `retry: {retry!r}` at {location} is the shorthand form, "
                "which retries every failure"
            )
            conditions = retry.get("when")
            assert conditions is not None, (
                f"{path}: retry at {location} sets no `when`, so it retries "
                "every failure including a failed gate"
            )
            if isinstance(conditions, str):
                conditions = [conditions]
            assert set(conditions) <= ALLOWED_CONDITIONS, (
                f"{path}: retry at {location} retries on {sorted(conditions)}"
            )
