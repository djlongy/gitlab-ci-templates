#!/bin/sh
# helm-package-publish: package one chart, push it to an OCI registry, and
# record the immutable identity of what was pushed in chart.json.
#
# Embedded verbatim as the script block of
# templates/helm-package-publish/template.yml;
# tests/runtime/test_template_script_lockstep.py proves the two copies match.
#
# Defects in build/helm-oci.yml this replaces, each with a test named after it
# in tests/runtime/helm/test_helm_package_publish.py:
#
#  1. It suppressed the exit status of `helm dependency update`, publishing a
#     chart whose dependencies failed to resolve. Section 11.4 forbids hiding
#     a packaging failure. Here `helm dependency build` runs against the
#     committed Chart.lock and its exit code stands.
#  2. `CHART_FILE=$(ls *.tgz | head -1)` pushes whatever archive happens to sort
#     first in the working directory. Here the archive is written to a
#     dedicated directory and its path is read from the line `helm package`
#     prints on stdout, so the chart pushed is the chart just packaged.
#  3. Registry credentials were optional: `if [ -n "$HARBOR_USER" ] ... login`
#     meant an unset variable skipped the login and the push then ran
#     unauthenticated. Section 10.1: credentials unavailable is a failure.
#  4. The push overwrote an existing chart version silently. The version is
#     checked in the registry first and an existing one is refused unless the
#     caller explicitly allowed the overwrite.
#  5. The registry and project were job-level `variables:`, which outrank a
#     consumer's top-level `variables:`, so the documented override never took
#     effect and two estate facts were baked into shared code. They are inputs
#     now, with no default.
set -e

: "${CI_TPL_CHART_PATH:?chart-path is required}"
: "${CI_TPL_CHART_REPOSITORY:?chart-repository is required}"
: "${CI_TPL_REGISTRY_USERNAME_VARIABLE:?registry-username-variable is required}"
: "${CI_TPL_REGISTRY_PASSWORD_VARIABLE:?registry-password-variable is required}"
: "${CI_TPL_ARTIFACT_DIR:?artifact directory is required}"

ci_tpl_require_inside_checkout "$CI_TPL_CHART_PATH"
if [ ! -f "$CI_TPL_CHART_PATH/Chart.yaml" ]; then
  echo "ERROR: '${CI_TPL_CHART_PATH}' is not a chart directory: no Chart.yaml" >&2
  exit 1
fi

