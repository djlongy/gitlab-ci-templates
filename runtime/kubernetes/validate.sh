#!/bin/sh
# kubernetes-validate: render every manifest path and validate the result
# against the target Kubernetes schema.
#
# Embedded verbatim as the script block of
# templates/kubernetes-validate/template.yml;
# tests/runtime/test_template_script_lockstep.py proves the two copies match.
# Requires the functions from runtime/kubernetes/toolchain.sh.
#
# A path holding kustomization.yaml is rendered with `kustomize build`; any
# other path is validated as written. The component installs kustomize in
# before_script with literal, pinned arguments, so no CI variable can point the
# job at a different binary.
#
# The gitops repositories this serves validate ArgoCD and Flux desired state.
# Section 11.4: rendered resources are validated against the target Kubernetes,
# API and CRD versions, and helm lint alone is not schema validation.
set -e

: "${CI_TPL_MANIFEST_PATHS:?manifest-paths is required}"
: "${CI_TPL_KUBERNETES_VERSION:?kubernetes-version is required}"
: "${CI_TPL_ARTIFACT_DIR:?artifact directory is required}"

ci_tpl_slug() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '-'
}

mkdir -p "$CI_TPL_ARTIFACT_DIR"
ci_tpl_paths_seen=0

# Split the path list into positional parameters. Newline-only splitting keeps
# a path containing spaces intact; `set -f` stops a path with a glob character
# expanding against the file system.
ci_tpl_saved_ifs=$IFS
IFS=$CI_TPL_NEWLINE
set -f
# shellcheck disable=SC2086
set -- $CI_TPL_MANIFEST_PATHS
IFS=$ci_tpl_saved_ifs
set +f

for ci_tpl_path in "$@"; do
  [ -n "$ci_tpl_path" ] || continue
  ci_tpl_require_inside_checkout "$ci_tpl_path"
  ci_tpl_render="${CI_TPL_ARTIFACT_DIR}/$(ci_tpl_slug "$ci_tpl_path").rendered.yaml"

  if [ -f "$ci_tpl_path/kustomization.yaml" ] || [ -f "$ci_tpl_path/kustomization.yml" ]; then
    # Rendered to a file, not piped: a pipeline reports only the last command's
    # status, so piping kustomize into kubeconform would discard kustomize's
    # exit code and validate a truncated render as clean.
    echo "=== kustomize build ${ci_tpl_path} ==="
    kustomize build "$ci_tpl_path" >"$ci_tpl_render"
  elif [ -d "$ci_tpl_path" ]; then
    echo "=== collect ${ci_tpl_path} ==="
    : >"$ci_tpl_render"
    ci_tpl_found=0
    for ci_tpl_file in "$ci_tpl_path"/*.yaml "$ci_tpl_path"/*.yml; do
      [ -f "$ci_tpl_file" ] || continue
      echo "---" >>"$ci_tpl_render"
      cat "$ci_tpl_file" >>"$ci_tpl_render"
      ci_tpl_found=$((ci_tpl_found + 1))
    done
    if [ "$ci_tpl_found" -eq 0 ]; then
      echo "ERROR: '${ci_tpl_path}' contains no .yaml or .yml manifests" >&2
      exit 1
    fi
    echo "collected ${ci_tpl_found} file(s)"
  elif [ -f "$ci_tpl_path" ]; then
    cp "$ci_tpl_path" "$ci_tpl_render"
  else
    echo "ERROR: manifest path '${ci_tpl_path}' does not exist" >&2
    exit 1
  fi

  if [ ! -s "$ci_tpl_render" ]; then
    echo "ERROR: '${ci_tpl_path}' rendered nothing; there is nothing to validate" >&2
    exit 1
  fi

  ci_tpl_kubeconform "$ci_tpl_render"
  ci_tpl_paths_seen=$((ci_tpl_paths_seen + 1))
done

# A run that validated nothing is not a passing run (section 10.1).
if [ "$ci_tpl_paths_seen" -eq 0 ]; then
  echo "ERROR: manifest-paths listed no paths; nothing was validated" >&2
  exit 1
fi
echo "kubernetes-validate: ${ci_tpl_paths_seen} path(s) rendered and schema-validated"
