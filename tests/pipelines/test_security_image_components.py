"""Structure and merged-configuration checks for the security-image components.

Two halves. The offline half reads the eight templates and asserts what sections
5 to 10 require of every public component: job naming, artifact roots,
digest-pinned images, no suppressed failure, no secret in an input. The online
half renders the inputs with tools/resolve/render_component.py and sends the
result to the CI Lint API, which is the only check here that compiles a
component the way GitLab will.

The renderer, not GitLab, performs the substitution, so the first online test is
the positive control that pins the two against each other. The second sends
configuration GitLab must reject, so a client that returned `valid: true` for
everything could not pass.

The online tests skip without GITLAB_TOKEN: a missing credential is not evidence
of a broken component, and a skipped run earns no `gitlab-linted` label. A lint
is not an execution; nothing here runs a job.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
TEMPLATES = REPO_ROOT / "templates"
FIXTURES = Path(__file__).parent / "fixtures" / "security-image-components.yml"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_module = load("ci_lint", "lint.py")

# render_component moved to tools/resolve/ so the lint harness and the local
# runner share one implementation; `load()` still reaches lint.py beside this file.
from tools.resolve import render_component as render  # noqa: E402

FIXTURE_DATA = yaml.safe_load(FIXTURES.read_text())["components"]
COMPONENTS = sorted(FIXTURE_DATA)

STAGE_VOCABULARY = {
    "verify", "build", "test", "scan", "plan",
    "attest", "publish", "deploy", "verify-deploy",
}
FORBIDDEN_TOP_LEVEL = {
    "stages", "workflow", "default", "variables", "image", "cache", "before_script",
}
DIGEST_REGEX = r"^.+@sha256:[0-9a-f]{64}$"

needs_token = pytest.mark.skipif(
    not os.environ.get("GITLAB_TOKEN"),
    reason="GITLAB_TOKEN is not set; see README.md, Running the tests",
)


def template_path(component: str) -> Path:
    return TEMPLATES / component / "template.yml"


def declared(component: str) -> dict:
    return render.load(template_path(component))[0]


def body(component: str) -> dict:
    return render.load(template_path(component))[1]


def job(component: str) -> dict:
    jobs = body(component)
    assert len(jobs) == 1, f"{component} declares {len(jobs)} top-level keys; expected one job"
    return next(iter(jobs.values()))


def job_name(component: str) -> str:
    return next(iter(body(component)))


def resolve(value, instance: str):
    if isinstance(value, str):
        return value.format(instance=instance)
    if isinstance(value, list):
        return [resolve(item, instance) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, instance) for key, item in value.items()}
    return value


def inputs_for(component: str, instance: str) -> dict:
    values = {"instance": instance}
    values.update(resolve(FIXTURE_DATA[component].get("inputs", {}), instance))
    return values


def producers_for(component: str, instances: list[str], omit: str | None = None) -> dict:
    jobs = {}
    for instance in instances:
        for producer, stage in FIXTURE_DATA[component]["producers"].items():
            name = f"{instance}:{producer}"
            if name == omit:
                continue
            jobs[name] = {"stage": stage, "script": ["true"]}
    return jobs


def rendered(component: str, instance: str) -> dict:
    return render.render(template_path(component), **inputs_for(component, instance))


# --------------------------------------------------------------- structure


@pytest.mark.parametrize("component", COMPONENTS)
def test_job_name_is_instance_then_component_name(component: str):
    assert job_name(component) == f"$[[ inputs.instance ]]:{component}"


@pytest.mark.parametrize("component", COMPONENTS)
def test_instance_input_is_required_and_constrained(component: str):
    instance = declared(component)["instance"]
    assert "default" not in instance, "instance must be supplied, never defaulted"
    assert instance["regex"] == "^[a-z][a-z0-9-]{0,47}$"


@pytest.mark.parametrize("component", COMPONENTS)
def test_stage_default_is_in_the_vocabulary(component: str):
    assert declared(component)["stage"]["default"] in STAGE_VOCABULARY


@pytest.mark.parametrize("component", COMPONENTS)
def test_execution_image_default_is_pinned_by_digest(component: str):
    image = declared(component)["execution-image"]
    assert image["regex"] == DIGEST_REGEX
    assert re.match(DIGEST_REGEX, image["default"]), image["default"]


@pytest.mark.parametrize("component", COMPONENTS)
def test_runner_tags_and_run_rules_are_arrays(component: str):
    inputs = declared(component)
    assert inputs["runner-tags"]["type"] == "array"
    assert inputs["runner-tags"]["default"] == []
    assert inputs["run-rules"]["type"] == "array"
    assert inputs["run-rules"]["default"], "an empty rules default creates a job that never runs"


@pytest.mark.parametrize("component", COMPONENTS)
def test_no_forbidden_top_level_keys(component: str):
    present = set(body(component)) & FORBIDDEN_TOP_LEVEL
    assert not present, f"{component} declares {present} at the top level"


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_runtime_arrives_through_an_embedded_block_not_an_include(component: str):
    """Section 12: a component include imports YAML, never this repository."""
    assert "include" not in body(component)
    assert "extends" not in job(component)
    text = template_path(component).read_text()
    assert "# BEGIN embed " in text


@pytest.mark.parametrize("component", COMPONENTS)
def test_artifacts_live_under_the_component_root(component: str):
    artifacts = job(component)["artifacts"]
    assert artifacts["paths"] == [f".ci-artifacts/$[[ inputs.instance ]]/{component}/"]
    assert "$[[ inputs.instance ]]" in artifacts["name"]
    assert component in artifacts["name"]


@pytest.mark.parametrize("component", COMPONENTS)
def test_failure_is_never_suppressed(component: str):
    """Read the parsed job: the comments quote the old behaviour verbatim."""
    definition = job(component)
    assert definition["allow_failure"] is False
    script = "\n".join(definition["script"])
    assert "|| true" not in script
    assert not re.search(r"^\s*exit 0\s*$", script, re.MULTILINE)
    for rule in declared(component)["run-rules"]["default"]:
        assert rule.get("allow_failure") is not True


@pytest.mark.parametrize("component", COMPONENTS)
def test_needs_are_explicit_and_never_optional(component: str):
    definition = job(component)
    assert "dependencies" not in definition, "needs and dependencies must not both appear"
    entries = definition["needs"]
    assert entries, "no component here is source-only"
    for entry in entries:
        if isinstance(entry, str):
            continue  # the interpolated gate-jobs array
        assert "optional" not in entry, "a required producer is never optional"
        assert entry["job"].startswith("$[[ inputs.")


@pytest.mark.parametrize("component", COMPONENTS)
def test_gate_jobs_is_spliced_into_needs(component: str):
    """One deliberate list: producers stay, gates are added (section 8)."""
    assert "$[[ inputs.gate-jobs ]]" in job(component)["needs"]


@pytest.mark.parametrize("component", COMPONENTS)
def test_artifact_producers_are_declared_with_artifacts_true(component: str):
    definition = job(component)
    producing = [
        entry for entry in definition["needs"]
        if isinstance(entry, dict) and entry.get("artifacts") is True
    ]
    reads = any(
        name in definition["variables"]
        for name in ("CI_TPL_IDENTITY_FILE", "CI_TPL_SBOM_DIR")
    )
    assert bool(producing) == reads


@pytest.mark.parametrize("component", COMPONENTS)
def test_gate_only_needs_declare_artifacts_false(component: str):
    for entry in job(component)["needs"]:
        if isinstance(entry, dict) and entry["job"] in (
            "$[[ inputs.sign-job ]]", "$[[ inputs.sync-job ]]"
        ):
            assert entry["artifacts"] is False


@pytest.mark.parametrize("component", COMPONENTS)
def test_no_input_carries_a_secret(component: str):
    for name, options in declared(component).items():
        assert not re.search(r"(password|token|api-key|secret)$", name), (
            f"{component} input {name} looks like a secret"
        )
        assert "://" not in str(options.get("default", "")) or name in (
            "cosign-key", "vault-address", "vault-jwt-audience",
        )


# Variables a job may set unprefixed: they configure the runner rather than
# carrying the component's own state, which is what CI_TPL_ reserves.
RUNNER_VARIABLES = {"GIT_STRATEGY", "FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR"}


@pytest.mark.parametrize("component", COMPONENTS)
def test_reserved_runtime_variables_are_prefixed(component: str):
    for name in job(component).get("variables", {}):
        assert name.startswith("CI_TPL_") or name in RUNNER_VARIABLES, (
            f"{component} defines an unprefixed variable {name}"
        )


@pytest.mark.parametrize("component", COMPONENTS)
def test_no_eval_in_any_script(component: str):
    assert not re.search(r"\beval\b", "\n".join(job(component)["script"]))


def test_promotion_defaults_to_a_protected_tag_and_a_manual_action():
    """The shipped rules, which the lint fixture deliberately overrides."""
    rules = declared("container-promote-harbor")["run-rules"]["default"]
    assert rules[0]["if"] == '$CI_COMMIT_TAG && $CI_COMMIT_REF_PROTECTED == "true"'
    assert rules[0]["when"] == "manual"
    assert rules[-1] == {"when": "never"}


def test_promotion_is_serialised_and_names_an_environment():
    definition = job("container-promote-harbor")
    assert definition["environment"]["name"] == "$[[ inputs.environment ]]"
    assert definition["resource_group"]
    assert definition["interruptible"] is False


@pytest.mark.parametrize(
    "component",
    ["container-sign-attest-cosign", "security-sync-vigil", "security-verify-vigil"],
)
def test_signing_and_the_release_gate_never_run_on_a_merge_request(component: str):
    first = declared(component)["run-rules"]["default"][0]
    assert first["if"] == '$CI_PIPELINE_SOURCE == "merge_request_event"'
    assert first["when"] == "never"


@pytest.mark.parametrize("component", ["security-image-trivy", "security-image-grype"])
def test_a_vulnerability_scan_hydrates_its_database_first(component: str):
    """A scanner with no database reports nothing and looks clean.

    The database step and the snapshot it records are the only thing standing
    between an unhydratable client and a green gate, so removing either fails
    here. Both commands were run against the pinned image digests on
    2026/09/15; this checks they are still in the job.
    """
    script = "\n".join(job(component)["script"])
    hydrate = {
        "security-image-trivy": "trivy image --download-db-only",
        "security-image-grype": "/grype db update",
    }[component]
    assert hydrate in script
    assert "CI_TPL_DB_UPDATED_AT=" in script
    assert "${CI_TPL_DB_UPDATED_AT:-unresolved}" in script, (
        "the snapshot must reach scan-result.json"
    )


def test_only_syft_writes_the_authoritative_sbom():
    """Section 9.2: one authoritative SBOM producer, one path (audit 5.1)."""
    for component in COMPONENTS:
        script = "\n".join(job(component)["script"])
        writes = "$CI_TPL_ARTIFACT_DIR/sbom.cdx.json" in script
        assert writes == (component == "security-sbom-syft"), component


def test_no_component_declares_raw_sarif_as_a_gitlab_report():
    """estate report_mode is artifact-only; section 10.3 forbids reports:sast."""
    for component in COMPONENTS:
        assert "reports" not in job(component)["artifacts"]


def test_the_signing_component_declares_its_vault_identity_as_inputs():
    """Section 10.2 forbids a hardcoded address or a universal role."""
    definition = job("container-sign-attest-cosign")
    assert definition["id_tokens"]["VAULT_ID_TOKEN"]["aud"] == "$[[ inputs.vault-jwt-audience ]]"
    for name in ("vault-address", "vault-auth-path", "vault-role", "vault-jwt-audience"):
        assert name in declared("container-sign-attest-cosign")


# --------------------------------------------------------------------- lint


@needs_token
def test_the_lint_client_rejects_broken_configuration():
    """Positive control: a client returning valid for everything cannot pass."""
    result = lint_module.lint(
        "job:\n  stage: nonexistent\n  script: ['true']\n",
        include_jobs=True,
        dry_run=True,
    )
    assert not result["valid"]
    assert result["errors"]


@needs_token
@pytest.mark.parametrize("component", COMPONENTS)
def test_gitlab_and_the_renderer_agree_on_the_job_name(component: str):
    """Positive control for render_component, per component.

    GitLab applies a root spec header itself. If its interpolation and the
    renderer's ever disagreed, every lint below would be measuring something
    the consumer will not get.
    """
    header, separator, template_body = template_path(component).read_text().partition("\n---\n")
    assert separator
    spec = yaml.safe_load(header)
    for name, value in inputs_for(component, "api").items():
        spec["spec"]["inputs"][name]["default"] = value
    stages = yaml.safe_dump({"stages": FIXTURE_DATA[component]["stages"]})
    stubs = yaml.safe_dump(producers_for(component, ["api"]))
    payload = yaml.safe_dump(spec) + "---\n" + stages + stubs + template_body

    through_gitlab = lint_module.lint(payload, include_jobs=True, dry_run=True)
    assert through_gitlab["valid"], through_gitlab.get("errors")
    assert f"api:{component}" in lint_module.job_names(through_gitlab)


@needs_token
@pytest.mark.parametrize("component", COMPONENTS)
def test_a_single_instance_with_defaults_lints_and_emits_its_job(component: str):
    payload = render.compose(
        FIXTURE_DATA[component]["stages"],
        producers_for(component, ["api"]),
        rendered(component, "api"),
    )
    result = lint_module.lint(payload, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    assert f"api:{component}" in lint_module.job_names(result)


@needs_token
@pytest.mark.parametrize("component", COMPONENTS)
def test_two_instances_emit_distinct_job_names(component: str):
    payload = render.compose(
        FIXTURE_DATA[component]["stages"],
        producers_for(component, ["api", "web"]),
        rendered(component, "api"),
        rendered(component, "web"),
    )
    result = lint_module.lint(payload, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    names = lint_module.job_names(result)
    assert f"api:{component}" in names
    assert f"web:{component}" in names


@needs_token
@pytest.mark.parametrize("component", COMPONENTS)
def test_a_missing_producer_fails_the_lint(component: str):
    """Section 8.10: validate the missing-producer case before releasing."""
    missing = FIXTURE_DATA[component]["required_producer"].format(instance="api")
    payload = render.compose(
        FIXTURE_DATA[component]["stages"],
        producers_for(component, ["api"], omit=missing),
        rendered(component, "api"),
    )
    result = lint_module.lint(payload, include_jobs=True, dry_run=True)
    assert not result["valid"], f"{component} linted without its producer {missing}"
    assert any("undefined need" in message for message in result["errors"]), result["errors"]


# ------------------------------------------------------- scan policy mode

POLICY_COMPONENTS = sorted(
    component for component in COMPONENTS if "policy-mode" in declared(component)
)


def test_the_policy_components_are_the_scanners():
    """Guard against an empty parametrisation quietly asserting nothing."""
    assert POLICY_COMPONENTS == ["security-image-grype", "security-image-trivy"]


@pytest.mark.parametrize("component", POLICY_COMPONENTS)
def test_an_image_scan_is_blocking_by_default(component: str):
    policy = declared(component)["policy-mode"]
    assert policy["default"] == "blocking"
    assert sorted(policy["options"]) == ["advisory", "blocking"]
    assert job(component)["variables"]["CI_TPL_POLICY_MODE"] == "$[[ inputs.policy-mode ]]"


@needs_token
@pytest.mark.parametrize("component", POLICY_COMPONENTS)
def test_a_policy_mode_outside_the_options_is_rejected(component: str):
    """The options list is the server's to enforce, not a runtime `case`.

    Posted as a root spec header, which is how GitLab gets the chance to refuse
    the value before any shell sees it.
    """
    header, separator, template_body = template_path(component).read_text().partition("\n---\n")
    assert separator
    spec = yaml.safe_load(header)
    for name, value in inputs_for(component, "api").items():
        spec["spec"]["inputs"][name]["default"] = value
    spec["spec"]["inputs"]["policy-mode"]["default"] = "warn"
    payload = (
        yaml.safe_dump(spec)
        + "---\n"
        + yaml.safe_dump({"stages": FIXTURE_DATA[component]["stages"]})
        + yaml.safe_dump(producers_for(component, ["api"]))
        + template_body
    )
    result = lint_module.lint(payload, include_jobs=True, dry_run=True)
    assert not result["valid"], "the server accepted a policy mode outside the options"
    assert any("policy-mode" in message for message in result["errors"]), result["errors"]
