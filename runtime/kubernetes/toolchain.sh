#!/bin/sh
# Shared shell for the helm and kubernetes validation components.
#
# It defines functions only; sourcing it runs nothing. The components embed it
# verbatim as the last before_script entry, so the functions are in scope for
# the script block that follows. tests/runtime/test_template_script_lockstep.py
# proves the embedded copy is byte-identical to this file.
#
# Why embedded rather than called as runtime/kubernetes/toolchain.sh: section 12
# of docs/gitlab-ci-agent-standard.md - `include:` imports YAML, not files, so a
# consumer job never has this repository checked out. The upgrade path is an
# execution image built from images/kubernetes/ shipping these tools already
# installed; until one exists and is published, the job fetches them.
#
# List-shaped inputs arrive as newline-separated strings in CI_TPL_ variables,
# because a GitLab `array` input cannot be interpolated into a variable value -
# the CI Lint API rejects it with "variable definition must be either a string
# or a hash". Every loop below sets IFS to a newline so a path may contain
# spaces, and skips empty lines.
set -e

# A newline, built rather than written. A literal newline here would be a
# continuation line, and this file is embedded into a YAML block scalar where
# every line picks up the block's indentation - the value would be a newline
# followed by six spaces, and IFS would then split on spaces too.
CI_TPL_NEWLINE=$(printf '\nx')
CI_TPL_NEWLINE=${CI_TPL_NEWLINE%x}

# Fetch a URL to a file. curl where present (alpine/helm has it), busybox wget
# otherwise (alpine has no curl). Both fail non-zero on an HTTP error.
ci_tpl_fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 -o "$2" "$1"
  else
    wget -q -O "$2" "$1"
  fi
}

# Install a pinned release asset, verified twice: the release's checksum file
# must match a SHA-256 pinned in the component, and the asset must match the
# line for it inside that checksum file. Pinning only the asset hash would need
# one pin per architecture; pinning the checksum file covers every asset in the
# release with one constant. Section 12 requires an independently verified
# checksum for an unavoidable runtime download, and a failure on any mismatch.
#
# ci_tpl_install_tool <name> <asset-url> <checksums-url> <checksums-sha256> \
#                     <asset-filename> <archive-member> <bin-dir>
ci_tpl_install_tool() {
  ci_tpl_tool_name=$1
  ci_tpl_asset_url=$2
  ci_tpl_checksums_url=$3
  ci_tpl_checksums_sha256=$4
  ci_tpl_asset_name=$5
  ci_tpl_member=$6
  ci_tpl_bin_dir=$7

  ci_tpl_work=$(mktemp -d)
  ci_tpl_fetch "$ci_tpl_checksums_url" "$ci_tpl_work/CHECKSUMS"
  if ! echo "${ci_tpl_checksums_sha256}  ${ci_tpl_work}/CHECKSUMS" | sha256sum -c - >/dev/null 2>&1; then
    echo "ERROR: the ${ci_tpl_tool_name} checksum file does not match its pinned SHA-256" >&2
    echo "ERROR: expected ${ci_tpl_checksums_sha256}, got $(sha256sum "${ci_tpl_work}/CHECKSUMS" | cut -d' ' -f1)" >&2
    return 1
  fi

  # The published format is "<sha256>  <filename>", sometimes "*<filename>".
  ci_tpl_asset_sha256=$(awk -v n="$ci_tpl_asset_name" \
    '$2 == n || $2 == "*" n {print $1}' "$ci_tpl_work/CHECKSUMS")
  if [ -z "$ci_tpl_asset_sha256" ]; then
    echo "ERROR: ${ci_tpl_asset_name} is not listed in the ${ci_tpl_tool_name} checksum file" >&2
    return 1
  fi

  ci_tpl_fetch "$ci_tpl_asset_url" "$ci_tpl_work/$ci_tpl_asset_name"
  if ! echo "${ci_tpl_asset_sha256}  ${ci_tpl_work}/${ci_tpl_asset_name}" | sha256sum -c - >/dev/null 2>&1; then
    echo "ERROR: downloaded ${ci_tpl_asset_name} does not match its published checksum" >&2
    return 1
  fi

  mkdir -p "$ci_tpl_bin_dir"
  tar -xzf "$ci_tpl_work/$ci_tpl_asset_name" -C "$ci_tpl_bin_dir" "$ci_tpl_member"
  chmod +x "$ci_tpl_bin_dir/$ci_tpl_member"
  rm -rf "$ci_tpl_work"
  echo "installed ${ci_tpl_tool_name} to ${ci_tpl_bin_dir}/${ci_tpl_member}"
}

# Fail when a path resolves outside the checkout. These paths come from
# component inputs; a ../ segment or a symlink must not let one of them read or
# write outside CI_PROJECT_DIR.
ci_tpl_require_inside_checkout() {
  ci_tpl_root=$(cd "$CI_PROJECT_DIR" && pwd -P)
  if ! ci_tpl_parent=$(cd "$(dirname -- "$1")" 2>/dev/null && pwd -P); then
    echo "ERROR: '$1' has no existing parent directory" >&2
    return 1
  fi
  ci_tpl_target="${ci_tpl_parent%/}/$(basename -- "$1")"
  case "$ci_tpl_target" in
    "$ci_tpl_root" | "$ci_tpl_root"/*) ;;
    *)
      echo "ERROR: '$1' resolves to '$ci_tpl_target', outside the checkout" >&2
      return 1
      ;;
  esac
}

# Run kubeconform over one rendered manifest file. It exits 0 for a clean run,
# 1 for a validation failure and other codes for execution errors; every
# non-zero outcome fails the job, which is what section 10.1 requires. Missing
# schemas are rejected unless the component was explicitly configured to
# tolerate them: a CRD-backed resource with no schema is an unvalidated
# resource, not a passing one.
ci_tpl_kubeconform() {
  ci_tpl_manifest=$1
  set -- -strict -summary -output text \
    -kubernetes-version "$CI_TPL_KUBERNETES_VERSION"
  if [ "${CI_TPL_IGNORE_MISSING_SCHEMAS:-false}" = "true" ]; then
    set -- "$@" -ignore-missing-schemas
  fi
  ci_tpl_saved_ifs=$IFS
  IFS=$CI_TPL_NEWLINE
  for ci_tpl_location in ${CI_TPL_SCHEMA_LOCATIONS:-}; do
    [ -n "$ci_tpl_location" ] || continue
    set -- "$@" -schema-location "$ci_tpl_location"
  done
  IFS=$ci_tpl_saved_ifs
  echo "+ kubeconform $* ${ci_tpl_manifest}"
  kubeconform "$@" "$ci_tpl_manifest"
}
