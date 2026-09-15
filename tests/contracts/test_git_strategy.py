"""A job that reads only artifacts does not clone the consumer's repository.

Reference-repo review P9. Seven components take everything they act on from a
producer's artifacts or from an API, and a clone of the consumer repository is
pure cost to them: on a large repository it is most of the job's wall time, and
it puts source on a runner that has no reason to hold it.

The pairing is what this file protects. `GIT_STRATEGY: none` is safe only while
the job reads nothing from the checkout, and the failure it causes later is a
confusing one: a missing file in a job that used to work. So the two halves are
asserted together, and a job that gains a `working-directory` input or opens a
path under the checkout fails here rather than in a consumer's pipeline.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"

# Every component whose job is artifact-only or API-only.
NO_CHECKOUT = [
    "container-promote-harbor",
    "release-trigger-jenkins",
    "release-trigger-semaphore",
    "security-repository-audit",
    "security-sbom-upload-dtrack",
    "security-sync-vigil",
    "security-verify-vigil",
]

# `$CI_PROJECT_DIR` alone is the artifact root and is present without a clone.
# A path under it is what would need one.
CHECKOUT_PATH = re.compile(r"\$CI_PROJECT_DIR/(?!\.ci-artifacts)")


def parts(component: str) -> tuple[dict, dict]:
    parsed = list(yaml.safe_load_all((TEMPLATES_DIR / component / "template.yml").read_text()))
    assert len(parsed) == 2, "a component is exactly two documents: spec, then jobs"
    jobs = parsed[1]
    assert len(jobs) == 1, f"{component} emits {len(jobs)} jobs"
    return parsed[0]["spec"]["inputs"], next(iter(jobs.values()))


def shell(job: dict) -> str:
    return "\n".join(
        line
        for key in ("before_script", "script", "after_script")
        for line in (job.get(key) or [])
    )


@pytest.mark.parametrize("component", NO_CHECKOUT)
def test_the_job_skips_the_clone(component):
    _, job = parts(component)
    assert (job.get("variables") or {}).get("GIT_STRATEGY") == "none", (
        f"{component} reads no checkout, so it must not clone one"
    )


@pytest.mark.parametrize("component", NO_CHECKOUT)
def test_the_job_reads_nothing_from_the_checkout(component):
    """The other half: GIT_STRATEGY none is only correct while this holds."""
    declared, job = parts(component)
    assert "working-directory" not in declared, (
        f"{component} declares working-directory, so it does read the checkout; "
        "remove GIT_STRATEGY: none or remove the input"
    )
    body = shell(job) + "\n" + yaml.safe_dump(job.get("variables") or {})
    offenders = sorted(set(CHECKOUT_PATH.findall(body)))
    assert not offenders, f"{component} opens a checkout path: {offenders}"


def test_a_component_that_clones_is_not_in_the_list():
    """Guards the list itself: a name that no longer belongs would pass silently."""
    for component in sorted(path.parent.name for path in TEMPLATES_DIR.glob("*/template.yml")):
        _, job = parts(component)
        skips = (job.get("variables") or {}).get("GIT_STRATEGY") == "none"
        assert skips == (component in NO_CHECKOUT), (
            f"{component}: GIT_STRATEGY none is {skips}, membership of the "
            "no-checkout list is not"
        )
