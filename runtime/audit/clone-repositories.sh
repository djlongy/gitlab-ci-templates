#!/usr/bin/env bash
# clone-repositories.sh — resolve an audit's repository set, clone all of it, and
# record the coverage it actually achieved.
#
# Section 11.6 is the reason this script exists rather than a loop in a job:
# "A failure to clone an in-scope repository must report incomplete coverage and
# fail the required audit; scanning zero repositories is not success." The two
# templates this replaces (security/group-scan.yml, security/terraform-audit.yml)
# both printed "SKIP: Could not clone ..." and exited 0, so a token that had lost
# access to every project produced a green fleet-wide audit that read nothing.
#
# Usage:
#   clone-repositories.sh <destination-directory>
#
# Environment:
#   CI_TPL_REPOSITORIES   whitespace-separated full paths (group/name). Explicit
#                         set; mutually exclusive with CI_TPL_GROUP_PATH.
#   CI_TPL_GROUP_PATH     full path of a group whose projects are the set,
#                         including subgroups. Resolved through the API.
#   CI_TPL_API_URL        API v4 root, e.g. https://git.example/api/v4
#   CI_TPL_SERVER_URL     server root used to build clone URLs
#   CI_TPL_AUDIT_TOKEN    token value with read_api and read_repository
#   CI_TPL_COVERAGE_FILE  path of the coverage record to write
#   CI_TPL_CLONE_DEPTH    git clone depth (default 1; 0 clones full history)
#
# Exit codes — the audit's failure semantics, tested in tests/runtime/audit/:
#   0  every in-scope repository cloned
#   1  usage error (no destination, both or neither selector, missing tool)
#   2  a repository in scope failed to clone: coverage is incomplete
#   3  the repository set could not be resolved, or resolved to nothing

set -euo pipefail

destination="${1:?usage: clone-repositories.sh <destination-directory>}"

repositories="${CI_TPL_REPOSITORIES:-}"
group_path="${CI_TPL_GROUP_PATH:-}"
api_url="${CI_TPL_API_URL:-}"
server_url="${CI_TPL_SERVER_URL:-}"
token="${CI_TPL_AUDIT_TOKEN:-}"
coverage_file="${CI_TPL_COVERAGE_FILE:-}"
clone_depth="${CI_TPL_CLONE_DEPTH:-1}"

die() { echo "clone-repositories: $2" >&2; exit "$1"; }

for tool in git curl jq; do
  command -v "$tool" >/dev/null 2>&1 || die 1 "$tool is not on PATH"
done

if [ -n "$repositories" ] && [ -n "$group_path" ]; then
  die 1 "set CI_TPL_REPOSITORIES or CI_TPL_GROUP_PATH, not both"
fi
if [ -z "$repositories" ] && [ -z "$group_path" ]; then
  die 1 "set CI_TPL_REPOSITORIES or CI_TPL_GROUP_PATH"
fi
[ -n "$coverage_file" ] || die 1 "CI_TPL_COVERAGE_FILE is not set"
[ -n "$server_url" ] || die 1 "CI_TPL_SERVER_URL is not set"

# ---------------------------------------------------------------- resolve set
source_kind=explicit
if [ -n "$group_path" ]; then
  source_kind=group
  [ -n "$api_url" ] || die 1 "CI_TPL_API_URL is not set"
  [ -n "$token" ] || die 1 "CI_TPL_AUDIT_TOKEN is not set for a group listing"

  encoded=$(printf '%s' "$group_path" | sed 's|/|%2F|g')
  page=1
  resolved=""
  while [ "$page" -le 20 ]; do
    body=$(curl -fsS --max-time 60 -H "PRIVATE-TOKEN: $token" \
      "$api_url/groups/$encoded/projects?per_page=100&include_subgroups=true&archived=false&page=$page") \
      || die 3 "listing projects of group '$group_path' failed (page $page)"
    printf '%s' "$body" | jq -e 'type == "array"' >/dev/null 2>&1 \
      || die 3 "unexpected response listing group '$group_path'"
    names=$(printf '%s' "$body" | jq -r '.[].path_with_namespace')
    [ -n "$names" ] || break
    resolved="${resolved}${names}"$'\n'
    page=$((page + 1))
  done
  repositories="$resolved"
fi

set -- $repositories
[ "$#" -gt 0 ] || die 3 "the repository set is empty; scanning nothing is not a pass"

# ---------------------------------------------------------------------- clone
mkdir -p "$destination"

if [ -n "$token" ]; then
  # The token goes in a private credential file rather than the clone URL: a URL
  # carrying it is echoed back by git on any error, and lands in the job log.
  credentials="$destination/.git-credentials"
  host=${server_url#*://}
  scheme=${server_url%%://*}
  umask 077
  printf '%s://oauth2:%s@%s\n' "$scheme" "$token" "${host%%/*}" >"$credentials"
  export GIT_CONFIG_GLOBAL="$destination/.gitconfig"
  git config --global credential.helper "store --file=$credentials"
fi

depth_args=""
[ "$clone_depth" -gt 0 ] 2>/dev/null && depth_args="--depth $clone_depth"

failed=0
entries=""
for path in "$@"; do
  target="$destination/$(printf '%s' "$path" | tr '/' '_')"
  echo "--- cloning $path"
  # shellcheck disable=SC2086  # depth_args is a deliberate word-split of our own value
  if git clone --quiet $depth_args "$server_url/$path.git" "$target"; then
    outcome=cloned
  else
    outcome=failed
    failed=$((failed + 1))
    echo "clone-repositories: FAILED to clone $path" >&2
  fi
  entries="${entries}${path} ${outcome}"$'\n'
done

# ------------------------------------------------------------------- coverage
mkdir -p "$(dirname "$coverage_file")"
printf '%s' "$entries" | jq -R -s --arg source "$source_kind" \
  --arg group "$group_path" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '
  split("\n") | map(select(length > 0) | split(" ")) as $rows |
  {
    schema_version: 1,
    source: $source,
    group_path: (if $group == "" then null else $group end),
    generated_at: $at,
    in_scope: ($rows | length),
    cloned: ($rows | map(select(.[1] == "cloned")) | length),
    repositories: ($rows | map({path: .[0], outcome: .[1]}))
  }' >"$coverage_file"

echo "coverage record: $coverage_file"
if [ "$failed" -gt 0 ]; then
  die 2 "$failed of $# repositories failed to clone; coverage is incomplete"
fi
echo "clone-repositories: $# of $# repositories cloned"
