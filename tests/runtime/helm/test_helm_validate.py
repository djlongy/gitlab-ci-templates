"""helm-validate: what fails, and what must not pass.

Each defect the audit recorded against the pipelines this replaces has a test
named after it here. The script under test is the copy embedded in
templates/helm-validate/template.yml.
"""

from __future__ import annotations

import pytest

TOOLCHAIN = "runtime/kubernetes/toolchain.sh"
VALIDATE = "runtime/helm/validate.sh"
TEMPLATE = "helm-validate"

# helm's stdout for every call. The manifest keeps the render file non-empty;
# `helm show chart` reads name/version off the same text.
HELM_STDOUT = "name: demo\nversion: 1.0.0\napiVersion: v1\nkind: ConfigMap\n"


@pytest.fixture
def validate(harness, chart):
    """Run helm-validate over `paths` with helm and kubeconform stubbed."""

    def _validate(paths: str, *, values: str = "", helm_exits: dict | None = None,
                  kubeconform_exit: int = 0, **settings):
        harness.stub("helm", stdout=HELM_STDOUT, subcommand_exits=helm_exits or {})
        harness.stub("kubeconform", exit_code=kubeconform_exit)
        harness.env.update({
            "CI_TPL_CHART_PATHS": paths,
            "CI_TPL_VALUES_FILES": values,
            "CI_TPL_RELEASE_NAME": "validate",
            "CI_TPL_KUBERNETES_VERSION": "1.31.0",
            "CI_TPL_IGNORE_MISSING_SCHEMAS": "false",
            "CI_TPL_SCHEMA_LOCATIONS": "",
            "CI_TPL_ARTIFACT_DIR": str(harness.project_dir / ".ci-artifacts/app/helm-validate"),
            **settings,
        })
        return harness.run(TEMPLATE, TOOLCHAIN, VALIDATE)

    return _validate


def test_a_clean_chart_passes_and_leaves_the_render_as_evidence(harness, chart, validate):
    chart("charts/app")

    result = validate("charts/app")

    assert result.returncode == 0, result.output
    assert "1 chart(s) linted, rendered and schema-validated" in result.stdout
    rendered = harness.project_dir / ".ci-artifacts/app/helm-validate/charts-app.rendered.yaml"
    assert rendered.is_file() and rendered.read_text().strip()


def test_a_failed_render_is_not_hidden_by_the_pipe(harness, chart, validate):
    """Defect 1. `helm template ... | kubeconform` reports only kubeconform.

    With a passing kubeconform and a failing helm, the pipeline form exits 0 and
    a chart that does not render at all is reported as valid.
    """
    chart("charts/app")

    result = validate("charts/app", helm_exits={"template": 1}, kubeconform_exit=0)

    assert result.returncode != 0
    assert not result.called("kubeconform"), \
        "kubeconform ran on a render that helm failed to produce"


def test_a_schema_violation_fails_the_job(chart, validate):
    chart("charts/app")

    result = validate("charts/app", kubeconform_exit=1)

    assert result.returncode != 0


def test_a_lint_failure_fails_before_rendering(chart, validate):
    chart("charts/app")

    result = validate("charts/app", helm_exits={"lint": 1})

    assert result.returncode != 0
    assert not result.called("helm template")


def test_dependencies_without_a_lock_file_fail(chart, validate):
    """Defect 2, first half. The chart pins nothing, so CI would validate

    whatever versions resolved at that moment rather than what the repository
    describes.
    """
    chart("charts/app", dependencies=True, lock=False)

    result = validate("charts/app")

    assert result.returncode != 0
    assert "has no Chart.lock" in result.output
    assert not result.called("dependency build")


def test_a_dependency_build_failure_fails_the_job(chart, validate):
    """Defect 2, second half. The template this replaces suppressed the exit

    status of the same command, so an unresolvable dependency was a silent skip.
    """
    chart("charts/app", dependencies=True, lock=True)

    result = validate("charts/app", helm_exits={"dependency": 1})

    assert result.returncode != 0
    assert not result.called("helm template")


def test_missing_schemas_are_rejected_by_default(chart, validate):
    """Defect 3. -ignore-missing-schemas was unconditional in the pipeline this

    replaces, which passed every CRD-backed resource without looking at it.
    """
    chart("charts/app")

    result = validate("charts/app")

    assert result.returncode == 0, result.output
    assert not any("-ignore-missing-schemas" in call for call in result.calls)


