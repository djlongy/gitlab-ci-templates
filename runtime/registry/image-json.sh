#!/bin/sh
# Image identity helpers for the container build and container test components.
#
# Section 9.1 of docs/gitlab-ci-agent-standard.md makes image.json the
# authoritative identity record for a built image, and section 10.1 makes a
# missing or unverifiable identity a job failure rather than a warning. Every
# function here fails loudly; none of them falls back to a tag.
#
# POSIX sh with awk only. The builder images are busybox userlands with no jq
# and no python3, so the JSON handling below is deliberately small.
#
# This file is embedded verbatim into each component's before_script, because a
# component include imports YAML and never checks out this repository (section
# 12). tests/runtime/registry/test_embedded_helper.py fails when a copy drifts.

CI_TPL_DIGEST_PATTERN='^sha256:[0-9a-f]\{64\}$'

# Print the string value of the first "<key>": "<value>" pair in a JSON file.
# Key matching is literal, so a key containing dots (BuildKit writes
# "containerimage.digest") needs no escaping. Values carrying escaped quotes are
# out of scope: every field read here is a digest, a reference or a media type.
ci_tpl_json_string() {
    awk -v key="$2" '
        { blob = blob $0 }
        END {
            needle = "\"" key "\""
            p = index(blob, needle)
            if (p == 0) exit 1
            rest = substr(blob, p + length(needle))
            c = index(rest, ":")
            if (c == 0) exit 1
            rest = substr(rest, c + 1)
            q = index(rest, "\"")
            if (q == 0) exit 1
            rest = substr(rest, q + 1)
            e = index(rest, "\"")
            if (e == 0) exit 1
            printf "%s", substr(rest, 1, e - 1)
        }
    ' "$1"
}

# GitLab renders an array input used in a string context as a JSON array
# literal. Split one into a value per line so the job can quote each item as a
# single argument. No eval: the values are consumer data.
ci_tpl_json_list() {
    printf '%s' "$1" | awk '
        { blob = blob $0 }
        END {
            n = length(blob)
            inside = 0
            item = ""
            for (i = 1; i <= n; i++) {
                c = substr(blob, i, 1)
                if (inside) {
                    if (c == "\\") { i++; item = item substr(blob, i, 1); continue }
                    if (c == "\"") { print item; item = ""; inside = 0; continue }
                    item = item c
                } else if (c == "\"") {
                    inside = 1
                }
            }
        }
    '
}

ci_tpl_fail() {
    echo "ERROR: $*" >&2
    return 1
}

# A digest is sha256 followed by 64 hex characters. Anything else -- a tag, an
# empty string, a truncated digest -- is refused here rather than written into a
# field named digest.
ci_tpl_require_digest() {
    case "$1" in
        '') ci_tpl_fail "no image digest was produced; refusing to continue without one" ;;
        *)
            if expr "$1" : "$CI_TPL_DIGEST_PATTERN" >/dev/null; then
                return 0
            fi
            ci_tpl_fail "'$1' is not a sha256 digest; a tag is not an identity"
            ;;
    esac
}

# A repository is registry[:port]/path with no tag and no digest attached.
ci_tpl_require_repository() {
    case "$1" in
        '') ci_tpl_fail "image-repository is empty" ;;
        *@*) ci_tpl_fail "image-repository '$1' carries a digest; pass the repository alone" ;;
        */*) ;;
        *) ci_tpl_fail "image-repository '$1' has no registry host" ;;
    esac || return 1
    # A colon is only legitimate in the registry host, as a port.
    case "${1#*/}" in
        *:*) ci_tpl_fail "image-repository '$1' carries a tag; pass the repository alone" ;;
        *) return 0 ;;
    esac
}

# A comma-separated platform list of more than one entry is pushed as an index.
ci_tpl_subject_kind_from_platforms() {
    case "$1" in
        *,*) echo index ;;
        *) echo manifest ;;
    esac
}

# BuildKit records the pushed descriptor's media type. That is the authoritative
# answer to index-versus-manifest; the platform list is only the request.
ci_tpl_subject_kind_from_metadata() {
    media_type=$(ci_tpl_json_string "$1" mediaType 2>/dev/null) || media_type=''
    case "$media_type" in
        *index*|*manifest.list*) echo index ;;
        '') ci_tpl_subject_kind_from_platforms "$2" ;;
        *) echo manifest ;;
    esac
}

# BuildKit writes both the exported image digest and the image config digest.
# containerimage.digest is the manifest digest cosign and every scanner need;
# containerimage.config.digest is not. Read the right one, or fail.
ci_tpl_buildkit_digest() {
    digest=$(ci_tpl_json_string "$1" containerimage.digest 2>/dev/null) || digest=''
    ci_tpl_require_digest "$digest" || return 1
    echo "$digest"
}

# ko --image-refs writes one fully qualified repository@digest per built import
# path. Parse it as a reference, never as "the last line of the build output".
ci_tpl_reference_digest() {
    case "$1" in
        *@*) digest=${1##*@} ;;
        *) ci_tpl_fail "'$1' is not a repository@digest reference" ; return 1 ;;
    esac
    ci_tpl_require_digest "$digest" || return 1
    echo "$digest"
}

ci_tpl_reference_repository() {
    echo "${1%@*}"
}

# An OCI tag. The pattern is deliberately narrower than the specification: a
# tag that still contains a dollar sign means GitLab did not expand the variable
# it came from, and pushing a literal '$CI_COMMIT_SHORT_SHA' would be worse than
# failing here.
ci_tpl_require_tag() {
    expr "$1" : '^[A-Za-z0-9_][A-Za-z0-9._-]\{0,126\}$' >/dev/null && return 0
    ci_tpl_fail "'$1' is not a usable image tag"
}

