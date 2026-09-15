"""CI Lint evidence for the ansible-lint, ansible-syntax and ansible-check components.

Section 14.1 of docs/gitlab-ci-agent-standard.md requires merged configuration to
be validated by the CI Lint API, because a local YAML parse cannot resolve an
include, apply an input regex, or tell you which jobs the result creates.

Why these fixtures post the template rather than a consumer shaped include: the
CI Lint API resolves `include: local:` against the project's default branch, not
against the branch under review. Posting

    include:
      - local: '/templates/ansible-lint/template.yml'

returns "Local file `templates/ansible-lint/template.yml` does not exist!" while
the component is still on a feature branch, which says nothing about the
component. GitLab does accept a `spec:` header on the posted root configuration,
so each test posts the template's own two documents with the chosen input values
written in as defaults. Everything the server owns is still exercised on the real
bytes: input types, regex validation, interpolation and the emitted job names.
A consumer shaped include fixture becomes possible once these files are on the
default branch.

A lint is not an execution. Nothing here runs a pipeline.

This suite needs network access to gitlab.example.com and a token with api scope.
It skips, rather than fails, when GITLAB_TOKEN is absent: a missing credential is
not evidence of a broken component.
"""

from __future__ import annotations

import copy
import importlib.util
import os
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "templates"

COMPONENTS = ("ansible-lint", "ansible-syntax", "ansible-check")

# Section 7.1. A composition owns this list; a component never declares it.
STAGE_VOCABULARY = (
    "verify",
    "build",
    "test",
    "scan",
    "plan",
    "attest",
    "publish",
    "deploy",
    "verify-deploy",
)

# Values that satisfy every required input, so a component can be linted with
# nothing but its own defaults filling in the rest.
REQUIRED_INPUTS = {
    "ansible-lint": {},
    "ansible-syntax": {"playbook": "playbooks/site.yml"},
    "ansible-check": {
        "playbook": "playbooks/site.yml",
        "inventory": "inventories/mgt/hosts.yml",
        # `dry_run` simulates a default branch push, and ansible-check
        # deliberately does not run on one: its default rules admit only web and
        # schedule pipelines. A composition that wants it in a branch pipeline
        # says so, which is what this stands in for. The default is asserted
        # separately by test_ansible_check_defaults_to_no_job_on_a_push.
        "run-rules": [{"if": "$CI_COMMIT_BRANCH"}],
    },
}


