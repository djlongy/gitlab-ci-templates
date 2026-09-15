#!/bin/sh
# helm-validate: lint every chart, render it, and validate the rendered
# resources against the target Kubernetes schema.
#
# Embedded verbatim as the script block of templates/helm-validate/template.yml;
# tests/runtime/test_template_script_lockstep.py proves the two copies match.
# Requires the functions from runtime/kubernetes/toolchain.sh.
#
# Four defects in the pipelines this replaces are fixed here, each with a test
# named after it in tests/runtime/helm/test_helm_validate.py:
#
#  1. `helm template ... | kubeconform` (platform/demo-app) hides a helm failure:
#     without pipefail the pipeline reports kubeconform's status, so a chart
#     that does not render at all validates clean. This renders to a file and
#     checks helm's own exit code before anything reads the file.
#  2. build/helm-oci.yml and platform/platform-gitops suppress the exit status of
#     `helm dependency update` and `helm repo add`, turning a missing
#     dependency into a silent skip. Section 11.4 forbids it; failing to
#     resolve a dependency is a validation failure.
#  3. `-ignore-missing-schemas` unconditionally (platform/demo-app) passes every
#     CRD-backed resource without looking at it. It is off by default here and
#     is an input the consumer has to set deliberately.
#  4. platform/platform-gitops and platform/cluster-gitops skip a chart whose pull failed
#     and still exit 0, so a run can validate nothing and pass. An empty chart
#     set, or a path that is not a chart, fails here.
set -e

: "${CI_TPL_CHART_PATHS:?chart-paths is required}"
: "${CI_TPL_KUBERNETES_VERSION:?kubernetes-version is required}"
: "${CI_TPL_RELEASE_NAME:?release-name is required}"
: "${CI_TPL_ARTIFACT_DIR:?artifact directory is required}"

ci_tpl_slug() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '-'
}

# Lint, render and schema-validate one chart with one optional values file.
# An empty second argument means the chart's own values.
ci_tpl_validate_pass() {
  ci_tpl_chart=$1
  ci_tpl_values=$2

  if [ -n "$ci_tpl_values" ]; then
    ci_tpl_require_inside_checkout "$ci_tpl_values"
    if [ ! -f "$ci_tpl_values" ]; then
      echo "ERROR: values file '${ci_tpl_values}' does not exist" >&2
      return 1
    fi
    ci_tpl_label="$(ci_tpl_slug "$ci_tpl_chart")--$(ci_tpl_slug "$ci_tpl_values")"
    set -- --values "$ci_tpl_values"
  else
    ci_tpl_label=$(ci_tpl_slug "$ci_tpl_chart")
    set --
  fi

  echo "=== helm lint ${ci_tpl_chart} ${ci_tpl_values} ==="
  helm lint "$ci_tpl_chart" "$@"

  # Rendered to a file, not piped: a pipeline reports only the last command's
  # status, so piping helm into kubeconform discards helm's exit code.
  ci_tpl_render="${CI_TPL_ARTIFACT_DIR}/${ci_tpl_label}.rendered.yaml"
  echo "=== helm template ${ci_tpl_chart} ${ci_tpl_values} ==="
  helm template "$CI_TPL_RELEASE_NAME" "$ci_tpl_chart" "$@" >"$ci_tpl_render"
  if [ ! -s "$ci_tpl_render" ]; then
    echo "ERROR: '${ci_tpl_chart}' rendered no resources; there is nothing to validate" >&2
    return 1
  fi

  ci_tpl_kubeconform "$ci_tpl_render"
}

ci_tpl_validate_chart() {
  ci_tpl_this_chart=$1

  ci_tpl_require_inside_checkout "$ci_tpl_this_chart"
  if [ ! -f "$ci_tpl_this_chart/Chart.yaml" ]; then
    echo "ERROR: '${ci_tpl_this_chart}' is not a chart directory: no Chart.yaml" >&2
    return 1
  fi

  # Dependencies must resolve. `build` installs the versions in Chart.lock, so
  # CI validates what the repository pins; `update` would re-resolve them and
  # validate something the repository does not describe.
  if grep -q '^dependencies:' "$ci_tpl_this_chart/Chart.yaml"; then
    if [ ! -f "$ci_tpl_this_chart/Chart.lock" ]; then
      echo "ERROR: '${ci_tpl_this_chart}' declares dependencies but has no Chart.lock." >&2
      echo "ERROR: run 'helm dependency update' and commit the lock file, so CI" >&2
      echo "ERROR: validates the dependency versions the repository pins." >&2
      return 1
    fi
    echo "=== helm dependency build ${ci_tpl_this_chart} ==="
    helm dependency build "$ci_tpl_this_chart"
  fi

  ci_tpl_validate_pass "$ci_tpl_this_chart" ''

  # Split the values list into this function's own positional parameters.
  # Newline-only splitting keeps a path containing spaces intact; `set -f`
  # stops a path with a glob character expanding against the file system.
  ci_tpl_saved_ifs=$IFS
  IFS=$CI_TPL_NEWLINE
  set -f
  # shellcheck disable=SC2086
  set -- ${CI_TPL_VALUES_FILES:-}
  IFS=$ci_tpl_saved_ifs
  set +f
  for ci_tpl_values_path in "$@"; do
    [ -n "$ci_tpl_values_path" ] || continue
    ci_tpl_validate_pass "$ci_tpl_this_chart" "$ci_tpl_values_path"
  done
}

mkdir -p "$CI_TPL_ARTIFACT_DIR"
ci_tpl_charts_seen=0

ci_tpl_saved_ifs=$IFS
IFS=$CI_TPL_NEWLINE
set -f
# shellcheck disable=SC2086
set -- $CI_TPL_CHART_PATHS
IFS=$ci_tpl_saved_ifs
set +f

for ci_tpl_chart_path in "$@"; do
  [ -n "$ci_tpl_chart_path" ] || continue
  ci_tpl_validate_chart "$ci_tpl_chart_path"
  ci_tpl_charts_seen=$((ci_tpl_charts_seen + 1))
done

# A run that validated nothing is not a passing run. Section 10.1: missing
# evidence fails. Same failure mode as an audit that scans zero repositories
# and reports success.
if [ "$ci_tpl_charts_seen" -eq 0 ]; then
  echo "ERROR: chart-paths listed no charts; nothing was validated" >&2
  exit 1
fi
echo "helm-validate: ${ci_tpl_charts_seen} chart(s) linted, rendered and schema-validated"