ci_tpl_require_build_arg() {
    expr "$1" : '^[A-Za-z_][A-Za-z0-9_]*=' >/dev/null && return 0
    ci_tpl_fail "'$1' is not a NAME=VALUE build argument"
}

ci_tpl_json_array() {
    # Render a comma-separated list as a JSON array of strings.
    echo "$1" | awk -F, '{
        out = ""
        for (i = 1; i <= NF; i++) {
            if (out != "") out = out ", "
            out = out "\"" $i "\""
        }
        printf "[%s]", out
    }'
}

# Write image.json (schema 1) and the optional build.env convenience dotenv.
# Arguments: artifact-dir repository digest platforms-csv subject-kind
ci_tpl_write_image_json() {
    ci_tpl_require_repository "$2" || return 1
    ci_tpl_require_digest "$3" || return 1
    mkdir -p "$1"
    cat > "$1/image.json" <<JSON
{
  "schema_version": 1,
  "repository": "$2",
  "digest": "$3",
  "reference": "$2@$3",
  "source_commit": "${CI_COMMIT_SHA}",
  "pipeline_id": "${CI_PIPELINE_ID}",
  "job_id": "${CI_JOB_ID}",
  "platforms": $(ci_tpl_json_array "$4"),
  "subject_kind": "$5",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
    {
        printf 'CI_TPL_IMAGE_REFERENCE=%s@%s\n' "$2" "$3"
        printf 'CI_TPL_IMAGE_PUSHED=true\n'
    } > "$1/build.env"
    ci_tpl_validate_image_json "$1/image.json"
}

# A build that did not push has no registry identity, so it writes no image.json
# at all. Anything downstream then fails its own validation rather than scanning
# or signing a reference that does not resolve.
ci_tpl_write_unpushed_env() {
    mkdir -p "$1"
    printf 'CI_TPL_IMAGE_PUSHED=false\n' > "$1/build.env"
}

# Producers validate their own output before reporting success (section 9.4).
# Consumers run the same check on what they downloaded.
ci_tpl_validate_image_json() {
    file=$1
    [ -f "$file" ] || { ci_tpl_fail "$file does not exist"; return 1; }
    grep -q '"schema_version": 1' "$file" ||
        { ci_tpl_fail "$file is not image.json schema 1"; return 1; }
    repository=$(ci_tpl_json_string "$file" repository 2>/dev/null) || repository=''
    digest=$(ci_tpl_json_string "$file" digest 2>/dev/null) || digest=''
    reference=$(ci_tpl_json_string "$file" reference 2>/dev/null) || reference=''
    commit=$(ci_tpl_json_string "$file" source_commit 2>/dev/null) || commit=''
    pipeline=$(ci_tpl_json_string "$file" pipeline_id 2>/dev/null) || pipeline=''
    job=$(ci_tpl_json_string "$file" job_id 2>/dev/null) || job=''
    platforms=$(ci_tpl_json_string "$file" platforms 2>/dev/null) || platforms=''
    kind=$(ci_tpl_json_string "$file" subject_kind 2>/dev/null) || kind=''
    created=$(ci_tpl_json_string "$file" created_at 2>/dev/null) || created=''
    ci_tpl_require_repository "$repository" || return 1
    ci_tpl_require_digest "$digest" || return 1
    [ "$reference" = "$repository@$digest" ] ||
        { ci_tpl_fail "$file reference '$reference' disagrees with its repository and digest"; return 1; }
    expr "$commit" : '^[0-9a-f]\{40\}$' >/dev/null ||
        { ci_tpl_fail "$file source_commit '$commit' is not a full commit sha"; return 1; }
    [ -n "$pipeline" ] && [ -n "$job" ] ||
        { ci_tpl_fail "$file is missing pipeline_id or job_id"; return 1; }
    [ -n "$platforms" ] ||
        { ci_tpl_fail "$file records no platforms"; return 1; }
    case "$kind" in
        manifest|index) ;;
        *) ci_tpl_fail "$file subject_kind '$kind' is neither manifest nor index"; return 1 ;;
    esac
    [ -n "$created" ] ||
        { ci_tpl_fail "$file records no created_at"; return 1; }
    return 0
}

# Print the validated reference from an image.json.
ci_tpl_image_reference() {
    ci_tpl_validate_image_json "$1" || return 1
    ci_tpl_json_string "$1" reference
}

# Write a registry auth file from CI_TPL_REGISTRY_USER and
# CI_TPL_REGISTRY_PASSWORD. The credentials are read from the environment, never
# passed as arguments, so they do not appear in the process table. base64 wraps
# at 76 columns in both busybox and coreutils, so the newlines are stripped:
# a wrapped credential produces a config.json that no client can parse.
ci_tpl_write_registry_auth() {
    config_dir=$1
    registry=$2
    [ -n "$CI_TPL_REGISTRY_USER" ] && [ -n "$CI_TPL_REGISTRY_PASSWORD" ] ||
        { ci_tpl_fail "registry credentials are not set; this job cannot push"; return 1; }
    mkdir -p "$config_dir"
    auth=$(printf '%s:%s' "$CI_TPL_REGISTRY_USER" "$CI_TPL_REGISTRY_PASSWORD" | base64 | tr -d '\n')
    printf '{"auths":{"%s":{"auth":"%s"}}}' "$registry" "$auth" > "$config_dir/config.json"
    chmod 600 "$config_dir/config.json"
}

ci_tpl_registry_host() {
    echo "${1%%/*}"
}
