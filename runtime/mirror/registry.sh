#!/bin/sh
# Shared shell for the container mirror and export components.
#
# It defines functions only; sourcing it runs nothing. Both components embed it
# verbatim as a before_script entry, so the functions are in scope for the
# entries that follow. tests/runtime/mirror/ drives the copy the TEMPLATE
# carries, not this file, because the embedding is where shell changes meaning.
#
# Why embedded rather than called as runtime/mirror/registry.sh: section 12 of
# docs/gitlab-ci-agent-standard.md. `include:` imports YAML, not files, so a
# consumer job never has this repository on disk, and cloning it back at job
# time would pin nothing. The upgrade path is an execution image under images/
# that ships skopeo, curl, python3 and the AWS CLI already installed.
#
# Credentials never reach a command line. curl reads its Vault token from a
# mode-0600 --config file, the Vault JWT is posted from a mode-0600 file, and the
# registry token reaches skopeo through a docker config file written by python
# from the environment. A process listing is readable by every process on the
# runner; an argv is not a private channel.
set -eu

# Where this library puts the files it must not leave behind.
CI_TPL_SECRET_DIR=$(mktemp -d)
chmod 700 "$CI_TPL_SECRET_DIR"

# Trust an additional PEM bundle, for a registry issued by an internal
# authority. Empty skips it. A bundle carrying no certificate is an error, not
# an empty success: the job would then fail later with a TLS error that names
# the registry rather than the bundle.
ci_tpl_trust_bundle() {
  if [ -z "${CI_TPL_CA_BUNDLE_URL:-}" ]; then
    return 0
  fi
  curl -sf "$CI_TPL_CA_BUNDLE_URL" -o /usr/local/share/ca-certificates/ci-tpl-bundle.crt
  _certs=$(grep -c 'BEGIN CERTIFICATE' /usr/local/share/ca-certificates/ci-tpl-bundle.crt) || _certs=0
  if [ "$_certs" -eq 0 ]; then
    echo "ERROR: ca-bundle-url served no certificate" >&2
    exit 1
  fi
  update-ca-certificates >/dev/null 2>&1
  echo "trusted: $_certs extra certificate(s)"
}

# Exchange the job's id_token for a Vault token. Called by both credential
# resolvers and safe to call twice: the export component reads two KV paths
# with one login.
ci_tpl_vault_login() {
  if [ -n "${CI_TPL_VAULT_TOKEN:-}" ]; then
    return 0
  fi
  _body=$CI_TPL_SECRET_DIR/login-request.json
  : > "$_body"
  chmod 600 "$_body"
  python3 -c 'import json, os, sys; json.dump({"jwt": os.environ["VAULT_JWT"], "role": os.environ["CI_TPL_VAULT_ROLE"]}, sys.stdout)' \
    > "$_body"
  curl -sf -X POST -H 'Content-Type: application/json' --data-binary "@$_body" \
    "$CI_TPL_VAULT_ADDR/v1/auth/jwt/login" > "$CI_TPL_SECRET_DIR/login.json"
  rm -f "$_body"
  CI_TPL_VAULT_TOKEN=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["auth"]["client_token"])' \
    "$CI_TPL_SECRET_DIR/login.json" 2>/dev/null) || CI_TPL_VAULT_TOKEN=
  rm -f "$CI_TPL_SECRET_DIR/login.json"
  if [ -z "$CI_TPL_VAULT_TOKEN" ]; then
    echo 'ERROR: Vault JWT login returned no token' >&2
    exit 1
  fi
  export CI_TPL_VAULT_TOKEN
  echo "Vault token obtained (length ${#CI_TPL_VAULT_TOKEN})"
}

# Read one KV v2 secret to $2. $1 is <mount>/<path>.
ci_tpl_vault_read() {
  _mount=${1%%/*}
  _path=${1#*/}
  _config=$CI_TPL_SECRET_DIR/curl.conf
  : > "$_config"
  chmod 600 "$_config"
  python3 -c 'import os, sys; sys.stdout.write("header = \"X-Vault-Token: %s\"\n" % os.environ["CI_TPL_VAULT_TOKEN"])' \
    > "$_config"
  curl -sf --config "$_config" "$CI_TPL_VAULT_ADDR/v1/$_mount/data/$_path" > "$2"
  rm -f "$_config"
  chmod 600 "$2"
}

# One field of a secret read by ci_tpl_vault_read. $1 is the file, $2 the field.
ci_tpl_vault_field() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["data"]["data"][sys.argv[2]])' "$1" "$2"
}

