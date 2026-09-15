"""kubernetes-validate: rendering, and the failures that must not pass.

The script under test is the copy embedded in
templates/kubernetes-validate/template.yml.
"""

from __future__ import annotations

import pytest

TOOLCHAIN = "runtime/kubernetes/toolchain.sh"
VALIDATE = "runtime/kubernetes/validate.sh"
TEMPLATE = "kubernetes-validate"

MANIFEST = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: app\n"


@pytest.fixture
def validate(harness):
    def _validate(paths: str, *, kustomize_exit: int = 0, kustomize_stdout: str = MANIFEST,
                  kubeconform_exit: int = 0, **settings):
        harness.stub("kustomize", exit_code=kustomize_exit, stdout=kustomize_stdout)
        harness.stub("kubeconform", exit_code=kubeconform_exit)
        harness.env.update({
            "CI_TPL_MANIFEST_PATHS": paths,
            "CI_TPL_KUBERNETES_VERSION": "1.31.0",
            "CI_TPL_IGNORE_MISSING_SCHEMAS": "false",
            "CI_TPL_SCHEMA_LOCATIONS": "",
            "CI_TPL_ARTIFACT_DIR": str(
                harness.project_dir / ".ci-artifacts/app/kubernetes-validate"
            ),
            **settings,
        })
        return harness.run(TEMPLATE, TOOLCHAIN, VALIDATE)

    return _validate


def test_a_directory_of_plain_manifests_is_validated(harness, validate):
    harness.file("apps/system/one.yaml", MANIFEST)
    harness.file("apps/system/two.yml", MANIFEST)

    result = validate("apps/system")

    assert result.returncode == 0, result.output
    assert "collected 2 file(s)" in result.stdout
    rendered = harness.project_dir / ".ci-artifacts/app/kubernetes-validate/apps-system.rendered.yaml"
    assert rendered.read_text().count("kind: ConfigMap") == 2


def test_a_kustomization_is_rendered_with_kustomize(harness, validate):
    harness.file("overlays/prod/kustomization.yaml", "resources:\n  - app.yaml\n")
    harness.file("overlays/prod/app.yaml", MANIFEST)

    result = validate("overlays/prod")

    assert result.returncode == 0, result.output
    assert result.called("kustomize build overlays/prod")


def test_a_failed_kustomize_render_is_not_hidden_by_the_pipe(harness, validate):
    """`kustomize build | kubeconform` reports only kubeconform, so a broken

    overlay validates clean. The render goes to a file and kustomize's exit
    code is checked first.
    """
    harness.file("overlays/prod/kustomization.yaml", "resources:\n  - missing.yaml\n")

    result = validate("overlays/prod", kustomize_exit=1)

    assert result.returncode != 0
    assert not result.called("kubeconform")


def test_a_single_file_is_validated(harness, validate):
    harness.file("apps/one.yaml", MANIFEST)

    result = validate("apps/one.yaml")

    assert result.returncode == 0, result.output
    assert "1 path(s) rendered and schema-validated" in result.stdout


def test_a_schema_violation_fails_the_job(harness, validate):
    harness.file("apps/one.yaml", MANIFEST)

    result = validate("apps/one.yaml", kubeconform_exit=1)

    assert result.returncode != 0


def test_a_missing_path_fails(harness, validate):
    (harness.project_dir / "apps").mkdir()

    result = validate("apps/absent")

    assert result.returncode != 0
    assert "does not exist" in result.output


def test_a_path_whose_parent_is_missing_fails_in_the_guard(validate):
    """The checkout guard resolves the parent, so it rejects this one first.

    Either way the job fails; the test exists so the two messages are not
    confused for one another when a run is being read.
    """
    result = validate("apps/absent")

    assert result.returncode != 0
    assert "has no existing parent directory" in result.output


def test_a_directory_holding_no_manifests_fails(harness, validate):
    (harness.project_dir / "apps/empty").mkdir(parents=True)

    result = validate("apps/empty")

    assert result.returncode != 0
    assert "contains no .yaml or .yml manifests" in result.output


def test_a_path_list_that_names_nothing_fails(validate):
    result = validate("\n \n")

    assert result.returncode != 0


def test_a_path_escaping_the_checkout_fails(validate):
    result = validate("../elsewhere")

    assert result.returncode != 0
    assert "outside the checkout" in result.output


def test_every_listed_path_is_validated(harness, validate):
    harness.file("apps/one/a.yaml", MANIFEST)
    harness.file("apps/two/b.yaml", MANIFEST)

    result = validate("apps/one\napps/two")

    assert result.returncode == 0, result.output
    assert "2 path(s) rendered and schema-validated" in result.stdout


def test_an_empty_kustomize_render_fails_rather_than_validating_nothing(harness, validate):
    harness.file("overlays/prod/kustomization.yaml", "resources: []\n")

    result = validate("overlays/prod", kustomize_stdout="")

    assert result.returncode != 0
    assert "rendered nothing" in result.output
    assert not result.called("kubeconform")


def test_missing_schemas_are_rejected_by_default(harness, validate):
    harness.file("apps/one.yaml", MANIFEST)

    result = validate("apps/one.yaml")

    assert result.returncode == 0, result.output
    assert not any("-ignore-missing-schemas" in call for call in result.calls)
