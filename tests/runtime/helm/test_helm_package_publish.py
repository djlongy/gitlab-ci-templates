"""helm-package-publish: the five defects in build/helm-oci.yml, and chart.json.

The script under test is the copy embedded in
templates/helm-package-publish/template.yml. helm is stubbed by a script that
branches on the subcommand, because one run calls it six times and each call
needs its own answer.
"""

from __future__ import annotations

import json

import pytest

TOOLCHAIN = "runtime/kubernetes/toolchain.sh"
PUBLISH = "runtime/helm/package_publish.sh"
TEMPLATE = "helm-package-publish"

DIGEST = "sha256:" + "ab" * 32

# Behaviour is steered by environment variables so a test can name the outcome
# it needs. Defaults: the version does not exist yet, the push reports DIGEST.
HELM_STUB = """#!/bin/sh
printf 'helm %s\\n' "$*" >>"$CALL_LOG"
case "$1" in
  show)
    case "$3" in
      oci://*) exit "${HELM_OCI_SHOW_EXIT:-1}" ;;
      *) printf 'apiVersion: v2\\nname: %s\\nversion: %s\\n' \\
           "${HELM_NAME:-demo}" "${HELM_VERSION:-1.0.0}" ;;
    esac ;;
  dependency) exit "${HELM_DEPENDENCY_EXIT:-0}" ;;
  registry) exit "${HELM_LOGIN_EXIT:-0}" ;;
  package)
    destination=$4
    mkdir -p "$destination"
    archive="$destination/${HELM_NAME:-demo}-${HELM_VERSION:-1.0.0}.tgz"
    printf 'chart-bytes' >"$archive"
    # A second archive from an earlier run, to catch a `ls *.tgz | head -1`
    # style selection: it sorts first and is not the chart just packaged.
    printf 'stale-bytes' >"$destination/aaa-old-0.0.1.tgz"
    [ "${HELM_PACKAGE_SILENT:-false}" = "true" ] && exit 0
    printf 'Successfully packaged chart and saved it to: %s\\n' "$archive" ;;
  push)
    [ -n "${HELM_PUSH_OUTPUT-unset}" ] || true
    if [ "${HELM_PUSH_SILENT:-false}" != "true" ]; then
      printf 'Pushed: registry.invalid/charts/demo:1.0.0\\nDigest: %s\\n' \\
        "${HELM_PUSH_DIGEST:-$DEFAULT_DIGEST}" >&2
    fi
    exit "${HELM_PUSH_EXIT:-0}" ;;
esac
"""


@pytest.fixture
def publish(harness, chart):
    def _publish(*, chart_path: str = "charts/app",
                 repository: str = "registry.invalid/charts", **settings):
        harness.write_stub("helm", HELM_STUB)
        harness.env.update({
            "DEFAULT_DIGEST": DIGEST,
            "HARBOR_USER": "robot",
            "HARBOR_PASSWORD": "secret",
            "CI_COMMIT_SHA": "0" * 40,
            "CI_PIPELINE_ID": "4242",
            "CI_JOB_ID": "8484",
            "CI_TPL_CHART_PATH": chart_path,
            "CI_TPL_CHART_REPOSITORY": repository,
            "CI_TPL_REGISTRY_USERNAME_VARIABLE": "HARBOR_USER",
            "CI_TPL_REGISTRY_PASSWORD_VARIABLE": "HARBOR_PASSWORD",
            "CI_TPL_ALLOW_OVERWRITE": "false",
            "CI_TPL_ARTIFACT_DIR": str(
                harness.project_dir / ".ci-artifacts/app/helm-package-publish"
            ),
            **settings,
        })
        return harness.run(TEMPLATE, TOOLCHAIN, PUBLISH)

    return _publish


def chart_json(harness) -> dict:
    path = harness.project_dir / ".ci-artifacts/app/helm-package-publish/chart.json"
    return json.loads(path.read_text())


def test_a_publish_records_the_registry_digest_in_chart_json(harness, chart, publish):
    chart("charts/app")

    result = publish()

    assert result.returncode == 0, result.output
    record = chart_json(harness)
    assert record["schema_version"] == 1
    assert record["repository"] == "registry.invalid/charts/demo"
    assert record["name"] == "demo"
    assert record["version"] == "1.0.0"
    assert record["digest"] == DIGEST
    assert record["reference"] == f"registry.invalid/charts/demo@{DIGEST}"
    assert record["archive"] == "demo-1.0.0.tgz"
    assert record["source_commit"] == "0" * 40
    assert record["pipeline_id"] == "4242"
    assert record["job_id"] == "8484"
    assert record["created_at"].endswith("Z")


