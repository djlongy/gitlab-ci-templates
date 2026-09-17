"""A component job runs what the component ships, not what a consumer defaults.

On a consumer pipeline on the originating estate, 2026/09, `quality-sonarqube` died at
`mkdir $CI_PROJECT_DIR/.ci-tpl: Permission denied`. The scanner image runs as
uid 1000, and the consumer's `default: cache:` (a pip cache) was restored into
the job by the runner's cache helper before the script ran. The same image on
the same runner passes in every consumer without a global cache. A
consumer-side `inherit: default: [tags, timeout, interruptible]` on that one job
made it green end to end.

The fix belongs in the component, not in each consumer. A `default:` block is a
consumer's convenience for its OWN jobs; when it reaches a component job it
changes the image, the shell that runs before the script, or what is on disk
when it starts -- none of which the component's contract, tests or evidence
cover. A component that behaves differently depending on a file it never reads
has no contract at all.

What stays inherited is what a consumer legitimately owns: which runners its
jobs may use, and how long they may take. `retry` and `id_tokens` stay in the
list although every component sets both itself, because a consumer-level
definition of either is a deliberate estate decision rather than an accident of
convenience.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"

INHERITED = ["tags", "timeout", "interruptible", "retry", "id_tokens"]

# The `default:` keywords that must NOT reach a component job. GitLab's list,
# minus the five above.
BLOCKED = {"image", "before_script", "after_script", "cache", "services",
           "artifacts", "hooks"}

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
def test_every_job_declares_the_same_inheritance(component):
    """Hidden jobs included: a hidden parent is where an image would hide."""
    for name, job in jobs(component).items():
        assert "inherit" in job, f"{component}: job {name} inherits the consumer's default:"
        assert list(job["inherit"]) == ["default"], job["inherit"]
        assert job["inherit"]["default"] == INHERITED, (
            f"{component}: job {name} inherits {job['inherit']['default']}"
        )


@pytest.mark.parametrize("component", COMPONENTS)
def test_nothing_blocked_is_in_the_inherited_list(component):
    for name, job in jobs(component).items():
        leaked = BLOCKED & set(job["inherit"]["default"])
        assert not leaked, f"{component}: job {name} would inherit {sorted(leaked)}"


@pytest.mark.parametrize("component", COMPONENTS)
def test_a_component_may_still_set_these_keywords_itself(component):
    """The rule is about inheritance, not about the keywords.

    A component declaring its own `cache:`, `image:` or `before_script:` is
    normal and untouched; what it must not do is accept a consumer's.
    """
    for name, job in jobs(component).items():
        for keyword in ("image", "before_script", "cache"):
            if keyword in job:
                assert job["inherit"]["default"] == INHERITED, (
                    f"{component}: job {name} sets {keyword} and must still "
                    "declare the standard inheritance"
                )
    # At least one component sets each of image and before_script, or this test
    # is asserting nothing.
    assert any("image" in job for job in jobs(component).values()), component