def load_lint_module():
    spec = importlib.util.spec_from_file_location(
        "ci_lint", Path(__file__).parent / "lint.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_module = load_lint_module()

needs_token = pytest.mark.skipif(
    not os.environ.get("GITLAB_TOKEN"),
    reason="GITLAB_TOKEN is not set; see README.md, Running the tests",
)


def render(component: str, **inputs) -> str:
    """Return lintable configuration for one component with `inputs` applied.

    The template is read as it ships. Supplied values are written into the spec
    header as defaults, which is how a root configuration supplies inputs to
    itself, and is also how the server gets the chance to reject a value that
    violates the input's regex.
    """
    documents = list(
        yaml.safe_load_all((TEMPLATES / component / "template.yml").read_text())
    )
    spec_document, jobs_document = copy.deepcopy(documents[0]), documents[1]
    declared = spec_document["spec"]["inputs"]
    for name, value in inputs.items():
        assert name in declared, f"{component} has no input named {name}"
        declared[name]["default"] = value
    # A component never declares `stages:` (section 6.1); the owning composition
    # does. The posted root configuration has to play that part, so the standard
    # vocabulary from section 7.1 is added here and nowhere in the template.
    jobs_document = {"stages": list(STAGE_VOCABULARY), **jobs_document}
    return (
        yaml.safe_dump(spec_document, sort_keys=False)
        + "---\n"
        + yaml.safe_dump(jobs_document, sort_keys=False)
    )


def lint_component(component: str, instance: str = "demo", **inputs) -> dict:
    values = {"instance": instance, **REQUIRED_INPUTS[component], **inputs}
    return lint_module.lint(
        render(component, **values), include_jobs=True, dry_run=True
    )


def test_every_component_parses_as_two_documents():
    """Runs without a token. The spec header and the jobs must be separate documents."""
    for component in COMPONENTS:
        documents = list(
            yaml.safe_load_all((TEMPLATES / component / "template.yml").read_text())
        )
        assert len(documents) == 2, f"{component}: expected a spec header and jobs"
        assert set(documents[0]) == {"spec"}, f"{component}: doc 1 is not just spec"
        assert "inputs" in documents[0]["spec"]


@needs_token
@pytest.mark.parametrize("component", COMPONENTS)
def test_defaults_lint_and_emit_the_expected_job(component):
    """(a) One instance with defaults compiles and creates exactly the named job."""
    result = lint_component(component)
    assert result["valid"], result.get("errors")
    assert lint_module.job_names(result) == [f"demo:{component}"]


@needs_token
@pytest.mark.parametrize("component", COMPONENTS)
def test_two_instances_emit_distinct_job_names(component):
    """(b) Section 5.2: two instances of one component never collide."""
    first = lint_module.job_names(lint_component(component, instance="mgt-fleet"))
    second = lint_module.job_names(lint_component(component, instance="prod-fleet"))
    assert first == [f"mgt-fleet:{component}"]
    assert second == [f"prod-fleet:{component}"]
    assert set(first).isdisjoint(second)


@needs_token
@pytest.mark.parametrize("component", COMPONENTS)
def test_the_job_is_blocking_and_downloads_nothing(component):
    """Section 10.1 and section 8.6, read back from the server's merged view.

    `allow_failure` is checked here rather than by grepping the file because a
    consumer or a later edit could introduce it anywhere in the merge; what
    matters is the value GitLab ends up with.
    """
    result = lint_component(component)
    job = result["jobs"][0]
    assert job["allow_failure"] is False
    assert job["name"] == f"demo:{component}"


@needs_token
@pytest.mark.parametrize("component", COMPONENTS)
def test_a_tag_pinned_execution_image_is_rejected(component):
    """Section 1: a tag is not a digest, and the input regex is what enforces it."""
    result = lint_component(
        component, **{"execution-image": "registry.example.com/shared/base/ansible-ee:latest"}
    )
    assert not result["valid"]
    assert any("execution-image" in message for message in result["errors"])


@needs_token
@pytest.mark.parametrize("component", ("ansible-syntax", "ansible-check"))
def test_a_shell_metacharacter_in_playbook_is_rejected(component):
    """Section 6.1: inputs are not a shell safety mechanism, so constrain them.

    This is the server refusing the value at pipeline creation, before any shell
    sees it. Without the regex the value would reach the job as a CI variable.
    """
    result = lint_component(component, playbook="site.yml; rm -rf /")
    assert not result["valid"]
    assert any("playbook" in message for message in result["errors"])


@needs_token
@pytest.mark.parametrize("component", COMPONENTS)
def test_an_instance_outside_the_naming_grammar_is_rejected(component):
    """Section 5.2: instance matches ^[a-z][a-z0-9-]{0,47}$ and nothing else."""
    result = lint_component(component, instance="Prod_Fleet")
    assert not result["valid"]
    assert any("instance" in message for message in result["errors"])


@needs_token
def test_ansible_check_defaults_to_no_job_on_a_push():
    """The component that reaches managed hosts stays off ordinary pipelines.

    `dry_run` simulates a default branch push. With its shipped rules,
    ansible-check creates nothing there, so a consumer who includes it without
    thinking does not start SSH sessions to the fleet on every merge. The
    composition has to ask for it.
    """
    documents = list(
        yaml.safe_load_all((TEMPLATES / "ansible-check" / "template.yml").read_text())
    )
    shipped_rules = documents[0]["spec"]["inputs"]["run-rules"]["default"]
    assert shipped_rules == [
        {"if": '$CI_PIPELINE_SOURCE == "web"'},
        {"if": '$CI_PIPELINE_SOURCE == "schedule"'},
    ]
    result = lint_component(
        "ansible-check", **{"run-rules": shipped_rules}
    )
    assert result["errors"] == [
        "The resulting pipeline would have been empty. "
        "Review the rules configuration for the relevant jobs."
    ]


@needs_token
def test_a_vault_password_value_cannot_be_passed_as_the_variable_name():
    """vault-password-secret takes a NAME. The regex refuses anything else.

    A password with lowercase letters, punctuation or spaces fails the pattern,
    so a consumer that pastes the value instead of the variable name gets a
    pipeline that will not create rather than a password in the job log.
    """
    result = lint_component("ansible-syntax", **{"vault-password-secret": "hunter2-not-a-name"})
    assert not result["valid"]
    assert any("vault-password-secret" in message for message in result["errors"])


# --------------------------------------------------------------------------
# ansible-lint policy-mode
# --------------------------------------------------------------------------

def lint_inputs(component: str) -> dict:
    return list(
        yaml.safe_load_all((TEMPLATES / component / "template.yml").read_text())
    )[0]["spec"]["inputs"]


def test_ansible_lint_is_blocking_unless_a_composition_says_otherwise():
    """Runs without a token. The relaxed mode is never the default."""
    policy = lint_inputs("ansible-lint")["policy-mode"]
    assert policy["default"] == "blocking"
    assert policy["options"] == ["blocking", "advisory"]


def test_advisory_mode_is_a_wrapper_decision_not_a_suppressed_failure():
    """Section 10.1: no `|| true`, no `allow_failure: true`, no bare `exit 0`.

    Read off the parsed job so a comment quoting the old behaviour cannot make
    this pass or fail. The gate is scan-gate.sh classifying the exit code, and
    the evidence record is written either way.
    """
    documents = list(
        yaml.safe_load_all((TEMPLATES / "ansible-lint" / "template.yml").read_text())
    )
    definition = next(iter(documents[1].values()))
    script = "\n".join(definition["before_script"] + definition["script"])
    # The embedded wrapper quotes `|| true` in its own comments, explaining why
    # it exists; the check is about shell that runs, so comments come out first.
    executable = "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )
    assert definition["allow_failure"] is False
    assert "|| true" not in executable
    assert "scan-gate.sh" in script
    assert "scan-result.json" in script
    assert definition["artifacts"]["when"] == "always"


@needs_token
def test_a_policy_mode_outside_the_options_is_rejected():
    """The options list is the server's to enforce, not a runtime `case`."""
    result = lint_component("ansible-lint", **{"policy-mode": "warn"})
    assert not result["valid"]
    assert any("policy-mode" in message for message in result["errors"])


@needs_token
def test_an_advisory_ansible_lint_job_is_still_a_blocking_job():
    """Advisory is about findings. The job itself never becomes optional."""
    result = lint_component("ansible-lint", **{"policy-mode": "advisory"})
    assert result["valid"], result.get("errors")
    assert lint_module.job_names(result) == ["demo:ansible-lint"]
    assert result["jobs"][0]["allow_failure"] is False


def test_the_gate_wrapper_is_not_written_into_the_tree_being_linted():
    """Measured, not styled: a file in the checkout hides the empty-tree failure.

    In the pinned image on 2026/09/15, `ansible-lint --offline --nocolor` exits
    5 on a directory with no Ansible content and 0 as soon as one unrelated file
    is present ("0 files processed of 1 encountered"). Writing the embedded
    wrapper under $CI_PROJECT_DIR puts exactly such a file there, so a
    working-directory pointing at the wrong place would pass instead of failing
    as an execution error. An empty artifact directory does not have that
    effect, so only the runtime lives outside the checkout.
    """
    documents = list(
        yaml.safe_load_all((TEMPLATES / "ansible-lint" / "template.yml").read_text())
    )
    (definition,) = documents[1].values()
    assert definition["variables"]["CI_TPL_RUNTIME_DIR"] == "/tmp/ci-tpl"
    before = "\n".join(definition["before_script"])
    assert "# BEGIN embed dir $CI_TPL_RUNTIME_DIR runtime/scan/scan-gate.sh" in before
    assert "$CI_PROJECT_DIR/.ci-tpl" not in "\n".join(
        definition["before_script"] + definition["script"]
    )
