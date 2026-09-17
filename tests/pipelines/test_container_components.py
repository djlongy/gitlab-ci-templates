"""Lint the container build and test components against the live CI Lint API.

Section 14.1 requires merged-configuration validation. These components are not
on the server yet, so a fixture cannot `include:` them; tests/pipelines/
render_component.py substitutes the inputs locally and GitLab compiles the
result. The evidence label that earns is `gitlab-linted`, and nothing above it:
no pipeline has run.

The first two tests are the positive controls that make the rest mean something.
One pins GitLab's own input substitution against the renderer, so a renderer
that quietly diverged would fail here rather than in a consumer's pipeline. The
other sends configuration GitLab must reject, so a client that returned
`valid: true` for everything could not pass.

Needs GITLAB_TOKEN with api scope, and GITLAB_URL/GITLAB_PROJECT_ID to point
at your own GitLab; see README.md. The suite
skips without one, because a missing credential is not evidence of a broken
component.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
TEMPLATES = REPO_ROOT / "templates"
BUILDKIT = TEMPLATES / "container-build-buildkit" / "template.yml"
KO = TEMPLATES / "container-build-ko" / "template.yml"
JIB = TEMPLATES / "container-build-jib" / "template.yml"
SMOKE = TEMPLATES / "container-smoke-test" / "template.yml"

REPOSITORY = "registry.example.com/dev/platform/demo-app"
ALWAYS = [{"if": '$CI_PIPELINE_SOURCE == "push"'}]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_module = load("ci_lint", "lint.py")

# render_component moved to tools/resolve/ so the lint harness and the local
# runner share one implementation; `load()` still reaches lint.py beside this file.
from tools.resolve import render_component as render  # noqa: E402

needs_token = pytest.mark.skipif(
    not os.environ.get("GITLAB_TOKEN"),
    reason="GITLAB_TOKEN is not set; see README.md, Running the tests",
)


def names(result: dict) -> list[str]:
    return lint_module.job_names(result)


# --- positive controls -------------------------------------------------------


@needs_token
def test_gitlab_substitutes_inputs_the_way_the_renderer_does():
    """Pin the three substitution rules render_component.py implements.

    GitLab performs the substitution for a real consumer; the renderer performs
    it here. If the two ever disagree, every other test in this module is
    measuring the wrong thing, so this check comes first.
    """
    probe = """
spec:
  inputs:
    name: {type: string, default: 'api'}
    flag: {type: boolean, default: false}
    items: {type: array, default: ['one', 'two']}
    producers: {type: array, default: ['upstream-b']}
---
upstream-a:
  stage: build
  script: ['true']
upstream-b:
  stage: build
  script: ['true']
probe:
  stage: test
  variables:
    WHOLE: '$[[ inputs.name ]]'
  script:
    - 'echo bool=[$[[ inputs.flag ]]]'
    - 'echo array=$[[ inputs.items ]]'
    - 'echo string=$[[ inputs.name ]]:tag'
  needs:
    - job: 'upstream-a'
      artifacts: true
    - $[[ inputs.producers ]]
