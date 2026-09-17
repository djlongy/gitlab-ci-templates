"""The three builders take their resource group from an input, like Terraform.

A resource group is project-scoped, so the name a build job carries serialises
that job across every pipeline in the project. `terraform-plan` and
`terraform-apply` have taken theirs from an input since 1.0.0; the builders had
none, so a consumer that needed one had to redefine a component job in its own
composition.

The empty default is the whole compatibility claim: an empty `resource_group`
key creates no resource group and the job runs unserialised. That was measured
rather than assumed, on a consumer pipeline where `empty-rg` and `set-rg` both
went green and the project's resource-groups API afterwards listed only the
non-empty key.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"
BUILDERS = [
    "container-build-buildkit",
    "container-build-jib",
    "container-build-ko",
]
# The pattern terraform-plan and terraform-apply accept their state-id under,
# plus the empty alternative that means "no group".
RESOURCE_GROUP_REGEX = "^$|^[a-z0-9][a-z0-9._-]{0,63}$"


def documents(component: str) -> tuple[dict, dict]:
    path = TEMPLATES_DIR / component / "template.yml"
    spec, jobs = list(yaml.safe_load_all(path.read_text()))
    return spec["spec"]["inputs"], jobs


def job_of(component: str) -> dict:
    _, jobs = documents(component)
    return jobs[f"$[[ inputs.instance ]]:{component}"]


@pytest.mark.parametrize("component", BUILDERS)
def test_the_builder_declares_the_input_with_an_empty_default(component: str):
    declared = documents(component)[0]["resource-group"]
    assert declared["type"] == "string"
    assert declared["default"] == ""
    assert declared["regex"] == RESOURCE_GROUP_REGEX


@pytest.mark.parametrize("component", BUILDERS)
def test_the_builder_job_carries_the_input(component: str):
    assert job_of(component)["resource_group"] == "$[[ inputs.resource-group ]]"


@pytest.mark.parametrize("component", BUILDERS)
def test_the_regex_refuses_a_value_that_is_not_a_resource_group_key(component: str):
    pattern = re.compile(documents(component)[0]["resource-group"]["regex"])
    assert pattern.match("")
    assert pattern.match("runner-images-factory")
    assert not pattern.match("Runner Images")
    assert not pattern.match("$CI_COMMIT_REF_SLUG")
    assert not pattern.match("-leading-dash")


def test_the_input_matches_the_pattern_it_says_it_copies():
    """terraform-plan is the component this input was modelled on, so a change
    to its state-id regex should surface here rather than let the two drift."""
    spec, _ = list(
        yaml.safe_load_all((TEMPLATES_DIR / "terraform-plan" / "template.yml").read_text())
    )
    state_id = spec["spec"]["inputs"]["state-id"]["regex"]
    assert RESOURCE_GROUP_REGEX == f"^$|{state_id}"