# Resolve the destination registry credential and leave skopeo able to use it.
#
# Two sources, and which one is in use is an input rather than a fallback: a
# fallback would turn a misconfigured Vault into an anonymous push attempt.
ci_tpl_registry_credentials() {
  if [ -n "${CI_TPL_VAULT_ADDR:-}" ]; then
    if [ -z "${CI_TPL_VAULT_ROLE:-}" ] || [ -z "${CI_TPL_VAULT_KV_PATH:-}" ]; then
      echo 'ERROR: vault-addr is set, so vault-role and vault-kv-path are required' >&2
      exit 1
    fi
    ci_tpl_vault_login
    ci_tpl_vault_read "$CI_TPL_VAULT_KV_PATH" "$CI_TPL_SECRET_DIR/registry.json"
    if [ -z "${CI_TPL_REGISTRY:-}" ]; then
      CI_TPL_REGISTRY=$(ci_tpl_vault_field "$CI_TPL_SECRET_DIR/registry.json" "$CI_TPL_FIELD_REGISTRY")
    fi
    CI_TPL_USER=$(ci_tpl_vault_field "$CI_TPL_SECRET_DIR/registry.json" "$CI_TPL_FIELD_USER")
    CI_TPL_TOKEN=$(ci_tpl_vault_field "$CI_TPL_SECRET_DIR/registry.json" "$CI_TPL_FIELD_TOKEN")
    rm -f "$CI_TPL_SECRET_DIR/registry.json"
  else
    if [ -z "${CI_TPL_REGISTRY:-}" ]; then
      echo 'ERROR: the registry input is required unless vault-addr is set' >&2
      exit 1
    fi
    CI_TPL_USER=${REGISTRY_USER:-}
    CI_TPL_TOKEN=${REGISTRY_PASSWORD:-}
    if [ -z "$CI_TPL_USER" ] || [ -z "$CI_TPL_TOKEN" ]; then
      echo 'ERROR: set the CI variables REGISTRY_USER and REGISTRY_PASSWORD, or set vault-addr' >&2
      exit 1
    fi
  fi
  export CI_TPL_REGISTRY CI_TPL_USER CI_TPL_TOKEN
  echo "registry: $CI_TPL_REGISTRY (user $CI_TPL_USER, credential length ${#CI_TPL_TOKEN})"
  ci_tpl_docker_config
}

# Resolve the object store credential. Export component only.
ci_tpl_s3_credentials() {
  if [ -n "${CI_TPL_VAULT_ADDR:-}" ]; then
    if [ -z "${CI_TPL_VAULT_S3_KV_PATH:-}" ]; then
      echo 'ERROR: vault-addr is set, so vault-s3-kv-path is required' >&2
      exit 1
    fi
    ci_tpl_vault_login
    ci_tpl_vault_read "$CI_TPL_VAULT_S3_KV_PATH" "$CI_TPL_SECRET_DIR/s3.json"
    AWS_ACCESS_KEY_ID=$(ci_tpl_vault_field "$CI_TPL_SECRET_DIR/s3.json" "$CI_TPL_S3_FIELD_ACCESS")
    AWS_SECRET_ACCESS_KEY=$(ci_tpl_vault_field "$CI_TPL_SECRET_DIR/s3.json" "$CI_TPL_S3_FIELD_SECRET")
    rm -f "$CI_TPL_SECRET_DIR/s3.json"
  else
    AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-}
    AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY:-}
    if [ -z "$AWS_ACCESS_KEY_ID" ] || [ -z "$AWS_SECRET_ACCESS_KEY" ]; then
      echo 'ERROR: set the CI variables AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, or set vault-addr' >&2
      exit 1
    fi
  fi
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
  echo "object store: ${CI_TPL_S3_ENDPOINT:-} (key length ${#AWS_ACCESS_KEY_ID})"
}

# Write the docker config skopeo authenticates with, and publish its path as
# CI_TPL_AUTHFILE. Built by python from the environment so the token never
# reaches a command line or a process listing.
ci_tpl_docker_config() {
  DOCKER_CONFIG=$CI_TPL_SECRET_DIR/docker
  mkdir -p "$DOCKER_CONFIG"
  chmod 700 "$DOCKER_CONFIG"
  python3 -c 'import base64, json, os, sys; pair = "%s:%s" % (os.environ["CI_TPL_USER"], os.environ["CI_TPL_TOKEN"]); json.dump({"auths": {os.environ["CI_TPL_REGISTRY"]: {"auth": base64.b64encode(pair.encode()).decode()}}}, sys.stdout)' \
    > "$DOCKER_CONFIG/config.json"
  chmod 600 "$DOCKER_CONFIG/config.json"
  CI_TPL_AUTHFILE=$DOCKER_CONFIG/config.json
  export DOCKER_CONFIG CI_TPL_AUTHFILE
}