"""
    result = lint_module.lint(probe, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    probe_job = next(job for job in result["jobs"] if job["name"] == "probe")
    assert probe_job["script"] == [
        "echo bool=[false]",
        'echo array=["one", "two"]',
        "echo string=api:tag",
    ]

    rendered = render.substitute(
        {
            "script": [
                "echo bool=[$[[ inputs.flag ]]]",
                "echo array=$[[ inputs.items ]]",
                "echo string=$[[ inputs.name ]]:tag",
            ],
            "needs": [
                {"job": "upstream-a", "artifacts": True},
                "$[[ inputs.producers ]]",
            ],
            "variables": {"WHOLE": "$[[ inputs.name ]]"},
        },
        {"name": "api", "flag": False, "items": ["one", "two"], "producers": ["upstream-b"]},
    )
    assert rendered["script"] == probe_job["script"]
    assert rendered["needs"] == [
        {"job": "upstream-a", "artifacts": True},
        "upstream-b",
    ]
    assert rendered["variables"]["WHOLE"] == "api"


@needs_token
def test_the_lint_client_rejects_broken_configuration():
    result = lint_module.lint(
        "thing:\n  stage: not-a-declared-stage\n  script:\n    - true\n",
        include_jobs=True,
        dry_run=True,
    )
    assert not result["valid"]
    assert result.get("errors")


# --- one instance, defaults --------------------------------------------------


@needs_token
def test_a_single_buildkit_instance_with_defaults_lints_and_emits_its_job():
    config = render.compose(
        ["build"],
        render.render(BUILDKIT, instance="api", **{"image-repository": REPOSITORY}),
    )
    result = lint_module.lint(config, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    assert names(result) == ["api:container-build-buildkit"]


@needs_token
def test_a_single_ko_instance_with_defaults_lints_and_emits_its_job():
    config = render.compose(
        ["build"],
        render.render(KO, instance="api", **{"image-repository": REPOSITORY}),
    )
    result = lint_module.lint(config, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    assert names(result) == ["api:container-build-ko"]


@needs_token
def test_a_single_jib_instance_with_defaults_lints_and_emits_its_job():
    config = render.compose(
        ["build"],
        render.render(JIB, instance="api", **{"image-repository": REPOSITORY}),
    )
    result = lint_module.lint(config, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    assert names(result) == ["api:container-build-jib"]


@needs_token
def test_the_smoke_test_with_defaults_compiles_but_is_not_created_off_a_tag():
    """Its default run-rules require a semver tag, and the lint simulates a
    default-branch push, so a valid compile that creates no job is the correct
    result here. The job name is asserted by the two tests below, which supply
    rules that fire."""
    config = render.compose(
        ["build", "test"],
        render.render(BUILDKIT, instance="api", **{"image-repository": REPOSITORY}),
        render.render(
            SMOKE,
            instance="api",
            **{
                "build-job": "api:container-build-buildkit",
                "image-identities": [
                    "API_IMAGE=.ci-artifacts/api/container-build-buildkit/image.json"
                ],
            },
        ),
    )
    result = lint_module.lint(config, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    assert names(result) == ["api:container-build-buildkit"]


# --- two instances ------------------------------------------------------------


@needs_token
@pytest.mark.parametrize(
    "template, component",
    [
        (BUILDKIT, "container-build-buildkit"),
        (KO, "container-build-ko"),
        (JIB, "container-build-jib"),
    ],
    ids=lambda value: value if isinstance(value, str) else value.parent.name,
)
def test_two_instances_emit_distinct_job_names(template: Path, component: str):
    config = render.compose(
        ["build"],
        render.render(template, instance="api", **{"image-repository": f"{REPOSITORY}-api"}),
        render.render(template, instance="frontend", **{"image-repository": f"{REPOSITORY}-web"}),
    )
    result = lint_module.lint(config, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    assert sorted(names(result)) == sorted(
        [f"api:{component}", f"frontend:{component}"]
    )


@needs_token
def test_two_smoke_test_instances_emit_distinct_job_names():
    jobs = {}
    for instance in ("api", "frontend"):
        jobs.update(
            render.render(
                BUILDKIT,
                instance=instance,
                **{"image-repository": f"{REPOSITORY}-{instance}", "run-rules": ALWAYS},
            )
        )
        jobs.update(
            render.render(
                SMOKE,
                instance=instance,
                **{
                    "build-job": f"{instance}:container-build-buildkit",
                    "image-identities": [
                        f"APP_IMAGE=.ci-artifacts/{instance}/container-build-buildkit/image.json"
                    ],
                    "run-rules": ALWAYS,
                },
            )
        )
    result = lint_module.lint(
        render.compose(["build", "test"], jobs), include_jobs=True, dry_run=True
    )
    assert result["valid"], result.get("errors")
    assert sorted(names(result)) == [
        "api:container-build-buildkit",
        "api:container-smoke-test",
        "frontend:container-build-buildkit",
        "frontend:container-smoke-test",
    ]


# --- the producer the smoke test consumes ------------------------------------


@needs_token
def test_the_smoke_test_lints_with_its_producer_and_its_service_producers():
    jobs = {}
    jobs.update(
        render.render(
            BUILDKIT, instance="api", **{"image-repository": REPOSITORY, "run-rules": ALWAYS}
        )
    )
    jobs.update(
        render.render(
            BUILDKIT,
            instance="db",
            **{"image-repository": f"{REPOSITORY}-db", "run-rules": ALWAYS},
        )
    )
    jobs.update(
        render.render(
            SMOKE,
            instance="api",
            **{
                "build-job": "api:container-build-buildkit",
                "service-image-jobs": ["db:container-build-buildkit"],
                "image-identities": [
                    "API_IMAGE=.ci-artifacts/api/container-build-buildkit/image.json",
                    "DB_IMAGE=.ci-artifacts/db/container-build-buildkit/image.json",
                ],
                "run-rules": ALWAYS,
            },
        )
    )
    result = lint_module.lint(
        render.compose(["build", "test"], jobs), include_jobs=True, dry_run=True
    )
    assert result["valid"], result.get("errors")
    assert sorted(names(result)) == [
        "api:container-build-buildkit",
        "api:container-smoke-test",
        "db:container-build-buildkit",
    ]


@needs_token
def test_the_smoke_test_runs_against_an_image_it_did_not_build():
    """Coupling: the subject may be named rather than produced here."""
    reference = f"{REPOSITORY}@sha256:" + "ab" * 32
    jobs = render.render(
        SMOKE,
        instance="api",
        **{
            "subject-reference": reference,
            "image-identities": [
                "API_IMAGE=.ci-artifacts/api/container-smoke-test/image.json"
            ],
            "run-rules": ALWAYS,
        },
    )
    assert jobs["api:container-smoke-test"]["needs"] == []
    script = "\n".join(jobs["api:container-smoke-test"]["before_script"])
    assert "ci_tpl_resolve_identity_file" in script

    result = lint_module.lint(
        render.compose(["build", "test"], jobs), include_jobs=True, dry_run=True
    )
    assert result["valid"], result.get("errors")
    assert names(result) == ["api:container-smoke-test"]


@needs_token
def test_the_smoke_test_fails_lint_when_its_producer_is_absent():
    """Section 8.10: the missing-producer case is validated before release. The
    old smoke-test template took its image from a free-text variable, so a
    pipeline with no build at all still produced a green test job."""
    config = render.compose(
        ["build", "test"],
        render.render(
            SMOKE,
            instance="api",
            **{
                "build-job": "api:container-build-buildkit",
                "image-identities": [
                    "API_IMAGE=.ci-artifacts/api/container-build-buildkit/image.json"
                ],
                "run-rules": ALWAYS,
            },
        ),
    )
    result = lint_module.lint(config, include_jobs=True, dry_run=True)
    assert not result["valid"]
    assert any("undefined need" in message for message in result["errors"]), result["errors"]


@needs_token
def test_the_smoke_test_fails_lint_when_a_service_producer_is_absent():
    jobs = {}
    jobs.update(
        render.render(
            BUILDKIT, instance="api", **{"image-repository": REPOSITORY, "run-rules": ALWAYS}
        )
    )
    jobs.update(
        render.render(
            SMOKE,
            instance="api",
            **{
                "build-job": "api:container-build-buildkit",
                "service-image-jobs": ["db:container-build-buildkit"],
                "image-identities": [
                    "API_IMAGE=.ci-artifacts/api/container-build-buildkit/image.json"
                ],
                "run-rules": ALWAYS,
            },
        )
    )
    result = lint_module.lint(
        render.compose(["build", "test"], jobs), include_jobs=True, dry_run=True
    )
    assert not result["valid"]
    assert any("undefined need" in message for message in result["errors"]), result["errors"]


# --- the two GitLab behaviours the components are shaped around ---------------


@needs_token
def test_gitlab_rejects_a_bare_array_in_a_job_variable():
    """Why the components write `json $[[ inputs.tags ]]` rather than the
    placeholder alone. A placeholder that is the whole value substitutes the
    array itself, and a CI variable cannot hold a list. Written as a longer
    string it renders as a JSON array literal, which the runtime helper reads."""
    bare = """