def test_missing_schemas_are_tolerated_only_when_asked_for(chart, validate):
    chart("charts/app")

    result = validate("charts/app", CI_TPL_IGNORE_MISSING_SCHEMAS="true")

    assert result.returncode == 0, result.output
    assert any("-ignore-missing-schemas" in call for call in result.calls)


def test_a_chart_set_that_names_nothing_fails(validate):
    """Defect 4. A skipped chart let a run validate nothing and still exit 0."""
    result = validate("\n\n\n")

    assert result.returncode != 0
    assert "nothing was validated" in result.output


def test_a_path_that_is_not_a_chart_fails(harness, validate):
    harness.file("charts/app/values.yaml", "replicas: 1\n")

    result = validate("charts/app")

    assert result.returncode != 0
    assert "no Chart.yaml" in result.output


def test_a_chart_path_escaping_the_checkout_fails(validate):
    result = validate("../elsewhere")

    assert result.returncode != 0
    assert "outside the checkout" in result.output


def test_each_values_file_adds_a_pass_on_top_of_the_defaults_pass(harness, chart, validate):
    """A chart with per-environment values is validated once per environment,

    and once with its own values, which is what platform/demo-app does by hand.
    """
    chart("charts/app")
    harness.file("charts/app/values-staging.yaml", "env: staging\n")
    harness.file("charts/app/values-prod.yaml", "env: prod\n")

    result = validate(
        "charts/app", values="charts/app/values-staging.yaml\ncharts/app/values-prod.yaml"
    )

    assert result.returncode == 0, result.output
    assert sum("helm lint" in call for call in result.calls) == 3
    assert result.called("--values charts/app/values-prod.yaml")
    renders = sorted(
        p.name for p in (harness.project_dir / ".ci-artifacts/app/helm-validate").iterdir()
    )
    assert renders == [
        "charts-app--charts-app-values-prod.yaml.rendered.yaml",
        "charts-app--charts-app-values-staging.yaml.rendered.yaml",
        "charts-app.rendered.yaml",
    ]


def test_a_missing_values_file_fails(chart, validate):
    chart("charts/app")

    result = validate("charts/app", values="charts/app/values-absent.yaml")

    assert result.returncode != 0
    assert "does not exist" in result.output


def test_several_charts_are_all_validated(chart, validate):
    chart("charts/one", name="one")
    chart("charts/two", name="two")

    result = validate("charts/one\ncharts/two")

    assert result.returncode == 0, result.output
    assert "2 chart(s) linted" in result.stdout


def test_a_chart_path_containing_a_space_is_not_split(chart, validate):
    """The list separator is a newline, not whitespace, so this must be one path."""
    chart("charts/my app")

    result = validate("charts/my app")

    assert result.returncode == 0, result.output
    assert "1 chart(s) linted" in result.stdout


def test_schema_locations_are_passed_through_one_flag_each(chart, validate):
    chart("charts/app")

    result = validate(
        "charts/app",
        CI_TPL_SCHEMA_LOCATIONS="https://crds.invalid/{{.ResourceKind}}.json\n/opt/schemas",
    )

    assert result.returncode == 0, result.output
    assert result.called(
        "-schema-location https://crds.invalid/{{.ResourceKind}}.json "
        "-schema-location /opt/schemas"
    )


def test_an_empty_render_fails_rather_than_validating_nothing(harness, chart, validate):
    harness.stub("helm", stdout="")
    harness.stub("kubeconform")
    chart("charts/app")
    harness.env.update({
        "CI_TPL_CHART_PATHS": "charts/app",
        "CI_TPL_VALUES_FILES": "",
        "CI_TPL_RELEASE_NAME": "validate",
        "CI_TPL_KUBERNETES_VERSION": "1.31.0",
        "CI_TPL_IGNORE_MISSING_SCHEMAS": "false",
        "CI_TPL_SCHEMA_LOCATIONS": "",
        "CI_TPL_ARTIFACT_DIR": str(harness.project_dir / ".ci-artifacts/app/helm-validate"),
    })

    result = harness.run(TEMPLATE, TOOLCHAIN, VALIDATE)

    assert result.returncode != 0
    assert "rendered no resources" in result.output
    assert not result.called("kubeconform")