# Refuse to run in a mode that writes nothing, unless a merge request asked for
# it. CI_TPL_DRY_RUN decides whether the mirror copies anything, and a project
# or group CI variable outranks a job variable: unguarded, one such variable
# turns every mirror into a green run nobody notices.
ci_tpl_require_write_mode() {
  case "${CI_TPL_DRY_RUN:-}" in
    true | false) ;;
    *)
      echo "ERROR: CI_TPL_DRY_RUN must be true or false, got '${CI_TPL_DRY_RUN:-}'" >&2
      exit 1
      ;;
  esac
  if [ "$CI_TPL_DRY_RUN" = "true" ] && [ "${CI_PIPELINE_SOURCE:-}" != "merge_request_event" ]; then
    echo "ERROR: CI_TPL_DRY_RUN is true on a ${CI_PIPELINE_SOURCE:-} pipeline; only a merge request may inspect instead of copying" >&2
    exit 1
  fi
}

# Read every path in CI_TPL_LIST_FILES into $1, comments and blanks removed.
#
# CI_TPL_LIST_FILES is deliberately unquoted: the list-files input is a
# regex-validated space-separated string because GitLab cannot interpolate an
# array input into a variable value, and the split is what turns it back into
# paths. The regex admits no whitespace inside a path.
ci_tpl_collect_refs() {
  _out=$1
  _root=$(cd "$CI_PROJECT_DIR" && pwd -P)
  : > "$_out"
  for _file in $CI_TPL_LIST_FILES; do
    case "$_file" in
      /* | *..*)
        echo "ERROR: list file $_file is absolute or traverses" >&2
        exit 1
        ;;
    esac
    _path=$CI_PROJECT_DIR/$_file
    if [ ! -f "$_path" ]; then
      echo "ERROR: list file $_file not found" >&2
      exit 1
    fi
    case "$(cd "$(dirname "$_path")" && pwd -P)" in
      "$_root" | "$_root"/*) ;;
      *)
        echo "ERROR: list file $_file escapes the checkout" >&2
        exit 1
        ;;
    esac
    sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$_path" >> "$_out"
  done
  if [ ! -s "$_out" ]; then
    echo 'ERROR: no references in list-files' >&2
    exit 1
  fi
}

# The reference to pull for $1. skopeo refuses a reference carrying both a tag
# and a digest, so a pinned entry is fetched as repo@digest and the tag is only
# used to name the copy.
ci_tpl_source_ref() {
  _name=${1%%@*}
  _last=${_name##*/}
  case $_last in
    *:*) _repo=${_name%:*} ;;
    *) _repo=$_name ;;
  esac
  case $1 in
    *@sha256:*) printf '%s@%s\n' "$_repo" "${1#*@}" ;;
    *) printf '%s\n' "$_name" ;;
  esac
}

# Where $1 lands: its upstream path with the registry host stripped, and its
# tag. The caller prepends the registry and the repository prefix. A first
# segment carrying a dot, a colon or spelled localhost is a registry host;
# anything else is a Docker Hub namespace and is part of the path.
ci_tpl_destination() {
  _name=${1%%@*}
  _last=${_name##*/}
  case $_last in
    *:*)
      _tag=${_last##*:}
      _repo=${_name%:*}
      ;;
    *)
      _tag=latest
      _repo=$_name
      ;;
  esac
  _path=$_repo
  case ${_path%%/*} in
    *.* | *:* | localhost) _path=${_path#*/} ;;
  esac
  printf '%s:%s\n' "$_path" "$_tag"
}

# Copy $1 to $2, or inspect $1 when CI_TPL_DRY_RUN is true, and print the
# resulting digest. Three failed rounds is a real outage or a pinned digest the
# registry does not have, not the 502 a public registry answers now and then.
#
# --format oci, not --preserve-digests: the export component stores these
# manifests in an OCI layout, which takes OCI media types only.
ci_tpl_copy_ref() {
  _src=$1
  _dest=$2
  _os=${CI_TPL_ARCH%%-*}
  _cpu=${CI_TPL_ARCH##*-}
  _attempt=1
  while : ; do
    if [ "$CI_TPL_DRY_RUN" = "true" ]; then
      if _digest=$(skopeo inspect --override-os "$_os" --override-arch "$_cpu" \
          --format '{{.Digest}}' "docker://$_src"); then
        break
      fi
    elif skopeo copy -q --retry-times 3 --format oci \
        --override-os "$_os" --override-arch "$_cpu" \
        --dest-authfile "$CI_TPL_AUTHFILE" "docker://$_src" "docker://$_dest"; then
      if _digest=$(skopeo inspect --authfile "$CI_TPL_AUTHFILE" --override-os "$_os" \
          --override-arch "$_cpu" --format '{{.Digest}}' "docker://$_dest"); then
        break
      fi
    fi
    if [ "$_attempt" -ge 3 ]; then
      echo "failed three times: $_src" >&2
      exit 1
    fi
    echo "retry $_attempt for $_src" >&2
    # Linear backoff. CI_TPL_RETRY_DELAY exists so the runtime tests can drive
    # all three rounds without waiting a minute for them; no template sets it.
    sleep $((_attempt * ${CI_TPL_RETRY_DELAY:-20}))
    _attempt=$((_attempt + 1))
  done
  printf '%s\n' "$_digest"
}