spec:
  inputs:
    tags: {type: array, default: ['1.2.3']}
---
j:
  stage: build
  variables:
    CI_TPL_TAGS: '$[[ inputs.tags ]]'
  script: ['true']
"""
    result = lint_module.lint(bare, include_jobs=True, dry_run=True)
    assert not result["valid"]
    assert any("variable definition" in message for message in result["errors"]), result[
        "errors"
    ]

    embedded = bare.replace("'$[[ inputs.tags ]]'", "'json $[[ inputs.tags ]]'")
    result = lint_module.lint(embedded, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")


@needs_token
def test_gitlab_accepts_a_boolean_input_as_a_job_variable():
    """push-candidate is a boolean and reaches the shell as a variable the script
    compares against the string 'true'."""
    config = """
spec:
  inputs:
    push: {type: boolean, default: true}
---
j:
  stage: build
  variables:
    CI_TPL_PUSH: '$[[ inputs.push ]]'
  script:
    - '[ "$CI_TPL_PUSH" = "true" ]'
"""
    result = lint_module.lint(config, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")


# --- resource groups ---------------------------------------------------------


@needs_token
@pytest.mark.parametrize("template", [BUILDKIT, KO, JIB])
def test_a_builder_carries_no_resource_group_by_default(template: Path):
    """The empty default has to compile, or every existing consumer breaks on
    the upgrade. What an empty key does at pipeline creation is not something a
    lint can answer: a consumer pipeline answered it."""
    rendered = render.render(template, instance="api", **{"image-repository": REPOSITORY})
    job = next(iter(rendered.values()))
    assert job["resource_group"] == ""
    config = render.compose(["build"], rendered)
    result = lint_module.lint(config, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    assert "resource_group: ''" in result["merged_yaml"], result["merged_yaml"]


@needs_token
@pytest.mark.parametrize("template", [BUILDKIT, KO, JIB])
def test_a_builder_serialises_on_the_group_it_is_given(template: Path):
    config = render.compose(
        ["build"],
        render.render(
            template,
            instance="api",
            **{
                "image-repository": REPOSITORY,
                "resource-group": "runner-images-factory",
            },
        ),
    )
    result = lint_module.lint(config, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    assert "resource_group: runner-images-factory" in result["merged_yaml"]


# --- build secrets -----------------------------------------------------------


@needs_token
def test_buildkit_accepts_build_secrets_and_keeps_them_out_of_the_build_args():
    config = render.compose(
        ["build"],
        render.render(
            BUILDKIT,
            instance="api",
            **{
                "image-repository": REPOSITORY,
                "build-args": ["BASE_REF=registry.example/base:1"],
                "build-secrets": ["cert=ENTITLEMENT_PEM"],
            },
        ),
    )
    result = lint_module.lint(config, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    merged = result["merged_yaml"]
    assert 'CI_TPL_BUILD_SECRETS: json ["cert=ENTITLEMENT_PEM"]' in merged
    assert "build-arg:cert" not in merged


# --- ordering: a stage barrier by default, edges only when asked (C12) -------


def two_image_config(*, base_rules: list | None = None) -> str:
    """base builds, child builds FROM what base pushed."""
    return render.compose(
        ["build"],
        render.render(
            BUILDKIT,
            instance="base",
            **{
                "image-repository": REPOSITORY,
                "run-rules": base_rules if base_rules is not None else ALWAYS,
            },
        ),
        render.render(
            BUILDKIT,
            instance="child",
            **{
                "image-repository": REPOSITORY,
                "run-rules": ALWAYS,
                "upstream-image-job": "base:container-build-buildkit",
            },
        ),
    )


@pytest.mark.parametrize("template", [BUILDKIT, KO, JIB])
def test_a_builder_asked_for_nothing_keeps_its_stage_barrier(template: Path):
    """Section 8.6, and the reason the choice is made on an `include:` rather
    than in the job body: a component cannot drop a key, so a builder that
    built `needs:` from its inputs carried `needs:` for every consumer, empty
    list and all, and lost the stage barrier for the ones that asked for
    nothing."""
    job = render.render(template, instance="api", **{"image-repository": REPOSITORY})[
        f"api:{template.parent.name}"
    ]
    assert job["dependencies"] == []
    assert "needs" not in job


@pytest.mark.parametrize("template", [BUILDKIT, KO, JIB])
def test_gate_jobs_turn_the_barrier_into_edges(template: Path):
    """Section 8.5: the two keys are never both present."""
    gates = [{"job": "api:security-secrets-gitleaks", "artifacts": False}]
    job = render.render(
        template,
        instance="api",
        **{
            "image-repository": REPOSITORY,
            "gate-jobs": gates,
            "gate-jobs-set": True,
        },
    )[f"api:{template.parent.name}"]
    assert job["needs"] == gates
    assert "dependencies" not in job


@pytest.mark.parametrize("template", [BUILDKIT, KO, JIB])
def test_an_upstream_image_job_becomes_an_artifact_edge(template: Path):
    """The edge has to download the parent's image.json, so `artifacts: true`,
    and it comes first because it is the producer rather than a gate."""
    gates = [{"job": "api:security-secrets-gitleaks", "artifacts": False}]
    job = render.render(
        template,
        instance="api",
        **{
            "image-repository": REPOSITORY,
            "gate-jobs": gates,
            "gate-jobs-set": True,
            "upstream-image-job": "base:container-build-buildkit",
        },
    )[f"api:{template.parent.name}"]
    assert job["needs"] == [
        {"job": "base:container-build-buildkit", "artifacts": True}
    ] + gates
    assert "dependencies" not in job


@pytest.mark.parametrize("template", [BUILDKIT, KO, JIB])
def test_an_upstream_image_job_alone_is_enough(template: Path):
    """gate-jobs-set stays false: the upstream input selects the edges on its
    own, because a string input can be tested for emptiness in a rule where an
    array input cannot."""
    job = render.render(
        template,
        instance="api",
        **{
            "image-repository": REPOSITORY,
            "upstream-image-job": "base:container-build-buildkit",
        },
    )[f"api:{template.parent.name}"]
    assert job["needs"] == [{"job": "base:container-build-buildkit", "artifacts": True}]
    assert "dependencies" not in job


@needs_token
@pytest.mark.parametrize("template", [BUILDKIT, KO, JIB])
def test_a_builder_at_its_defaults_lints_with_the_barrier(template: Path):
    config = render.compose(
        ["build"], render.render(template, instance="api", **{"image-repository": REPOSITORY})
    )
    result = lint_module.lint(config, include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    # The parsed job, not a substring of the document: every builder embeds
    # runtime/build/gate_inputs.sh, whose comments discuss the very keys this
    # asserts over, so a text search answers a question about prose.
    job = yaml.safe_load(result["merged_yaml"])[f"api:{template.parent.name}"]
    assert "needs" not in job
    assert job["dependencies"] == []


@needs_token
def test_a_child_build_can_name_the_parent_build_it_starts_from():
    result = lint_module.lint(two_image_config(), include_jobs=True, dry_run=True)
    assert result["valid"], result.get("errors")
    assert names(result) == [
        "base:container-build-buildkit",
        "child:container-build-buildkit",
    ]
    merged = result["merged_yaml"]
    assert "CI_TPL_UPSTREAM_IMAGE_JOB: base:container-build-buildkit" in merged
    assert "CI_TPL_UPSTREAM_BUILD_ARG: BASE_REF" in merged


@needs_token
def test_a_parent_filtered_out_by_its_own_rules_fails_pipeline_creation():
    """The limitation upstream-image-job carries, measured rather than quoted.
    The edge it creates is not `optional: true`, so a parent a rule can remove
    from the pipeline cannot be named here; such a consumer keeps its own
    ordering in gate-jobs and passes the reference another way."""
    never = [{"if": '$THIS_VARIABLE_IS_NEVER_SET == "yes"'}]
    result = lint_module.lint(
        two_image_config(base_rules=never), include_jobs=True, dry_run=True
    )
    assert not result["valid"]
    assert any("does not exist in the pipeline" in error for error in result["errors"]), result[
        "errors"
    ]
    assert any("needs:optional" in error for error in result["errors"])
