#!/bin/sh
# Copy one digest from one OCI repository to another, and prove the copy.
#
# runtime/promote/harbor.py does the same job through the Harbor API, so a site
# whose registry is Quay or Artifactory cannot promote at all. Every registry in
# that list implements the OCI distribution API, and skopeo speaks only that, so
# the vendor becomes two repository names instead of a second component.
#
# Nothing is rebuilt. A copy by digest reuses the manifest the source already
# holds, and the target manifest is read back and hashed: a digest that differs
# is a different artifact, whatever the copy reported.
#
# Functions only; sourcing it runs nothing. The component embeds it verbatim
# after runtime/registry/image-json.sh, whose ci_tpl_fail, ci_tpl_json_string,
# ci_tpl_json_list, ci_tpl_require_digest, ci_tpl_validate_image_json,
# ci_tpl_write_registry_auth and ci_tpl_registry_host it calls. Section 12:
# `include:` imports YAML, so a consumer job never has this repository on disk.
#
# tests/runtime/promote/test_skopeo.py drives this file with skopeo stubbed.

# The digest to promote. Exactly one source, never a fallback: an unreadable
# image.json that quietly became "whatever tag is there now" is the defect
# container-promote-harbor was written to remove.
ci_tpl_promote_source_digest() {
    if [ -n "${CI_TPL_SOURCE_DIGEST:-}" ]; then
        ci_tpl_require_digest "$CI_TPL_SOURCE_DIGEST" || return 1
        printf '%s\n' "$CI_TPL_SOURCE_DIGEST"
        return 0
    fi
    if [ -z "${CI_TPL_IDENTITY_FILE:-}" ] || [ ! -f "$CI_TPL_IDENTITY_FILE" ]; then
        ci_tpl_fail "neither source-digest nor a readable image.json at ${CI_TPL_IDENTITY_FILE:-<unset>}"
        return 1
    fi
    ci_tpl_validate_image_json "$CI_TPL_IDENTITY_FILE" || return 1
    ci_tpl_json_string "$CI_TPL_IDENTITY_FILE" digest
}

# The tags the promoted digest is published under, one per line.
#
# At least one is required. skopeo refuses a digest as a copy DESTINATION, and
# an untagged push would land an artifact the registry's own garbage collection
# is entitled to remove.
ci_tpl_promote_tags() {
    _list=$(ci_tpl_json_list "${CI_TPL_TARGET_TAGS:-}")
    if [ -z "$_list" ]; then
        ci_tpl_fail 'target-tags is empty; a copy destination needs at least one tag'
        return 1
    fi
    # Split on newlines only, with globbing off: a tag is consumer data and
    # must not become a pattern that matches a file in the checkout.
    _saved_ifs=$IFS
    IFS='
'
    set -f
    for _tag in $_list; do
        case "$_tag" in
            *[!A-Za-z0-9._-]* | '')
                set +f
                IFS=$_saved_ifs
                ci_tpl_fail "'$_tag' is not a valid tag name"
                return 1
                ;;
        esac
        printf '%s\n' "$_tag"
    done
    set +f
    IFS=$_saved_ifs
}

# Write the two docker config files skopeo reads, one per registry.
#
# Two calls to the same helper with the two credential variable NAMES swapped in
# turn, so a promotion between registries takes two identities and a promotion
# inside one takes the same identity twice. Neither credential reaches a command
# line: skopeo is given a file path.
ci_tpl_promote_authfiles() {
    _dir=$1
    _source_host=$(ci_tpl_registry_host "$CI_TPL_SOURCE_REPOSITORY")
    _target_host=$(ci_tpl_registry_host "$CI_TPL_TARGET_REPOSITORY")
    CI_TPL_REGISTRY_USERNAME_VARIABLE=$CI_TPL_SOURCE_USERNAME_VARIABLE
    CI_TPL_REGISTRY_PASSWORD_VARIABLE=$CI_TPL_SOURCE_PASSWORD_VARIABLE
    ci_tpl_write_registry_auth "$_dir/source" "$_source_host" || return 1
    CI_TPL_REGISTRY_USERNAME_VARIABLE=$CI_TPL_TARGET_USERNAME_VARIABLE
    CI_TPL_REGISTRY_PASSWORD_VARIABLE=$CI_TPL_TARGET_PASSWORD_VARIABLE
    ci_tpl_write_registry_auth "$_dir/target" "$_target_host" || return 1
    CI_TPL_SOURCE_AUTHFILE=$_dir/source/config.json
    CI_TPL_TARGET_AUTHFILE=$_dir/target/config.json
    export CI_TPL_SOURCE_AUTHFILE CI_TPL_TARGET_AUTHFILE
}

# Copy the digest to one tag on the target.
#
# --all copies every manifest an index names. Without it skopeo copies the one
# image matching the runner's own platform, which silently turns a multi-arch
# release into an amd64 one and changes the digest.
ci_tpl_promote_copy() {
    _digest=$1
    _tag=$2
    skopeo copy --all \
        --src-authfile "$CI_TPL_SOURCE_AUTHFILE" \
        --dest-authfile "$CI_TPL_TARGET_AUTHFILE" \
        "docker://$CI_TPL_SOURCE_REPOSITORY@$_digest" \
        "docker://$CI_TPL_TARGET_REPOSITORY:$_tag"
}

# The digest the target tag resolves to.
#
# The raw manifest is hashed rather than a field of `skopeo inspect` being read.
# A digest IS the sha256 of those bytes, so this answers with the registry's
# content rather than with what the registry says about it; and plain `skopeo
# inspect` resolves an index down to the runner's platform, which would report
# a child digest and never equal the index digest it was asked about.
ci_tpl_promote_target_digest() {
    _tag=$1
    _raw=${CI_TPL_WORK:-/tmp}/ci-tpl-target-manifest.json
    skopeo inspect --raw \
        --authfile "$CI_TPL_TARGET_AUTHFILE" \
        "docker://$CI_TPL_TARGET_REPOSITORY:$_tag" > "$_raw" || return 1
    printf 'sha256:%s\n' "$(sha256sum "$_raw" | cut -d ' ' -f 1)"
}

# Fail unless the target carries the digest that was copied.
ci_tpl_promote_verify() {
    _digest=$1
    _tag=$2
    _got=$(ci_tpl_promote_target_digest "$_tag") || return 1
    if [ "$_got" != "$_digest" ]; then
        ci_tpl_fail "$CI_TPL_TARGET_REPOSITORY:$_tag is $_got, not the $_digest that was copied"
        return 1
    fi
    echo "verified $CI_TPL_TARGET_REPOSITORY:$_tag at $_digest"
}

# The evidence record. Written after the verification, never before: a result
# file naming a digest nobody read back is the "HTTP 201 is proof" defect.
ci_tpl_promote_result() {
    _file=$1
    _digest=$2
    shift 2
    mkdir -p "$(dirname "$_file")"
    _tags=''
    for _tag in "$@"; do
        _tags="$_tags${_tags:+, }\"$_tag\""
    done
    cat > "$_file" <<EOF
{
  "schema_version": 1,
  "source": "$CI_TPL_SOURCE_REPOSITORY@$_digest",
  "target": "$CI_TPL_TARGET_REPOSITORY@$_digest",
  "digest": "$_digest",
  "tags": [$_tags],
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
}
