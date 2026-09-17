"""Structural rules the four container components must keep.

These are the parts of sections 5, 6.1, 8, 9.4 and 10.1 that can be checked
without a server. They are deliberately narrow: each one names a rule from the
standard or a defect from the audit of the templates these components replace,
and each would fail against that old behaviour.

The CI Lint checks live in tests/pipelines/test_container_components.py. A green
run here says the file shape is right, not that GitLab accepts it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"
COMPONENTS = [
    "container-build-buildkit",
    "container-build-jib",
    "container-build-ko",
    "container-smoke-test",
]
BUILDERS = [name for name in COMPONENTS if name.startswith("container-build-")]
STAGES = {
    "verify", "build", "test", "scan", "plan",
    "attest", "publish", "deploy", "verify-deploy",
}
DIGEST_REGEX = r"^(\$[A-Z][A-Z0-9_]*/)?[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[0-9a-f]{64}$"
# Standard 1.0.5, section 12: a component may accept `name:tag` where its
# contract says which executor and which site the form is for. BuildKit does,
# because a site that mirrors an image into its own registry gets a digest of
# its own, so the estate's digest names nothing there and a tag is the only
# reference both sides can agree on. The shipped DEFAULT is still digest-pinned
# below, for every component including this one.
# The leading `$UPPER_SNAKE/` both patterns admit is the mirror prefix a site
# names once as a group variable; tests/contracts/test_execution_image_regex.py
# drives that form against every component.
TAG_OR_DIGEST_REGEX = (
    r"^(\$[A-Z][A-Z0-9_]*/)?[A-Za-z0-9][A-Za-z0-9._/:-]*"
    r"(@sha256:[0-9a-f]{64}|:[A-Za-z0-9._-]{1,128})$"
)
EXECUTION_IMAGE_REGEX = {
    "container-build-buildkit": TAG_OR_DIGEST_REGEX,
}
INSTANCE_REGEX = "^[a-z][a-z0-9-]{0,47}$"
FORBIDDEN_TOP_LEVEL = {
    "stages", "workflow", "default", "variables", "image", "cache", "before_script",
}
# Variables a job legitimately sets that are not part of the reserved namespace,
# because the tool reads them under a name it defines.
TOOL_VARIABLES = {
    "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_TLS_CERTDIR", "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH", "KO_DOCKER_REPO", "KO_DEFAULTBASEIMAGE", "GOFLAGS",
    "CGO_ENABLED",
}
# Runner feature flags, which configure the runner rather than the tool or the
# component. See tests/contracts/test_build_directory_ownership.py.
RUNNER_VARIABLES = {"FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR"}
# Section 13.1's working-directory guard, quoted exactly so a component that
# paraphrases it is caught.
ESCAPE_GUARD = """project_root=$(cd "$CI_PROJECT_DIR" && pwd -P)
cd "$CI_PROJECT_DIR/$CI_TPL_WORKING_DIRECTORY"
case "$(pwd -P)" in
  "$project_root"|"$project_root"/*) ;;
  *) echo 'ERROR: working-directory escapes the checkout'; exit 1 ;;
esac"""


def template_path(component: str) -> Path:
    return TEMPLATES_DIR / component / "template.yml"


def documents(component: str) -> tuple[dict, dict]:
    parsed = list(yaml.safe_load_all(template_path(component).read_text()))
    assert len(parsed) == 2, "a component is exactly two documents: spec, then jobs"
    # `include:` is not a job. A component that chooses its ordering from its
    # inputs includes one of the files under templates/_needs/ to carry it.
    jobs = {name: job for name, job in parsed[1].items() if name != "include"}
    return parsed[0], jobs


def inputs(component: str) -> dict:
    return documents(component)[0]["spec"]["inputs"]


def only_job(component: str) -> tuple[str, dict]:
    jobs = documents(component)[1]
    assert len(jobs) == 1, f"{component} emits {len(jobs)} jobs; these components emit one"
    return next(iter(jobs.items()))


NEEDS_ASSEMBLER = "/templates/_needs/needs.yml"
STAGE_BARRIER = "/templates/_needs/barrier.yml"


def needs_variants(component: str) -> list[list]:
    """Every `needs:` list this component can produce, as the template writes it.

    A component whose producers are all required writes one list in its job. A
    component with an optional producer, or with `subject-reference` as the
    alternative to `build-job`, cannot: a needs entry cannot name an empty job.
    It includes templates/_needs/needs.yml once per combination instead, and
    the assertions below hold for every combination.
    """
    parsed = list(yaml.safe_load_all(template_path(component).read_text()))
    _, job = only_job(component)
    if "needs" in job:
        return [job["needs"]]
    variants = []
    for entry in parsed[1].get("include", []) or []:
        assert entry["local"] in (NEEDS_ASSEMBLER, STAGE_BARRIER), entry
        if entry["local"] == STAGE_BARRIER:
            # That combination has no `needs:` at all; it is the stage barrier.
            continue
        supplied = entry["inputs"]
        variants.append([*supplied.get("producer-needs", []), supplied["gate-jobs"]])
    return variants


EMBED_OPEN = "# BEGIN embed "
EMBED_CLOSE = "# END embed"


def script_text(job: dict) -> str:
    """The component's own shell: the embedded helper and comments removed.

    The helper is shared runtime code with its own tests in
    tests/runtime/, and its comments discuss the very patterns these
    assertions forbid. Asserting over it would make every check here answer a
    question about prose rather than about the component.
    """
    lines: list[str] = []
    for block in job.get("before_script", []) + job.get("script", []):
        skipping = False
        for line in block.splitlines():
            if line.strip().startswith(EMBED_OPEN):
                skipping = True
                continue
            if skipping:
                skipping = line.strip() != EMBED_CLOSE
                continue
            if line.lstrip().startswith("#"):
                continue
            lines.append(line)
    return "\n".join(lines)


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_job_name_is_instance_then_component(component: str):
    name, _ = only_job(component)
    assert name == f"$[[ inputs.instance ]]:{component}"


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_mandatory_inputs_are_declared(component: str):
    declared = inputs(component)
    assert declared["instance"]["regex"] == INSTANCE_REGEX
    assert "default" not in declared["instance"], "instance is required"
    assert declared["stage"]["default"] in STAGES
    assert declared["execution-image"]["regex"] == EXECUTION_IMAGE_REGEX.get(
        component, DIGEST_REGEX
    )
    assert re.match(DIGEST_REGEX, declared["execution-image"]["default"]), (
        "the shipped default must itself be digest-pinned; section 1 forbids "
        "calling a tag a digest"
    )
    assert declared["runner-tags"]["type"] == "array"
    assert declared["runner-tags"]["default"] == []
    assert declared["run-rules"]["type"] == "array"
    assert declared["run-rules"]["default"], "run-rules needs a safe default"
    assert declared["working-directory"]["default"] == "."


@pytest.mark.parametrize("component", COMPONENTS)
def test_every_input_has_a_description(component: str):
    missing = [name for name, spec in inputs(component).items() if not spec.get("description")]
    assert missing == []


@pytest.mark.parametrize("component", BUILDERS)
def test_a_builder_never_defaults_to_pushing_from_a_merge_request(component: str):
    """Section 6.1: never default a mutation to unconditional execution. A
    candidate push from an untrusted merge request pipeline is exactly that."""
    for rule in inputs(component)["run-rules"]["default"]:
        assert "if" in rule, f"{component} has an unconditional default rule: {rule}"
        assert "merge_request_event" not in rule["if"]
        assert '$CI_PIPELINE_SOURCE == "push"' in rule["if"]


@pytest.mark.parametrize("component", BUILDERS)
def test_a_builder_pins_its_base_image_by_digest_where_it_sets_one(component: str):
    declared = inputs(component)
    if "base-image" not in declared:
        return
    assert declared["base-image"]["regex"] == DIGEST_REGEX
    assert re.match(DIGEST_REGEX, declared["base-image"]["default"])


@pytest.mark.parametrize("component", COMPONENTS)
def test_no_forbidden_top_level_keys(component: str):
    jobs = documents(component)[1]
    assert FORBIDDEN_TOP_LEVEL & set(jobs) == set()


@pytest.mark.parametrize("component", COMPONENTS)
def test_no_extends_parent(component: str):
    _, job = only_job(component)
    assert "extends" not in job


@pytest.mark.parametrize("component", COMPONENTS)
def test_failures_are_never_swallowed(component: str):
    """Section 10.1. The templates these replace ended a scan in `|| true` and a
    missing digest in a warning plus exit 0."""
    _, job = only_job(component)
    text = script_text(job)
    assert "|| true" not in text
    assert not re.search(r"^\s*exit 0\s*$", text, re.MULTILINE)
    assert job["allow_failure"] is False


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_dependency_declaration_is_one_of_the_two_allowed_shapes(component: str):
    """Section 8.5 and 8.6: never both, never `needs: []` without cause, and a
    source-only job carries `dependencies: []` so it downloads nothing
    incidentally.

    A component that chooses between the two from its inputs cannot write either
    key here, because YAML has no conditional key and the including file wins
    whatever it sets. It includes one file per combination from
    templates/_needs/ instead, so the rule becomes: this body declares neither,
    and every file it includes declares exactly one of the two.

    A component that offers `subject-reference` has one combination with no
    entries at all: the consumer named the image rather than a producer, and
    passed no gates either, so there is nothing in this pipeline to wait for.
    That case renders as `needs: []`, which downloads no artifacts and starts
    the job at once. Any other component reaching it would be bypassing a stage
    barrier it needs, so only these may.
    """
    # The unfiltered second document: `documents()` drops `include:` for every
    # other test, and this is the one test that is about it.
    body = list(yaml.safe_load_all(template_path(component).read_text()))[1]
    _, job = only_job(component)
    assert not ("needs" in job and "dependencies" in job)
    if "include" in body:
        assert "needs" not in job and "dependencies" not in job, (
            f"{component} declares an ordering key and includes one; the body "
            "would win and the include would be dead"
        )
        for entry in body["include"]:
            fragment = list(
                yaml.safe_load_all((REPO_ROOT / entry["local"].lstrip("/")).read_text())
            )
            (supplied,) = fragment[1].values()
            assert set(supplied) in ({"needs"}, {"dependencies"}), (
                f"{entry['local']} supplies {sorted(supplied)}"
            )
    elif "needs" not in job:
        assert job["dependencies"] == []
    for entries in needs_variants(component):
        if entries:
            continue
        assert "subject-reference" in inputs(component), (
            f"{component}: needs: [] bypasses the stage barrier"
        )


@pytest.mark.parametrize("component", COMPONENTS)
def test_artifacts_stay_under_the_component_root(component: str):
    _, job = only_job(component)
    artifacts = job["artifacts"]
    root = f".ci-artifacts/$[[ inputs.instance ]]/{component}/"
    for path in artifacts["paths"]:
        assert path.startswith(root), path
    dotenv = artifacts.get("reports", {}).get("dotenv")
    if dotenv:
        assert dotenv.startswith(root)
    assert "$[[ inputs.instance ]]" in artifacts["name"]
    assert component in artifacts["name"]
    assert artifacts["expire_in"]


@pytest.mark.parametrize("component", COMPONENTS)
def test_job_variables_are_reserved_or_tool_defined(component: str):
    """Section 5.3: internal variables carry the CI_TPL_ prefix so a consumer can
    see which names the component owns."""
    _, job = only_job(component)
    unexpected = {
        name
        for name in job["variables"]
        if not name.startswith("CI_TPL_") and name not in TOOL_VARIABLES | RUNNER_VARIABLES
    }
    assert unexpected == set()


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_working_directory_guard_is_the_one_from_the_standard(component: str):
    _, job = only_job(component)
    assert any(ESCAPE_GUARD in line for line in job["before_script"])


@pytest.mark.parametrize("component", COMPONENTS)
def test_no_eval_anywhere(component: str):
    """Section 6.1: pass values through quoted arguments, never build a command
    with eval."""
    _, job = only_job(component)
    assert not re.search(r"\beval\b", script_text(job))


@pytest.mark.parametrize("component", COMPONENTS)
def test_an_array_input_reaches_the_shell_as_a_json_literal(component: str):
    """GitLab substitutes the array itself when a placeholder is the whole value,
    and rejects a variable whose value is a list. Embedding the placeholder in a
    longer string is what makes it render as JSON text instead. The live proof is
    test_gitlab_rejects_a_bare_array_in_variables in tests/pipelines/."""
    declared = inputs(component)
    _, job = only_job(component)
    arrays = {
        name for name, spec in declared.items()
        if spec.get("type") == "array" and name not in ("runner-tags", "run-rules")
    }
    for name in arrays:
        placeholder = f"$[[ inputs.{name} ]]"
        for value in job["variables"].values():
            if isinstance(value, str) and placeholder in value:
                assert value.strip() != placeholder, (
                    f"{component}: {name} must be embedded in a longer string"
                )


def test_the_smoke_test_declares_its_producers_and_consumes_their_artifacts():
    """Section 8.1 and 8.3: the exact producer, artifacts on, never optional."""
    variants = needs_variants("container-smoke-test")
    # With a build job: the exact producer, artifacts on. Without one, the
    # consumer named the image with subject-reference and there is no producer
    # to consume. Either way the service producers are spliced in.
    assert variants[0][0] == {"job": "$[[ inputs.build-job ]]", "artifacts": True}
    for entries in variants:
        assert entries[-1] == "$[[ inputs.service-image-jobs ]]"
        assert "optional" not in str(entries)
    declared = inputs("container-smoke-test")
    assert declared["build-job"]["default"] == ""
    assert declared["subject-reference"]["default"] == ""
    assert "default" not in declared["image-identities"]


def test_the_smoke_test_keeps_daemon_tls_on():
    """Section 10.2. The template this replaces set DOCKER_TLS_CERTDIR to an
    empty string and used plaintext 2375."""
    _, job = only_job("container-smoke-test")
    assert job["variables"]["DOCKER_TLS_CERTDIR"] == "/certs"
    assert job["variables"]["DOCKER_HOST"] == "tcp://docker:2376"
    assert job["variables"]["DOCKER_TLS_VERIFY"] == "1"


def test_the_smoke_test_emits_a_runnable_job():
    """The template this replaces defined only a hidden job, so a consumer that
    disabled it produced a job with rules and no script and the whole config
    failed to lint."""
    name, job = only_job("container-smoke-test")
    assert not name.startswith(".")
    assert job["script"]


@pytest.mark.parametrize("component", BUILDERS)
def test_a_builder_records_the_digest_and_never_a_tag(component: str):
    """The single defect shared by all three templates these replace: a tag was
    written into a field named digest."""
    _, job = only_job(component)
    text = script_text(job)
    assert "ci_tpl_write_image_json" in text
    assert "ci_tpl_require_digest" in text or "ci_tpl_buildkit_digest" in text or (
        "ci_tpl_reference_digest" in text
    )


def test_buildkit_reads_its_own_metadata_file_and_queries_no_registry():
    """The audit's first finding: the digest was scraped from a wget header dump
    with `|| true`, while --metadata-file was written and never read."""
    _, job = only_job("container-build-buildkit")
    text = script_text(job)
    assert "--metadata-file" in text
    assert "ci_tpl_buildkit_digest" in text
    assert "wget" not in text
    assert "docker-content-digest" not in text


def test_ko_installs_a_pinned_version():
    """Section 12 names `go install ...@latest` explicitly; the template this
    replaces used it."""
    declared = inputs("container-build-ko")
    _, job = only_job("container-build-ko")
    text = script_text(job)
    assert "@latest" not in text
    assert "$CI_TPL_KO_VERSION" in text
    assert re.match(r"^v[0-9]+\.[0-9]+\.[0-9]+$", declared["ko-version"]["default"])


def test_ko_adds_no_release_tag_of_its_own():
    """The template this replaces pushed `latest` on every build."""
    _, job = only_job("container-build-ko")
    assert inputs("container-build-ko")["tags"]["default"] == ["$CI_COMMIT_SHORT_SHA"]
    assert "latest" not in script_text(job)


def test_jib_passes_no_credential_on_the_command_line():
    """Section 10.2: the template this replaces passed -Djib.to.auth.password,
    which puts the secret in the process table."""
    _, job = only_job("container-build-jib")
    text = script_text(job)
    assert "jib.to.auth" not in text
    # The credential reaches Jib through the docker config file the helper
    # writes, so it must not appear anywhere in the component's own commands.
    assert "$CI_TPL_REGISTRY_PASSWORD" not in text
    assert job["variables"]["DOCKER_CONFIG"]
