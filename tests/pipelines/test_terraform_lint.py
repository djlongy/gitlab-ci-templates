"""Merged-configuration lint for the terraform components, on the real server.

Section 14.1 requires the CI Lint API for merged configuration: a local YAML
parse cannot say whether a component compiles, which jobs it creates, or whether
a `needs:` target exists.

What this proves and what it does not: the configuration these components
produce compiles on gitlab.example.com 18.9.1-ee, and GitLab's own input handling
(defaults, regex validation, `$[[ inputs.* ]]` substitution) is exercised,
because tests/pipelines/compose_fixture.py supplies values through the spec
header rather than substituting them itself. It does not prove that
`include: local:` with `inputs:` resolves, because an include resolves against
the branch on the server and this work is not pushed. It is a lint. Nothing
here runs a pipeline, and no plan or apply is executed.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import compose_fixture as cf  # noqa: E402


def load_lint_module():
    spec = importlib.util.spec_from_file_location("ci_lint", HERE / "lint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_module = load_lint_module()

needs_token = pytest.mark.skipif(
    not os.environ.get("GITLAB_TOKEN"),
    reason="GITLAB_TOKEN is not set; see README.md, Running the tests",
)

# A rule that always matches, so a component whose own default deliberately
# refuses branch pipelines can still be compiled here.
ALWAYS = [{"when": "always"}]

# One minimal, valid set of inputs per component: only what has no default.
MINIMAL = {
    "terraform-fmt": ({"instance": "demo"}, "demo:terraform-fmt", "verify"),
    "terraform-validate": ({"instance": "demo"}, "demo:terraform-validate", "verify"),
    "terraform-plan": (
        {"instance": "demo", "environment": "mgt", "state-id": "demo-mgt"},
        "demo:terraform-plan",
        "plan",
    ),
    "terraform-module-publish": (
        {
            "instance": "hypervisor-vm",
            "module-name": "hypervisor-vm",
            "module-system": "hypervisor",
            "run-rules": ALWAYS,
        },
        "hypervisor-vm:terraform-module-publish",
        "publish",
    ),
}

STAGES = ["verify", "plan", "deploy", "publish"]


def lint(content):
    return lint_module.lint(content, include_jobs=True, dry_run=True)


def names(result):
    return lint_module.job_names(result)


@needs_token
@pytest.mark.parametrize("component", sorted(MINIMAL), ids=sorted(MINIMAL))
def test_single_instance_with_defaults_compiles(component):
    inputs, expected_job, expected_stage = MINIMAL[component]
    result = lint(cf.compose(STAGES, [(component, inputs)]))
    assert result["valid"], result.get("errors")
    assert names(result) == [expected_job]
    assert result["jobs"][0]["stage"] == expected_stage
    assert result["jobs"][0]["allow_failure"] is False


@needs_token
@pytest.mark.parametrize("component", sorted(MINIMAL), ids=sorted(MINIMAL))
def test_two_instances_emit_distinct_job_names(component):
    """Section 5.2: two instances of one component never emit the same job name."""
    inputs, _, _ = MINIMAL[component]
    alpha = dict(inputs, instance="alpha")
    beta = dict(inputs, instance="beta")
    result = lint(cf.compose(STAGES, [(component, alpha), (component, beta)]))
    assert result["valid"], result.get("errors")
    emitted = names(result)
    assert len(emitted) == 2
    assert len(set(emitted)) == 2
    assert all(job.startswith(("alpha:", "beta:")) for job in emitted)


@needs_token
def test_apply_waits_for_its_named_plan_producer():
    result = lint(
        cf.compose(
            STAGES,
            [
                (
                    "terraform-plan",
                    {"instance": "demo", "environment": "mgt", "state-id": "demo-mgt"},
                ),
                (
                    "terraform-apply",
                    {
                        "instance": "demo",
                        "environment": "mgt",
                        "state-id": "demo-mgt",
                        "plan-job": "demo:terraform-plan",
                    },
                ),
            ],
        )
    )
    assert result["valid"], result.get("errors")
    assert names(result) == ["demo:terraform-plan", "demo:terraform-apply"]

    apply_job = result["jobs"][1]
    assert apply_job["stage"] == "deploy"
    # The mutation is never unconditional and never optional.
    assert apply_job["when"] == "manual"
    assert apply_job["allow_failure"] is False
    # An environment record is what a protected-environment approver attaches
    # to; `when: manual` alone is pressable by any project Developer.
    assert apply_job["environment"] == "mgt"


@needs_token
def test_apply_without_its_producer_fails_lint():
    """Section 8 rule 10: validate the missing-producer case before releasing.

    The old terraform/plan-apply.yml took its plan from GitLab's implicit
    "download everything from earlier stages", so an absent plan job produced a
    pipeline that compiled and then applied whatever happened to be on disk.
    """
    result = lint(
        cf.compose(
            STAGES,
            [
                (
                    "terraform-apply",
                    {
                        "instance": "demo",
                        "environment": "mgt",
                        "state-id": "demo-mgt",
                        "plan-job": "demo:terraform-plan",
                    },
                )
            ],
        )
    )
    assert not result["valid"]
    assert any("undefined need" in message for message in result["errors"]), result["errors"]


@needs_token
def test_module_publish_does_not_run_on_a_branch_pipeline():
    """Its default rules require a protected tag, so a branch pipeline omits it.

    The sentinel keeps the pipeline non-empty, so an absent publish job is a
    rules decision rather than GitLab rejecting an empty pipeline.
    """
    content = cf.compose(
        STAGES,
        [
            (
                "terraform-module-publish",
                {
                    "instance": "hypervisor-vm",
                    "module-name": "hypervisor-vm",
                    "module-system": "hypervisor",
                },
            )
        ],
    )
    content += "\nsentinel:\n  stage: verify\n  script:\n    - echo sentinel\n"
    result = lint(content)
    assert result["valid"], result.get("errors")
    assert names(result) == ["sentinel"]


@needs_token
@pytest.mark.parametrize(
    ("component", "bad_instance"),
    [("terraform-fmt", "Bad_Instance"), ("terraform-apply", "x" * 60)],
)
def test_instance_regex_rejects_an_unusable_value(component, bad_instance):
    """Positive control for the regex on `instance`, enforced by GitLab itself."""
    inputs = {"instance": bad_instance}
    if component == "terraform-apply":
        inputs |= {
            "environment": "mgt",
            "state-id": "demo-mgt",
            "plan-job": "x:terraform-plan",
        }
    result = lint(cf.compose(STAGES, [(component, inputs)]))
    assert not result["valid"]
    assert any("RegEx" in message for message in result["errors"]), result["errors"]