def test_the_archive_pushed_is_the_one_just_packaged(harness, chart, publish):
    """Defect 2. `ls *.tgz | head -1` would pick aaa-old-0.0.1.tgz, which the

    stub leaves in the same directory precisely to catch that.
    """
    chart("charts/app")

    result = publish()

    assert result.returncode == 0, result.output
    assert result.called("helm push")
    assert any("demo-1.0.0.tgz" in call and call.startswith("helm push")
               for call in result.calls)
    assert not any("aaa-old-0.0.1.tgz" in call for call in result.calls)


def test_a_silent_package_fails_rather_than_pushing_something_else(chart, publish):
    chart("charts/app")

    result = publish(HELM_PACKAGE_SILENT="true")

    assert result.returncode != 0
    assert "did not report an archive path" in result.output
    assert not result.called("helm push")


def test_missing_credentials_fail_before_the_registry_is_touched(chart, publish):
    """Defect 3. The template this replaces skipped the login when the variable

    was unset and let the push run unauthenticated. Section 10.1: credentials
    unavailable is a failure.
    """
    chart("charts/app")

    result = publish(HARBOR_PASSWORD="")

    assert result.returncode != 0
    assert "registry credentials are unavailable" in result.output
    assert not result.called("helm registry login")
    assert not result.called("helm push")


def test_credentials_are_read_by_variable_name(harness, chart, publish):
    chart("charts/app")

    result = publish(
        CI_TPL_REGISTRY_USERNAME_VARIABLE="CHART_USER",
        CI_TPL_REGISTRY_PASSWORD_VARIABLE="CHART_TOKEN",
        CHART_USER="other-robot",
        CHART_TOKEN="other-secret",
    )

    assert result.returncode == 0, result.output
    assert result.called("--username other-robot")
    assert not result.called("other-secret"), "the password reached the command line"


def test_an_existing_version_is_refused(chart, publish):
    """Defect 4. A published chart version is immutable."""
    chart("charts/app")

    result = publish(HELM_OCI_SHOW_EXIT="0")

    assert result.returncode != 0
    assert "already exists" in result.output
    assert not result.called("helm push")


def test_an_existing_version_is_republished_only_when_allowed(chart, publish):
    chart("charts/app")

    result = publish(HELM_OCI_SHOW_EXIT="0", CI_TPL_ALLOW_OVERWRITE="true")

    assert result.returncode == 0, result.output
    assert "WARNING: overwriting existing" in result.output
    assert result.called("helm push")


def test_a_push_reporting_no_digest_fails(harness, chart, publish):
    """A publication whose identity cannot be read is unverified, not done."""
    chart("charts/app")

    result = publish(HELM_PUSH_SILENT="true")

    assert result.returncode != 0
    assert "no sha256 digest" in result.output
    assert not (
        harness.project_dir / ".ci-artifacts/app/helm-package-publish/chart.json"
    ).exists()


def test_a_truncated_digest_fails(chart, publish):
    chart("charts/app")

    result = publish(HELM_PUSH_DIGEST="sha256:abc123")

    assert result.returncode != 0
    assert "is not a sha256" in result.output


def test_a_failed_push_fails_the_job(chart, publish):
    chart("charts/app")

    result = publish(HELM_PUSH_EXIT="1")

    assert result.returncode != 0


def test_dependencies_without_a_lock_file_fail(chart, publish):
    chart("charts/app", dependencies=True, lock=False)

    result = publish()

    assert result.returncode != 0
    assert "has no Chart.lock" in result.output
    assert not result.called("helm push")


def test_a_dependency_build_failure_fails_the_job(chart, publish):
    """Defect 1. The template this replaces suppressed this command's status."""
    chart("charts/app", dependencies=True, lock=True)

    result = publish(HELM_DEPENDENCY_EXIT="1")

    assert result.returncode != 0
    assert not result.called("helm push")


def test_a_repository_without_a_project_path_is_refused(chart, publish):
    """Defect 5, the shape half: chart-repository is an OCI namespace, and

    helm would otherwise push into the registry root.
    """
    chart("charts/app")

    result = publish(repository="registry.invalid")

    assert result.returncode != 0
    assert "has no project path" in result.output


def test_a_chart_path_escaping_the_checkout_fails(publish):
    result = publish(chart_path="../elsewhere")

    assert result.returncode != 0
    assert "outside the checkout" in result.output


def test_a_chart_json_field_that_came_out_empty_fails_the_job(harness, chart, publish):
    """The producer validates its own output before success (section 9.4).

    An empty CI_COMMIT_SHA is the realistic case: the record would name a
    publication with no source identity and still look like valid JSON.
    """
    chart("charts/app")

    result = publish(CI_COMMIT_SHA="")

    assert result.returncode != 0
    assert "empty field" in result.output
