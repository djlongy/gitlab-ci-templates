"""Lint the container mirror composition and its components on the real server.

These replace the YAML fixtures that arrived from main under
tests/pipelines/container-mirror/. Those files were linted by hand, which is
another way of saying nobody had linted them since the day they were written.
Everything they proved is proved here, and one thing they could not: their
README records that `defaults.yml` "cannot produce a job list from a feature
branch", because `include: local` at a non-default ref needs `dry_run: true` and
a dry run simulates a push this composition's `workflow:` rejects. The harness
does not include from the server at all — it resolves the composition locally
and sends the resulting jobs — so `dry_run: false` compiles the real graph and
GitLab answers with the job names.

The cases kept from the fixtures, by name:

    defaults.yml          -> test_the_composition_lints_with_required_inputs_only
    vault-mode.yml        -> test_the_vault_credential_path_still_compiles
    two-instances.yml     -> test_two_instances_emit_their_own_jobs_and_artifacts
    missing-list-job.yml  -> test_the_mirror_refuses_to_compile_without_its_producer

Needs GITLAB_TOKEN with api scope, and GITLAB_URL/GITLAB_PROJECT_ID to point
at your own GitLab; see README.md. The suite
skips without one, because a missing credential is not evidence of a broken
component.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

import composition

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "templates"

MIRROR_JOB = "container-mirror-skopeo"
LIST_JOB = "container-list-rke2"
EXPORT_JOB = "container-export-skopeo"

# Enough to resolve the composition. Hosts are .invalid on purpose: nothing here
# connects to anything, and a real name in a fixture is a name somebody copies.
REQUIRED = {
    "registry": "registry.invalid",
    "repository-prefix": "mirror",
    "s3-endpoint": "http://s3.invalid:9010",
    "s3-bucket": "fixture-bucket",
    "s3-prefix": "transfer/out",
    "have-key": "transfer/have/blobs.txt",
}
VAULT = {
    "vault-addr": "https://vault.invalid:8200",
    "vault-role": "gitlab-ci",
    "vault-kv-path": "kv/apps/fixture",
    "vault-s3-kv-path": "kv/apps/fixture-s3",
}
ALWAYS = [{"when": "always"}]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_module = load("ci_lint", "lint.py")
render = load("render_component", "render_component.py")

needs_token = pytest.mark.skipif(
    not os.environ.get("GITLAB_TOKEN"),
    reason="GITLAB_TOKEN is not set; see README.md, Running the tests",
)


# --------------------------------------------------------------------------
# What the composition resolves to, before any server sees it
# --------------------------------------------------------------------------

def test_only_a_merge_request_runs_the_mirror_in_inspect_mode():
    """An input is fixed at pipeline creation, so the mode is a rule variable.
    It must be set on the merge request rule and on no other."""
    config = composition.resolve("container-mirror", instance="mirror", **REQUIRED)
    carrying = [
        rule
        for rule in config[f"mirror:{MIRROR_JOB}"]["rules"]
        if "CI_TPL_DRY_RUN" in str(rule.get("variables", {}))
    ]
    assert len(carrying) == 1, config[f"mirror:{MIRROR_JOB}"]["rules"]
    assert carrying[0]["if"] == '$CI_PIPELINE_SOURCE == "merge_request_event"'
    assert carrying[0]["variables"]["CI_TPL_DRY_RUN"] == "true"


def test_nothing_leaves_the_low_side_from_a_merge_request():
    """The export job uploads to the object store. A merge request may inspect
    the registry; it may not ship."""
    config = composition.resolve("container-mirror", instance="mirror", **REQUIRED)
    conditions = composition.rule_conditions(config[f"mirror:{EXPORT_JOB}"])
    assert not any("merge_request_event" in condition for condition in conditions), conditions


def test_the_export_gates_on_the_mirror_and_consumes_only_the_list():
    """Section 8.1 and 8.3: the exact producers, artifacts where an artifact is
    read, and never optional."""
    config = composition.resolve("container-mirror", instance="mirror", **REQUIRED)
    needs = config[f"mirror:{EXPORT_JOB}"]["needs"]
    assert needs == [
        {"job": f"mirror:{LIST_JOB}", "artifacts": True},
        {"job": f"mirror:{MIRROR_JOB}", "artifacts": False},
    ]


# --------------------------------------------------------------------------
# The real server
# --------------------------------------------------------------------------

@needs_token
def test_the_composition_lints_with_required_inputs_only():
    """Was tests/pipelines/container-mirror/defaults.yml."""
    config = composition.render("container-mirror", instance="mirror", **REQUIRED)
    result = lint_module.lint(config, dry_run=False)
    assert result["valid"], result.get("errors")
    assert sorted(lint_module.job_names(result)) == sorted(
        [f"mirror:{LIST_JOB}", f"mirror:{MIRROR_JOB}", f"mirror:{EXPORT_JOB}"]
    )


@needs_token
def test_the_vault_credential_path_still_compiles():
    """Was vault-mode.yml. An empty registry input is allowed once vault-addr is
    set, because the host is then read from the KV path."""
    config = composition.resolve(
        "container-mirror",
        instance="mirror",
        **{**REQUIRED, "registry": "", **VAULT},
    )
    assert config[f"mirror:{MIRROR_JOB}"]["id_tokens"]["VAULT_JWT"]["aud"] == VAULT["vault-addr"]
    result = lint_module.lint(composition.dump_yaml(config), dry_run=False)
    assert result["valid"], result.get("errors")
    assert sorted(lint_module.job_names(result)) == sorted(
        [f"mirror:{LIST_JOB}", f"mirror:{MIRROR_JOB}", f"mirror:{EXPORT_JOB}"]
    )


@needs_token
def test_two_instances_emit_their_own_jobs_and_artifacts():
    """Was two-instances.yml. Two workloads in one project are two instances,
    and nothing about one may reach the other: not the job name, not the
    artifact directory, not the producer edge."""
    jobs = {}
    for instance, prefix in (("alpha", "alpha-org"), ("beta", "beta-org")):
        jobs.update(
            render.render(
                TEMPLATES / LIST_JOB / "template.yml",
                instance=instance,
                stage="verify",
                **{"run-rules": ALWAYS},
            )
        )
        jobs.update(
            render.render(
                TEMPLATES / MIRROR_JOB / "template.yml",
                instance=instance,
                **{
                    "list-job": f"{instance}:{LIST_JOB}",
                    "list-files": f".ci-artifacts/{instance}/{LIST_JOB}/images.txt",
                    "repository-prefix": prefix,
                    "registry": "registry.invalid",
                    "run-rules": ALWAYS,
                },
            )
        )
    config = render.compose(["verify", "publish"], jobs)
    result = lint_module.lint(config, dry_run=False)
    assert result["valid"], result.get("errors")
    assert sorted(lint_module.job_names(result)) == [
        f"alpha:{LIST_JOB}",
        f"alpha:{MIRROR_JOB}",
        f"beta:{LIST_JOB}",
        f"beta:{MIRROR_JOB}",
    ]
    by_name = {job["name"]: job for job in result["jobs"]}
    for instance in ("alpha", "beta"):
        # The Lint API spells a need `name`, not `job`, and fills in `optional`.
        # It reports no artifacts at all, so the paths are asserted on the
        # rendered configuration it was given.
        assert by_name[f"{instance}:{MIRROR_JOB}"]["needs"] == [
            {"name": f"{instance}:{LIST_JOB}", "artifacts": True, "optional": False}
        ]
        for component in (LIST_JOB, MIRROR_JOB):
            artifacts = jobs[f"{instance}:{component}"]["artifacts"]
            assert artifacts["paths"] == [f".ci-artifacts/{instance}/{component}/"]
            assert artifacts["name"].startswith(f"{instance}-{component}-")


@needs_token
def test_the_mirror_refuses_to_compile_without_its_producer():
    """Was missing-list-job.yml, and the positive control for the edges above.

    A mirror with no reference list must not be a pipeline that runs. Standalone
    inclusion of a component that consumes an artifact is not supported, and
    pipeline creation failing on a producer that does not exist is the intended
    outcome rather than a quiet degrade.
    """
    config = composition.resolve("container-mirror", instance="mirror", **REQUIRED)
    del config[f"mirror:{LIST_JOB}"]
    result = lint_module.lint(composition.dump_yaml(config), dry_run=False)
    assert not result["valid"], "GitLab accepted a mirror whose list job is absent"
    assert any(LIST_JOB in error for error in result["errors"]), result["errors"]


# --------------------------------------------------------------------------
# The real consumer
# --------------------------------------------------------------------------

CONSUMER = Path(__file__).parent / "fixtures" / "container-mirror.consumer.gitlab-ci.yml"


def consumer_include() -> dict:
    body = composition.load_yaml(CONSUMER.read_text())
    assert list(body) == ["include"], "the consumer file is an include and nothing else"
    entry = body["include"][0]
    assert entry["file"] == "/pipelines/container-mirror.yml"
    assert entry["ref"] == "REPLACE_WITH_APPROVED_SHARED_CI_REF"
    return entry["inputs"]


def test_the_consumer_sets_no_input_this_composition_stopped_declaring():
    """platform/k8s-gitops pins 0.2.0 and every input below came from that tag.

    A rename or a removal here is a consumer that cannot repin, and it has
    happened once already: 0.1.0's ca-chain-url became 0.2.0's ca-bundle-url and
    the consumer had to change shape in the same commit. This is the test that
    makes the next one visible before the repin rather than after.
    """
    declared, _ = composition.load("container-mirror")
    unknown = sorted(set(consumer_include()) - set(declared))
    assert unknown == [], (
        f"the consumer passes inputs this composition no longer declares: {unknown}"
    )


def test_the_consumer_supplies_every_input_that_has_no_default():
    """The other half: an input made required is as breaking as one renamed."""
    declared, _ = composition.load("container-mirror")
    required = {name for name, spec in declared.items() if "default" not in (spec or {})}
    missing = sorted(required - set(consumer_include()))
    assert missing == [], f"the consumer supplies no value for {missing}"


@needs_token
def test_the_consumer_configuration_compiles_against_this_tree():
    """The consumer's own values, resolved against the composition in this tree.

    It runs in Vault mode with the registry input left empty, which is the
    combination no other test here covers: the host is read from the KV path
    rather than supplied.
    """
    inputs = consumer_include()
    config = composition.resolve("container-mirror", **inputs)
    instance = inputs["instance"]
    mirror = config[f"{instance}:{MIRROR_JOB}"]
    assert mirror["variables"]["CI_TPL_REGISTRY"] == "", "the consumer reads its registry from Vault"
    assert mirror["id_tokens"]["VAULT_JWT"]["aud"] == inputs["vault-addr"]

    result = lint_module.lint(composition.dump_yaml(config), dry_run=False)
    assert result["valid"], result.get("errors")
    assert sorted(lint_module.job_names(result)) == sorted(
        [f"{instance}:{LIST_JOB}", f"{instance}:{MIRROR_JOB}", f"{instance}:{EXPORT_JOB}"]
    )
