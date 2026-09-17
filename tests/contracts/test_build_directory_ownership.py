"""A component job owns its build directory, whatever uid its image runs as.

Measured on a documentation consumer, across four jobs in two main pipelines:
`quality-sonarqube` died at
`java.nio.file.AccessDeniedException: /builds/<project>/.git/objects/4c`
whenever it landed in the same runner concurrency slot immediately after
`docs-wiki-sync`, which runs as root in the same build directory and writes git
objects there. The same configuration is green when the two jobs land in
different slots, which is why the release candidate looked fine. Earlier in the
same program the same class broke `quality-sonarqube` in another consumer,
through a root-restored cache rather than a root sibling job.

The class is one thing: a component job whose image is non-root cannot depend on
the ownership of a build directory that the helper, a cache restore or a
previous job wrote as root. `FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR` is the
runner's documented answer -- it discovers the image's uid and gid and chowns
the build directory to them, rather than relying on `umask 0000` making what the
helper wrote group-writable.

The rule here is unconditional: every component job sets it, not only the two
whose default image is non-root today. Two reasons, both about what can be
checked rather than about taste.

1. `execution-image` is a consumer input on all 36 components. A component's
   default image being root says nothing about the image the job will actually
   run in, so a rule keyed on the default leaves the class open for every
   consumer override.
2. A conditional rule is not statically testable. Deciding whether a component
   needs the flag means resolving its image against the registry, which this
   suite cannot do offline. A rule that cannot be enforced by a test is a rule
   that lasts until the next component.

The flag costs nothing on a root image: the runner chowns the directory to 0:0,
which is what it already was. Its one precondition is that the image carries the
POSIX `id` utility, which the runner calls with `-u` and `-g`. Every default
image in `templates/` was measured on 2026/09/16 and all 18 resolve `id`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"

FLAG = "FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR"

COMPONENTS = sorted(path.parent.name for path in TEMPLATES_DIR.glob("*/template.yml"))


def jobs(component: str) -> dict:
    documents = list(
        yaml.safe_load_all((TEMPLATES_DIR / component / "template.yml").read_text())
    )
    assert len(documents) == 2, "a component is exactly two documents: spec, then jobs"
    # `include:` is not a job. A component that chooses its ordering from its
    # inputs includes one of the files under templates/_needs/ to carry it.
    return {name: job for name, job in documents[1].items() if name != "include"}


def test_there_are_components_to_check():
    assert len(COMPONENTS) >= 36, COMPONENTS


@pytest.mark.parametrize("component", COMPONENTS)
def test_every_job_takes_ownership_of_its_build_directory(component):
    """Hidden jobs included: a hidden parent is where the omission would hide."""
    for name, job in jobs(component).items():
        variables = job.get("variables", {})
        assert FLAG in variables, (
            f"{component}: job {name} does not set {FLAG}, so a non-root "
            "execution image inherits whatever owns the build directory"
        )


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_flag_is_the_quoted_string_true(component):
    """An unquoted `true` is a YAML boolean, and GitLab rejects it in variables."""
    for name, job in jobs(component).items():
        value = job["variables"][FLAG]
        assert isinstance(value, str), f"{component}: job {name} sets {FLAG} to a {type(value).__name__}"
        assert value == "true", f"{component}: job {name} sets {FLAG} to {value!r}"


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_flag_is_not_an_input(component):
    """A consumer cannot turn it off: the ownership is the component's, not theirs."""
    for name, job in jobs(component).items():
        assert "$[[" not in job["variables"][FLAG], (
            f"{component}: job {name} interpolates an input into {FLAG}"
        )
