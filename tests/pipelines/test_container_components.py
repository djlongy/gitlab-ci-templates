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
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
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
render = load("render_component", "render_component.py")

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
