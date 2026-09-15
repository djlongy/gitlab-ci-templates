"""Merged-configuration lint for the helm and kubernetes components.

Section 14.1 requires merged-config validation through the CI Lint API: a local
YAML parse cannot say whether GitLab compiles the result or which jobs it
creates. A lint is not an execution, and nothing here runs a pipeline.

Two paths are used, for the reason tests/pipelines/render.py explains:

  * the template file is posted as-is, so GitLab applies the spec defaults and
    does its own interpolation - this is the single-instance-with-defaults case;
  * fixtures rendered locally cover two instances of one component, and a
    component consuming a producer, which `include: local:` cannot reach while
    the templates are only on this branch.

The two are cross-checked: GitLab's own interpolation and render()'s must agree
on the job name for the same inputs, or the fixtures below prove nothing about
the real component.

The static checks at the end need no token and always run. They are the ones
that keep a pinned digest or a pinned checksum from quietly becoming a tag.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
CONSUMER_FIXTURE = FIXTURES / "helm-chart-release.consumer.gitlab-ci.yml"
COMPONENTS = ["helm-validate", "kubernetes-validate", "helm-package-publish"]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parent / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_module = load("ci_lint", "lint.py")
render_module = load("ci_render", "render.py")

needs_token = pytest.mark.skipif(
    not os.environ.get("GITLAB_TOKEN"),
    reason="GITLAB_TOKEN is not set; see README.md, Running the tests",
)


def template_text(component: str) -> str:
    return (REPO_ROOT / "templates" / component / "template.yml").read_text()


# The inputs each component requires. A spec header need not give these a
# default, so a lint of the template on its own has to supply them.
REQUIRED_INPUTS = {
    "helm-validate": {"chart-paths": "deploy/chart"},
    "kubernetes-validate": {"manifest-paths": "apps/system"},
    "helm-package-publish": {
        "chart-path": "deploy/chart",
        "chart-repository": "registry.example.com/charts",
    },
}


def lintable_template(component: str, instance: str) -> str:
    """The template itself, with required inputs defaulted and stages declared.

    Posting the file lets GitLab apply the spec defaults and do its own
    interpolation, which is what makes this the single-instance evidence rather
    than a test of the local renderer. `stages:` is added because a template
    must not declare one (section 6.1) and the lint has no composition to
    supply it.
    """
    header, _, body = template_text(component).partition("\n---\n")
    document = yaml.safe_load(header)
    document["spec"]["inputs"]["instance"]["default"] = instance
    for name, value in REQUIRED_INPUTS[component].items():
        document["spec"]["inputs"][name]["default"] = value
    return (
        yaml.safe_dump(document, sort_keys=False, width=10_000)
        + "---\n"
        + yaml.safe_dump({"stages": ["verify", "publish"]})
        + body
    )


# --------------------------------------------------------------------------
# Linted against the real server
# --------------------------------------------------------------------------


@pytest.mark.parametrize("component", COMPONENTS)
@needs_token
def test_one_instance_with_defaults_lints_and_emits_its_job(component):
    """GitLab applies the spec defaults and interpolates; we read the job list."""
    result = lint_module.lint(
        lintable_template(component, "app"), include_jobs=True, dry_run=False
    )

    assert result["valid"], result.get("errors")
    assert f"app:{component}" in lint_module.job_names(result), \
        lint_module.job_names(result)


@pytest.mark.parametrize("component", COMPONENTS)
@needs_token
def test_two_instances_emit_distinct_job_names(component, tmp_path):
    """Section 8.10: repeat inclusion is part of the tested contract."""
    overrides = {
        "helm-validate": [
            {"instance": "api", "chart-paths": "charts/api"},
            {"instance": "web", "chart-paths": "charts/web"},
        ],
        "kubernetes-validate": [
            {"instance": "api", "manifest-paths": "apps/api"},
            {"instance": "web", "manifest-paths": "apps/web"},
        ],
        "helm-package-publish": [
            {"instance": "api", "chart-path": "charts/api",
             "chart-repository": "registry.example.com/charts"},
            {"instance": "web", "chart-path": "charts/web",
             "chart-repository": "registry.example.com/charts"},
        ],
    }[component]
    stages = ["verify", "publish"]
    content = render_module.merged_pipeline(
        [(component, overrides[0]), (component, overrides[1])], stages
    )

    result = lint_module.lint(content, include_jobs=True, dry_run=False)

    assert result["valid"], result.get("errors")
    names = lint_module.job_names(result)
    assert f"api:{component}" in names and f"web:{component}" in names, names


@needs_token
def test_a_verify_and_publish_chain_lints_as_one_pipeline():
    """The shape a chart repository actually uses: validate, then publish.

    helm-package-publish declares no `needs`, so the stage barrier is what puts
    it after the validation. The lint proves the stages resolve; only a run
    would prove the ordering, and no run has happened.
    """
    content = render_module.merged_pipeline(
        [
            ("helm-validate", {"instance": "app", "chart-paths": "deploy/chart"}),
            ("kubernetes-validate", {"instance": "app", "manifest-paths": "deploy/manifests"}),
            ("helm-package-publish", {
                "instance": "app",
                "chart-path": "deploy/chart",
                "chart-repository": "registry.example.com/charts",
                "run-rules": [{"if": '$CI_PIPELINE_SOURCE == "push"'}],
            }),
        ],
        ["verify", "publish"],
    )
    result = lint_module.lint(content, include_jobs=True, dry_run=False)

    assert result["valid"], result.get("errors")
    assert set(lint_module.job_names(result)) == {
        "app:helm-validate", "app:kubernetes-validate", "app:helm-package-publish"
    }


@needs_token
def test_a_job_in_a_stage_the_pipeline_does_not_declare_is_rejected():
    """Positive control for the lints above.

    A client that returned `valid: true` for everything would make every test
    in this file pass. The publish component defaults to stage `publish`; a
    pipeline that declares only `verify` must be rejected.
    """
    content = render_module.merged_pipeline(
        [("helm-package-publish", {
            "instance": "app",
            "chart-path": "deploy/chart",
            "chart-repository": "registry.example.com/charts",
        })],
        ["verify"],
    )

    result = lint_module.lint(content, include_jobs=True, dry_run=False)

    assert not result["valid"]
    assert result.get("errors")


@pytest.mark.parametrize("component", COMPONENTS)
@needs_token
def test_the_local_renderer_agrees_with_gitlab_on_the_defaults(component):
    """Cross-check: the fixtures are only evidence if render() matches GitLab.

    Both are given the same inputs; both must name the job the same way.
    """
    inputs = {"instance": "xcheck", **REQUIRED_INPUTS[component]}

    from_gitlab = lint_module.job_names(
        lint_module.lint(
            lintable_template(component, "xcheck"), include_jobs=True, dry_run=False
        )
    )
    from_render = list(render_module.render(component, inputs))

    assert from_gitlab == from_render == [f"xcheck:{component}"]


# --------------------------------------------------------------------------
# Static checks on the shipped templates; no token, always run
# --------------------------------------------------------------------------


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_template_parses_as_a_spec_header_and_one_job(component):
    documents = list(
        yaml.safe_load_all(template_text(component))
    )
    assert len(documents) == 2
    assert list(documents[0]) == ["spec"]
    assert list(documents[1]) == [f"$[[ inputs.instance ]]:{component}"]


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_job_carries_no_success_masking(component):
    """No `|| true`, no allow_failure, no unconditional `exit 0` (section 10.1).

    The whole file is searched, comments included, because the fan-in check
    greps it the same way - a defect quoted in a comment reads as the defect.
    """
    text = template_text(component)
    assert "|| true" not in text
    assert "allow_failure: true" not in text
    assert "\n      exit 0\n" not in text
    job = list(yaml.safe_load_all(text))[1][f"$[[ inputs.instance ]]:{component}"]
    assert job["allow_failure"] is False


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_job_declares_dependencies_rather_than_an_empty_needs(component):
    """Section 8.6 and 8.7: a source-only job avoids incidental artifact

    downloads without `needs: []`, which would let it start before its stage.
    """
    job = list(yaml.safe_load_all(template_text(component)))[1][
        f"$[[ inputs.instance ]]:{component}"
    ]
    assert job["dependencies"] == []
    assert "needs" not in job


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_execution_image_default_is_a_digest(component):
    spec = yaml.safe_load(template_text(component).split("\n---\n")[0])["spec"]["inputs"]
    default = spec["execution-image"]["default"]
    assert "@sha256:" in default and ":latest" not in default
    assert len(default.split("@sha256:")[1]) == 64


@pytest.mark.parametrize("component", COMPONENTS)
def test_artifacts_are_namespaced_by_instance_and_component(component):
    job = list(yaml.safe_load_all(template_text(component)))[1][
        f"$[[ inputs.instance ]]:{component}"
    ]
    expected = f".ci-artifacts/$[[ inputs.instance ]]/{component}/"
    assert job["artifacts"]["paths"] == [expected]
    assert "$[[ inputs.instance ]]" in job["artifacts"]["name"]
    assert component in job["artifacts"]["name"]


def test_every_runtime_download_is_pinned_by_a_checksum():
    """Section 12. A pinned version with no checksum is not a pinned download.

    The pins live as literal arguments in the template, not in `variables:`,
    because a higher-precedence project or group CI variable outranks a job
    variable and could otherwise redirect the fetch.
    """
    expected = {
        "helm-validate": {
            "kubeconform": "5946c6300e94d5f43c877cb41ff7117c63130f1710ce20f04fd721f99fb0485e",
        },
        "kubernetes-validate": {
            "kubeconform": "5946c6300e94d5f43c877cb41ff7117c63130f1710ce20f04fd721f99fb0485e",
            "kustomize": "5070120f7ec21a36d213ebea62f405901e5af5a77597f32355f78963dc0910c2",
        },
    }
    for component, tools in expected.items():
        text = template_text(component)
        for tool, checksum in tools.items():
            assert f"ci_tpl_install_tool {tool} " in text, f"{component} no longer installs {tool}"
            assert f"'{checksum}'" in text, f"{component}: {tool} checksum pin changed"
        installs = text.count("ci_tpl_install_tool ") - text.count("# ci_tpl_install_tool ")
        assert installs == len(tools), \
            f"{component} installs a tool this test does not pin"


def test_the_publish_component_does_not_default_to_running_on_a_branch():
    """Section 6.1: never default a mutation to unconditional execution.

    build/helm-oci.yml published on every default-branch push and on any tag.
    """
    spec = yaml.safe_load(
        template_text("helm-package-publish").split("\n---\n")[0]
    )["spec"]["inputs"]
    rules = spec["run-rules"]["default"]
    assert rules == [{"if": '$CI_COMMIT_TAG && $CI_COMMIT_REF_PROTECTED == "true"'}]


def test_the_publish_component_ships_no_estate_registry_default():
    """Section 4: the registry and project are estate facts, not component code."""
    spec = yaml.safe_load(
        template_text("helm-package-publish").split("\n---\n")[0]
    )["spec"]["inputs"]
    assert "default" not in spec["chart-repository"]
    assert "registry.example.com/charts" not in template_text("helm-package-publish")


@pytest.mark.parametrize("component", COMPONENTS)
def test_no_secret_is_accepted_as_an_input(component):
    """Section 6.1: inputs take secret NAMES, never values."""
    spec = yaml.safe_load(template_text(component).split("\n---\n")[0])["spec"]["inputs"]
    for name, definition in spec.items():
        assert not any(
            word in name for word in ("password", "token", "secret", "key")
        ) or name.endswith("-variable"), f"{component}: input '{name}' looks like a secret"
        assert "hvs." not in str((definition or {}).get("default", ""))


def test_the_consumer_fixture_matches_the_inputs_the_components_declare():
    """The documented consumer shape for a chart repository.

    It is parsed, not linted: it pins `include: project ... ref:` and no
    release tag carries these components yet, so a lint would fail on a missing
    file rather than on anything this fixture says. Section 14.2 forbids
    pinning `main` for a production consumer, and .ci/estate.yml records
    shared_ci.approved_ref as unresolved for that reason. The lint evidence for
    these components comes from the rendered pipelines above.
    """
    consumer = yaml.safe_load(CONSUMER_FIXTURE.read_text())
    for include in consumer["include"]:
        component = Path(include["file"]).parent.name
        assert component in COMPONENTS
        spec = yaml.safe_load(template_text(component).split("\n---\n")[0])["spec"]["inputs"]
        unknown = set(include["inputs"]) - set(spec)
        assert not unknown, f"{component}: fixture sets inputs that do not exist: {unknown}"
        required = {n for n, d in spec.items() if "default" not in (d or {})}
        assert required <= set(include["inputs"]), \
            f"{component}: fixture omits required inputs {required - set(include['inputs'])}"


def test_the_publish_component_defaults_to_the_publish_stage():
    """Section 7.1. build/helm-oci.yml ran a publication in stage `build`."""
    spec = yaml.safe_load(
        template_text("helm-package-publish").split("\n---\n")[0]
    )["spec"]["inputs"]
    assert spec["stage"]["default"] == "publish"


def test_the_settings_that_decide_the_outcome_are_not_job_variables():
    """Section 6.1: a reserved CI_TPL_ name can be overridden by a

    higher-precedence project or group CI variable. These four decide where the
    chart goes, which credentials are used and whether an existing version may
    be overwritten, so the job assigns them in the shell from the interpolated
    input instead of reading them from `variables:`.
    """
    text = template_text("helm-package-publish")
    job = list(yaml.safe_load_all(text))[1][
        "$[[ inputs.instance ]]:helm-package-publish"
    ]
    guarded = [
        "CI_TPL_CHART_REPOSITORY",
        "CI_TPL_REGISTRY_USERNAME_VARIABLE",
        "CI_TPL_REGISTRY_PASSWORD_VARIABLE",
        "CI_TPL_ALLOW_OVERWRITE",
    ]
    for name in guarded:
        assert name not in job["variables"], f"{name} is overridable as a job variable"
        assert f"{name}='$[[ inputs." in text, f"{name} is never assigned from its input"


@pytest.mark.parametrize("component", ["helm-validate", "kubernetes-validate"])
def test_the_schema_policy_is_not_a_job_variable(component):
    text = template_text(component)
    job = list(yaml.safe_load_all(text))[1][f"$[[ inputs.instance ]]:{component}"]
    for name in ["CI_TPL_KUBERNETES_VERSION", "CI_TPL_IGNORE_MISSING_SCHEMAS"]:
        assert name not in job["variables"], f"{name} is overridable as a job variable"
        assert f"{name}='$[[ inputs." in text
