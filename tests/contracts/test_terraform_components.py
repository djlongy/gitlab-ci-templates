"""Structural rules the terraform components must keep, checked on the files.

These are the defects the audit found in `terraform/*.yml`, turned into checks
that fail if they come back. They are deliberately textual or structural: the
CI Lint suite in tests/pipelines/ proves the configuration compiles, and this
suite proves it is the shape the standard requires, which a lint cannot see.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"

COMPONENTS = [
    "terraform-fmt",
    "terraform-validate",
    "terraform-plan",
    "terraform-apply",
    "terraform-module-publish",
]

# Section 6.1: an atomic component sets none of these at the top level.
FORBIDDEN_TOP_LEVEL = {
    "stages",
    "workflow",
    "default",
    "variables",
    "image",
    "cache",
    "before_script",
}

DIGEST_PINNED = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
JOB_NAME = re.compile(r"^\$\[\[ inputs\.instance \]\]:([a-z][a-z0-9-]*)(:[a-z][a-z0-9-]*)?$")
# `terraform init`, optionally as the body of a case arm. A comment that merely
# mentions init is not an invocation.
INIT_INVOCATION = re.compile(r"^(?:[a-z][a-z0-9-]*\)\s*)?terraform init\b")


def template_path(component: str) -> Path:
    return TEMPLATES_DIR / component / "template.yml"


def documents(component: str) -> tuple[dict, dict]:
    parsed = list(yaml.safe_load_all(template_path(component).read_text()))
    assert len(parsed) == 2, f"{component}: a component template is exactly two documents"
    return parsed[0], parsed[1]


def inputs_of(component: str) -> dict:
    header, _ = documents(component)
    return header["spec"]["inputs"]


def jobs_of(component: str) -> dict:
    _, body = documents(component)
    return body


EMBED_REGION = re.compile(
    r"^(?P<indent>[ ]*)# BEGIN embed [^\n]+\n.*?^(?P=indent)# END embed\n",
    re.DOTALL | re.MULTILINE,
)


def without_embedded_runtime(text: str) -> str:
    """The component's own shell, with the runtime it carries cut out.

    runtime/terraform/tfguard.sh has its own tests in tests/runtime/terraform/
    and its own drift gate; its `grep ... || true` and its comments are not
    statements about how this component handles a failing command.
    """
    return EMBED_REGION.sub("", text)


def script_text(job: dict) -> str:
    lines: list[str] = []
    for key in ("before_script", "script", "after_script"):
        lines.extend(job.get(key) or [])
    return without_embedded_runtime("\n".join(lines) + "\n")


@pytest.mark.parametrize("component", COMPONENTS)
def test_body_sets_no_top_level_pipeline_configuration(component):
    body = jobs_of(component)
    assert not FORBIDDEN_TOP_LEVEL & set(body), (
        f"{component} sets top-level configuration that belongs to a composition: "
        f"{sorted(FORBIDDEN_TOP_LEVEL & set(body))}"
    )


@pytest.mark.parametrize("component", COMPONENTS)
def test_every_job_name_carries_the_instance(component):
    """Section 5.2: two instances must never collide, and no bare legacy name.

    The old files published `terraform-lint` and a set of hidden `.terraform-*`
    parents, so a second root in one project could not have its own jobs.
    """
    for name in jobs_of(component):
        match = JOB_NAME.match(name)
        assert match, f"{component}: job name {name!r} is not $[[ inputs.instance ]]:<component>"
        assert match.group(1) == component, (
            f"{component}: job {name!r} does not name its own component"
        )


@pytest.mark.parametrize("component", COMPONENTS)
def test_required_inputs_are_declared(component):
    declared = inputs_of(component)
    for required in ("instance", "stage", "execution-image", "runner-tags", "run-rules"):
        assert required in declared, f"{component} declares no {required} input"

    assert declared["instance"]["regex"] == "^[a-z][a-z0-9-]{0,47}$"
    assert declared["runner-tags"]["type"] == "array"
    assert declared["run-rules"]["type"] == "array"

    image = declared["execution-image"]
    assert image["regex"] == (
        r"^(\$[A-Z][A-Z0-9_]*/)?[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[0-9a-f]{64}$"
    )
    assert DIGEST_PINNED.match(image["default"]), (
        f"{component}: the execution-image default is not digest-pinned. "
        "Section 1: a tag is not a digest."
    )


@pytest.mark.parametrize("component", COMPONENTS)
def test_every_interpolated_input_is_declared(component):
    declared = set(inputs_of(component))
    referenced = set(
        re.findall(r"\$\[\[\s*inputs\.([a-z0-9-]+)[^\]]*\]\]", template_path(component).read_text())
    )
    assert referenced <= declared, (
        f"{component} interpolates undeclared inputs: {sorted(referenced - declared)}"
    )


@pytest.mark.parametrize("component", COMPONENTS)
def test_no_input_looks_like_a_secret(component):
    """Section 6.1: no token, password or private key in an input."""
    for name in inputs_of(component):
        assert not re.search(r"(password|token|secret|key)$", name) or name.endswith(
            "-variable"
        ), f"{component}: input {name!r} looks like it would carry a secret value"


@pytest.mark.parametrize("component", COMPONENTS)
def test_no_swallowed_failures(component):
    """Section 10.1: no `|| true`, no allow_failure, no unconditional exit 0."""
    text = without_embedded_runtime(template_path(component).read_text())
    assert "|| true" not in text, f"{component} swallows a command's exit code"
    assert "eval" not in text, f"{component} uses eval to build a command"
    for name, job in jobs_of(component).items():
        assert job.get("allow_failure") is False, f"{name} does not set allow_failure: false"
        assert "exit 0" not in script_text(job), f"{name} contains an unconditional exit 0"


@pytest.mark.parametrize("component", COMPONENTS)
def test_needs_and_dependencies_are_never_combined(component):
    """Section 8 rules 5, 6 and 7."""
    for name, job in jobs_of(component).items():
        has_needs = "needs" in job
        has_dependencies = "dependencies" in job
        assert has_needs or has_dependencies, (
            f"{name} declares neither needs nor dependencies; a source-only job "
            "uses dependencies: [] so it does not download unrelated artifacts"
        )
        assert not (has_needs and has_dependencies), f"{name} combines needs and dependencies"
        if has_needs:
            assert job["needs"] != [], f"{name} uses needs: [], which bypasses the stage barrier"


@pytest.mark.parametrize("component", COMPONENTS)
def test_working_directory_is_validated_against_checkout_escape(component):
    """Section 5.3. `cd ${DEPLOY_DIR}` with DEPLOY_DIR unset ran in the checkout root."""
    if "working-directory" not in inputs_of(component):
        pytest.skip(f"{component} has no working-directory input")
    for name, job in jobs_of(component).items():
        text = script_text(job)
        assert "escapes the checkout" in text, (
            f"{name} changes directory without proving the result is inside the checkout"
        )


def init_lines(component: str) -> list[str]:
    """Every line of the component's shell that invokes `terraform init`.

    terraform-validate runs its init inside a case arm, so the invocation is not
    at the start of the line.
    """
    job = next(iter(jobs_of(component).values()))
    lines = [
        line
        for line in script_text(job).splitlines()
        if INIT_INVOCATION.match(line.strip())
    ]
    assert lines, f"{component} never initialises"
    return lines


@pytest.mark.parametrize(
    "component", ["terraform-validate", "terraform-plan", "terraform-apply"]
)
def test_init_never_prompts(component):
    for line in init_lines(component):
        assert "-input=false" in line, f"{component}: init may prompt"


@pytest.mark.parametrize("component", ["terraform-plan", "terraform-apply"])
def test_init_honours_the_committed_provider_lockfile(component):
    """Section 11.2 item 2. The old `terraform init -backend=false` had neither
    flag, so a CI run could rewrite the lockfile it was supposed to honour.

    A plan and an apply act on a root module, which always commits a lockfile.
    They have no relaxed mode and must not grow one."""
    for line in init_lines(component):
        assert "-lockfile=readonly" in line, f"{component}: init may rewrite the lockfile"


def test_validate_relaxes_the_lockfile_only_for_a_module_repository():
    """Section 11.2 item 2 still binds by default. `lockfile-mode: module` is the
    one documented relaxation, for a repository that commits no lockfile because
    it ships reusable modules. tests/runtime/terraform/test_terraform_validate_job_shell.py
    proves which branch each value takes."""
    declaration = inputs_of("terraform-validate")["lockfile-mode"]
    assert declaration["default"] == "readonly", (
        "the safe mode must be the one a consumer gets without asking"
    )
    assert sorted(declaration["options"]) == ["module", "readonly"], (
        "an unconstrained string here would let any value through the spec header"
    )
    lines = init_lines("terraform-validate")
    readonly = [line for line in lines if line.strip().startswith("readonly)")]
    module = [line for line in lines if line.strip().startswith("module)")]
    assert len(readonly) == 1 and len(module) == 1, (
        f"expected one init per mode, saw {lines}"
    )
    assert "-lockfile=readonly" in readonly[0]
    assert "-lockfile=readonly" not in module[0]
    for line in lines:
        assert "-upgrade" not in line, (
            "relaxing the lockfile check must not also move provider versions"
        )
    assert "unknown lockfile-mode" in script_text(
        next(iter(jobs_of("terraform-validate").values()))
    ), "an unrecognised mode must fail rather than fall through to a default"


@pytest.mark.parametrize("component", COMPONENTS)
def test_artifacts_stay_under_the_component_root(component):
    """Section 9.4: paths under .ci-artifacts, names carrying the identifiers."""
    for name, job in jobs_of(component).items():
        artifacts = job.get("artifacts")
        if not artifacts:
            continue
        for path in artifacts["paths"]:
            assert path.startswith(f".ci-artifacts/$[[ inputs.instance ]]/{component}/"), (
                f"{name} publishes {path!r} outside its own artifact root"
            )
        assert "$[[ inputs.instance ]]" in artifacts["name"]
        assert component in artifacts["name"]


def test_plan_publishes_the_full_evidence_set():
    """Section 9.3: the plan is not deployable without its metadata."""
    job = jobs_of("terraform-plan")["$[[ inputs.instance ]]:terraform-plan"]
    text = script_text(job)
    assert "-out=" in text
    assert "plan-metadata" in text
    assert "--max-age-seconds" in text
    assert job["artifacts"]["access"] == "developer", (
        "a saved plan discloses every planned value"
    )
    # Section 9.3's recommended retention, not the old one hour.
    assert job["artifacts"]["expire_in"] == "$[[ inputs.plan-expire-in ]]"
    assert inputs_of("terraform-plan")["plan-expire-in"]["default"] == "7 days"


def test_plan_is_stage_scheduled_so_apply_inherits_the_earlier_gates():
    """The invariant terraform-apply's dependency chain rests on.

    Apply names only its plan producer in `needs`. That is enough to satisfy
    section 8 rule 8 only while the plan job is itself scheduled by the stage
    barrier: waiting for it then transitively waits for every earlier stage. A
    `needs` added here would silently let apply start before the verify gates.
    """
    job = jobs_of("terraform-plan")["$[[ inputs.instance ]]:terraform-plan"]
    assert "needs" not in job
    assert job["dependencies"] == []


def test_apply_names_exactly_one_required_producer():
    """Section 8 rules 1 and 3, and section 11.2's `plan-job` requirement.

    The gate list follows the producer and cannot displace it: it is a second
    entry, spliced in, and `gate-jobs` defaults to empty so an apply that needs
    no cross-instance ordering has exactly the one need it had before.
    """
    declared = inputs_of("terraform-apply")
    assert "default" not in declared["plan-job"], "plan-job must be required, not defaulted"
    assert declared["gate-jobs"]["default"] == []
    assert declared["gate-jobs"]["type"] == "array"

    job = jobs_of("terraform-apply")["$[[ inputs.instance ]]:terraform-apply"]
    assert job["needs"] == [
        {"job": "$[[ inputs.plan-job ]]", "artifacts": True},
        "$[[ inputs.gate-jobs ]]",
    ]


def test_apply_validates_the_plan_before_it_mutates_anything():
    """Section 11.2 items 6 and 7.

    The old apply job ran `terraform apply -auto-approve tfplan` against
    whatever artifact happened to be on disk, having checked nothing.
    """
    job = jobs_of("terraform-apply")["$[[ inputs.instance ]]:terraform-apply"]
    text = script_text(job)
    validate_at = text.index("validate-plan")
    guard_at = text.index("destroy-guard")
    apply_at = text.index("terraform apply")
    assert validate_at < apply_at, "the evidence is checked before the mutation"
    assert guard_at < apply_at, "the destroy guard runs before the mutation"
    assert "terraform plan" not in text, "apply must never re-plan (section 11.2 item 6)"
    assert text.count("terraform apply") == 1
    assert "-auto-approve" in text and "tfplan" in text


def test_apply_carries_its_deployment_controls():
    """Section 11.2 items 8 and 9, and section 7.1 on approvals."""
    job = jobs_of("terraform-apply")["$[[ inputs.instance ]]:terraform-apply"]
    assert job["interruptible"] is False, "a partial mutation must not be cancelled by a new commit"
    assert job["resource_group"] == "$[[ inputs.state-id ]]", "one resource group per state"
    assert job["environment"]["name"] == "$[[ inputs.environment ]]"


def test_apply_defaults_to_a_manual_run_on_the_default_branch_only():
    """Section 6.1: never default a production mutation to unconditional execution."""
    rules = inputs_of("terraform-apply")["run-rules"]["default"]
    assert len(rules) == 1
    rule = rules[0]
    assert rule["when"] == "manual"
    assert "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH" in rule["if"]
    assert '$CI_PIPELINE_SOURCE == "push"' in rule["if"]


def test_apply_refuses_destructive_plans_unless_told_otherwise():
    """The destroy guard terraform-hypervisor-vms implements by hand today."""
    allow_destroy = inputs_of("terraform-apply")["allow-destroy"]
    assert allow_destroy["type"] == "boolean"
    assert allow_destroy["default"] is False


def test_module_publish_requires_a_protected_tag():
    """Section 7.2: the release tag is the authorisation, so it must be protected.

    The old template accepted any tag matching ^v[0-9]+\\.[0-9]+\\.[0-9]+$ and
    hard-required the v prefix.
    """
    rules = inputs_of("terraform-module-publish")["run-rules"]["default"]
    assert len(rules) == 1
    condition = rules[0]["if"]
    assert "$CI_COMMIT_TAG" in condition
    assert '$CI_COMMIT_REF_PROTECTED == "true"' in condition


def test_module_publish_checks_immutability_and_its_own_result():
    """Section 11.5: reject an existing release rather than overwrite it."""
    job = jobs_of("terraform-module-publish")["$[[ inputs.instance ]]:terraform-module-publish"]
    text = script_text(job)
    assert text.index("--expect absent") < text.index("curl")
    assert text.index("curl") < text.index("--expect present")
    assert "module-package" in text
    assert "--fail-with-body" in text


@pytest.mark.parametrize("component", ["terraform-plan", "terraform-apply"])
def test_no_estate_backend_identity_is_baked_into_the_job(component):
    """The old plan-apply.yml carried `TF_HTTP_USERNAME=${TF_HTTP_USERNAME:-root}`."""
    job = next(iter(jobs_of(component).values()))
    variables = job["variables"]
    assert variables["TF_HTTP_USERNAME"] == "$[[ inputs.state-username ]]"
    assert variables["TF_HTTP_PASSWORD"] == "${$[[ inputs.state-token-variable ]]}"
    # No shell-level fallback identity anywhere in the job.
    assert not re.search(r"TF_HTTP_USERNAME=", script_text(job)), (
        f"{component} sets the backend username in the shell rather than from the input"
    )
    assert not re.search(r":-root\}", template_path(component).read_text())
    assert inputs_of(component)["state-token-variable"]["regex"] == "^[A-Z][A-Z0-9_]{0,63}$"