# The registry host is the first path segment of the repository, and the chart
# is published under the rest of it. helm derives the final name from Chart.yaml,
# so chart-repository is the OCI namespace, never the full chart reference.
ci_tpl_registry=${CI_TPL_CHART_REPOSITORY%%/*}
if [ "$ci_tpl_registry" = "$CI_TPL_CHART_REPOSITORY" ]; then
  echo "ERROR: chart-repository '${CI_TPL_CHART_REPOSITORY}' has no project path." >&2
  echo "ERROR: expected <registry-host>/<project>, for example registry.example/charts." >&2
  exit 1
fi

# Read the credentials by variable NAME. printenv, not eval: section 6.1
# forbids constructing a command from an input. printenv exits non-zero for an
# unset name, which is a missing credential rather than a script error.
ci_tpl_user=$(printenv "$CI_TPL_REGISTRY_USERNAME_VARIABLE") || ci_tpl_user=''
ci_tpl_password=$(printenv "$CI_TPL_REGISTRY_PASSWORD_VARIABLE") || ci_tpl_password=''
if [ -z "$ci_tpl_user" ] || [ -z "$ci_tpl_password" ]; then
  echo "ERROR: registry credentials are unavailable." >&2
  echo "ERROR: ${CI_TPL_REGISTRY_USERNAME_VARIABLE} and ${CI_TPL_REGISTRY_PASSWORD_VARIABLE}" >&2
  echo "ERROR: must both be set as protected CI variables for this ref." >&2
  exit 1
fi

# Chart identity comes from Chart.yaml, which is also what helm uses to name
# the archive and the OCI tag.
ci_tpl_chart_meta=$(helm show chart "$CI_TPL_CHART_PATH")
ci_tpl_name=$(printf '%s\n' "$ci_tpl_chart_meta" | awk '$1 == "name:" {print $2; exit}')
ci_tpl_version=$(printf '%s\n' "$ci_tpl_chart_meta" | awk '$1 == "version:" {print $2; exit}')
if [ -z "$ci_tpl_name" ] || [ -z "$ci_tpl_version" ]; then
  echo "ERROR: could not read name and version from ${CI_TPL_CHART_PATH}/Chart.yaml" >&2
  exit 1
fi
echo "chart ${ci_tpl_name} version ${ci_tpl_version}"

if grep -q '^dependencies:' "$CI_TPL_CHART_PATH/Chart.yaml"; then
  if [ ! -f "$CI_TPL_CHART_PATH/Chart.lock" ]; then
    echo "ERROR: '${CI_TPL_CHART_PATH}' declares dependencies but has no Chart.lock." >&2
    echo "ERROR: run 'helm dependency update' and commit the lock file before publishing." >&2
    exit 1
  fi
  echo "=== helm dependency build ==="
  helm dependency build "$CI_TPL_CHART_PATH"
fi

# helm writes "Pushed:" and "Digest:" to stderr and the packaged path to
# stdout, measured against alpine/helm 3.16.3 on 2026/09/15. Redirecting the
# wrong stream is how a digest ends up empty, so both are captured
# deliberately and the result is checked rather than assumed.
echo "=== helm registry login ${ci_tpl_registry} ==="
printf '%s' "$ci_tpl_password" |
  helm registry login "$ci_tpl_registry" --username "$ci_tpl_user" --password-stdin

if helm show chart "oci://${CI_TPL_CHART_REPOSITORY}/${ci_tpl_name}" \
  --version "$ci_tpl_version" >/dev/null 2>&1; then
  if [ "${CI_TPL_ALLOW_OVERWRITE:-false}" != "true" ]; then
    echo "ERROR: ${CI_TPL_CHART_REPOSITORY}/${ci_tpl_name}:${ci_tpl_version} already exists." >&2
    echo "ERROR: a published chart version is immutable. Bump the version in Chart.yaml," >&2
    echo "ERROR: or set allow-overwrite for a registry where republishing is intended." >&2
    exit 1
  fi
  echo "WARNING: overwriting existing ${ci_tpl_name}:${ci_tpl_version}, allow-overwrite is set"
fi

mkdir -p "$CI_TPL_ARTIFACT_DIR"
echo "=== helm package ==="
ci_tpl_package_output=$(helm package "$CI_TPL_CHART_PATH" --destination "$CI_TPL_ARTIFACT_DIR")
echo "$ci_tpl_package_output"
ci_tpl_archive=$(printf '%s\n' "$ci_tpl_package_output" |
  sed -n 's/^Successfully packaged chart and saved it to: //p' | tail -1)
if [ -z "$ci_tpl_archive" ] || [ ! -f "$ci_tpl_archive" ]; then
  echo "ERROR: helm package did not report an archive path; nothing to publish" >&2
  exit 1
fi
echo "packaged ${ci_tpl_archive}"

echo "=== helm push ==="
ci_tpl_push_output=$(helm push "$ci_tpl_archive" "oci://${CI_TPL_CHART_REPOSITORY}" 2>&1)
echo "$ci_tpl_push_output"
ci_tpl_digest=$(printf '%s\n' "$ci_tpl_push_output" |
  sed -n 's/^Digest: //p' | tail -1)
case "$ci_tpl_digest" in
  sha256:*) ;;
  *)
    echo "ERROR: helm push reported no sha256 digest; the publication is unverified" >&2
    exit 1
    ;;
esac
if [ "${#ci_tpl_digest}" -ne 71 ]; then
  echo "ERROR: '${ci_tpl_digest}' is not a sha256:<64 hex> digest" >&2
  exit 1
fi

ci_tpl_archive_sha256=$(sha256sum "$ci_tpl_archive" | cut -d' ' -f1)
ci_tpl_reference="${CI_TPL_CHART_REPOSITORY}/${ci_tpl_name}@${ci_tpl_digest}"

# chart.json is the chart equivalent of image.json in section 9.1: the
# authoritative identity record a downstream job consumes, never a tag.
#
# printf rather than a heredoc: this file is embedded into a YAML block scalar,
# where every line gains the block's indentation, and an indented heredoc
# terminator never closes the heredoc.
printf '%s\n' \
  '{' \
  '  "schema_version": 1,' \
  "  \"repository\": \"${CI_TPL_CHART_REPOSITORY}/${ci_tpl_name}\"," \
  "  \"name\": \"${ci_tpl_name}\"," \
  "  \"version\": \"${ci_tpl_version}\"," \
  "  \"digest\": \"${ci_tpl_digest}\"," \
  "  \"reference\": \"${ci_tpl_reference}\"," \
  "  \"archive\": \"$(basename "$ci_tpl_archive")\"," \
  "  \"archive_sha256\": \"${ci_tpl_archive_sha256}\"," \
  "  \"source_commit\": \"${CI_COMMIT_SHA}\"," \
  "  \"pipeline_id\": \"${CI_PIPELINE_ID}\"," \
  "  \"job_id\": \"${CI_JOB_ID}\"," \
  "  \"created_at\": \"$(date -u '+%Y-%m-%dT%H:%M:%SZ')\"" \
  '}' >"${CI_TPL_ARTIFACT_DIR}/chart.json"

# The producer validates its own output before reporting success (section 9.4).
for ci_tpl_field in schema_version repository name version digest reference \
  archive archive_sha256 source_commit pipeline_id job_id created_at; do
  if ! grep -q "\"${ci_tpl_field}\":" "${CI_TPL_ARTIFACT_DIR}/chart.json"; then
    echo "ERROR: chart.json is missing '${ci_tpl_field}'" >&2
    exit 1
  fi
done
if grep -q '": ""' "${CI_TPL_ARTIFACT_DIR}/chart.json"; then
  echo "ERROR: chart.json contains an empty field:" >&2
  grep -n '": ""' "${CI_TPL_ARTIFACT_DIR}/chart.json" >&2
  exit 1
fi

echo "published ${ci_tpl_reference}"
cat "${CI_TPL_ARTIFACT_DIR}/chart.json"
