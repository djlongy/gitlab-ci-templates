"""Behaviour of the shell the mirror and export components carry.

Every test drives the copy embedded in a template, not runtime/mirror/*.sh, for
the reason tests/runtime/conftest.py gives: the embedding is where shell changes
meaning, and a source file that behaves correctly proves nothing about the job.

Each test names something that was wrong, or would go wrong silently:

  * a mirror that reports success having copied nothing, which is what an
    inherited CI_TPL_DRY_RUN did before tag 0.1.1;
  * a credential on a command line, which is a process listing away from every
    other process on the runner;
  * a reference carrying both a tag and a digest, which skopeo refuses;
  * a copy that fails and leaves a digest line behind anyway.

skopeo, curl and the AWS CLI are stubs. The point is what the wrapper decides,
not whether a registry answers.
"""

from __future__ import annotations

import json
import os
import stat

MIRROR = "container-mirror-skopeo"
EXPORT = "container-export-skopeo"
LIBRARY = "runtime/mirror/registry.sh"
EGRESS = "runtime/mirror/egress.sh"

CREDENTIAL = "s3cr3t-robot-token-value"


def variables(**overrides) -> dict:
    """The job variables the mirror component sets, with the defaults filled in."""
    base = {
        "CI_TPL_LIST_FILES": "images.txt",
        "CI_TPL_REGISTRY": "registry.example.com",
        "CI_TPL_PREFIX": "mirror",
        "CI_TPL_ARCH": "linux-amd64",
        "CI_TPL_DRY_RUN": "false",
        "CI_TPL_VAULT_ADDR": "",
        "CI_TPL_VAULT_ROLE": "",
        "CI_TPL_VAULT_KV_PATH": "",
        "CI_TPL_FIELD_REGISTRY": "registry",
        "CI_TPL_FIELD_USER": "username",
        "CI_TPL_FIELD_TOKEN": "token",
        "CI_TPL_CA_BUNDLE_URL": "",
        "CI_TPL_EGRESS_PROXY": "",
        "CI_TPL_NO_PROXY": "",
        "CI_TPL_OUT_DIR": ".ci-artifacts/demo/container-mirror-skopeo",
        "REGISTRY_USER": "robot",
        "REGISTRY_PASSWORD": CREDENTIAL,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# The guard that stops a mirror reporting success having written nothing
# --------------------------------------------------------------------------

def test_a_dry_run_outside_a_merge_request_is_refused(harness):
    """Tag 0.1.1's fix. A project or group CI variable outranks a job variable,
    so an inherited CI_TPL_DRY_RUN=true turned every scheduled mirror green."""
    harness.env = variables(CI_TPL_DRY_RUN="true")
    harness.env["CI_PIPELINE_SOURCE"] = "schedule"
    run = harness.run(MIRROR, LIBRARY, then="ci_tpl_require_write_mode\n")
    assert run.returncode == 1
    assert "only a merge request may inspect instead of copying" in run.output


def test_a_dry_run_on_a_merge_request_is_allowed(harness):
    harness.env = variables(CI_TPL_DRY_RUN="true")
    harness.env["CI_PIPELINE_SOURCE"] = "merge_request_event"
    run = harness.run(MIRROR, LIBRARY, then="ci_tpl_require_write_mode\necho allowed\n")
    assert run.returncode == 0
    assert "allowed" in run.stdout


def test_a_dry_run_flag_that_is_not_a_boolean_is_refused(harness):
    """`True` is not `true`. Accepting it would take the false branch and copy."""
    harness.env = variables(CI_TPL_DRY_RUN="True")
    harness.env["CI_PIPELINE_SOURCE"] = "merge_request_event"
    run = harness.run(MIRROR, LIBRARY, then="ci_tpl_require_write_mode\n")
    assert run.returncode == 1
    assert "must be true or false, got 'True'" in run.output


# --------------------------------------------------------------------------
# Reading the reference lists
# --------------------------------------------------------------------------

def test_comments_and_blank_lines_are_stripped(harness):
    harness.env = variables(CI_TPL_LIST_FILES="images.txt extra.txt")
    harness.file("images.txt", "# a comment\n\ndocker.io/library/nginx:1.27  # trailing\n")
    harness.file("extra.txt", "quay.io/prometheus/node-exporter:v1.8.2\n")
    run = harness.run(
        MIRROR, LIBRARY, then='ci_tpl_collect_refs "$CI_PROJECT_DIR/refs"\ncat "$CI_PROJECT_DIR/refs"\n'
    )
    assert run.returncode == 0
    assert run.stdout.split() == [
        "docker.io/library/nginx:1.27",
        "quay.io/prometheus/node-exporter:v1.8.2",
    ]


def test_a_list_file_outside_the_checkout_is_refused(harness):
    harness.env = variables(CI_TPL_LIST_FILES="../escape.txt")
    run = harness.run(MIRROR, LIBRARY, then='ci_tpl_collect_refs "$CI_PROJECT_DIR/refs"\n')
    assert run.returncode == 1
    assert "absolute or traverses" in run.output


def test_a_list_file_that_is_not_there_is_refused(harness):
    harness.env = variables(CI_TPL_LIST_FILES="missing.txt")
    run = harness.run(MIRROR, LIBRARY, then='ci_tpl_collect_refs "$CI_PROJECT_DIR/refs"\n')
    assert run.returncode == 1
    assert "not found" in run.output


def test_a_list_of_only_comments_is_refused(harness):
    """Mirroring nothing is not a pass: the job would upload an empty digest
    list and every consumer downstream would read it as "nothing to do"."""
    harness.env = variables()
    harness.file("images.txt", "# everything is commented out\n")
    run = harness.run(MIRROR, LIBRARY, then='ci_tpl_collect_refs "$CI_PROJECT_DIR/refs"\n')
    assert run.returncode == 1
    assert "no references in list-files" in run.output


# --------------------------------------------------------------------------
# Reference parsing
# --------------------------------------------------------------------------

def test_a_reference_pinned_by_digest_is_fetched_without_its_tag(harness):
    """skopeo: "Docker references with both a tag and digest are currently not
    supported". The tag names the copy; the digest is what is pulled."""
    harness.env = variables()
    digest = "sha256:" + "0" * 64
    run = harness.run(
        MIRROR, LIBRARY, then=f'ci_tpl_source_ref "docker.io/library/nginx:1.27@{digest}"\n'
    )
    assert run.stdout.strip() == f"docker.io/library/nginx@{digest}"


def test_a_reference_with_no_digest_is_fetched_by_tag(harness):
    harness.env = variables()
    run = harness.run(MIRROR, LIBRARY, then='ci_tpl_source_ref "quay.io/coreos/etcd:v3.5.0"\n')
    assert run.stdout.strip() == "quay.io/coreos/etcd:v3.5.0"


def test_the_registry_host_is_stripped_from_the_destination(harness):
    harness.env = variables()
    run = harness.run(MIRROR, LIBRARY, then='ci_tpl_destination "quay.io/coreos/etcd:v3.5.0"\n')
    assert run.stdout.strip() == "coreos/etcd:v3.5.0"


def test_a_docker_hub_namespace_is_not_mistaken_for_a_registry_host(harness):
    """`rancher` carries no dot, no colon and is not localhost, so it is part of
    the path. Stripping it would collide every vendor's `agent` image."""
    harness.env = variables()
    run = harness.run(MIRROR, LIBRARY, then='ci_tpl_destination "rancher/rke2-runtime:v1.31.5"\n')
    assert run.stdout.strip() == "rancher/rke2-runtime:v1.31.5"


def test_a_reference_with_no_tag_lands_on_latest(harness):
    harness.env = variables()
    run = harness.run(MIRROR, LIBRARY, then='ci_tpl_destination "quay.io/coreos/etcd"\n')
    assert run.stdout.strip() == "coreos/etcd:latest"


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------

def test_ci_variable_mode_needs_both_variables(harness):
    harness.env = variables(REGISTRY_PASSWORD="")
    run = harness.run(MIRROR, LIBRARY, then="ci_tpl_registry_credentials\n")
    assert run.returncode == 1
    assert "REGISTRY_USER and REGISTRY_PASSWORD" in run.output


def test_ci_variable_mode_needs_a_registry(harness):
    harness.env = variables(CI_TPL_REGISTRY="")
    run = harness.run(MIRROR, LIBRARY, then="ci_tpl_registry_credentials\n")
    assert run.returncode == 1
    assert "the registry input is required unless vault-addr is set" in run.output


def test_vault_mode_needs_its_role_and_path(harness):
    """An empty role would post a login nobody authorised and, in the shape this
    replaced, fall through to an anonymous push."""
    harness.env = variables(CI_TPL_VAULT_ADDR="https://vault.example.com")
    run = harness.run(MIRROR, LIBRARY, then="ci_tpl_registry_credentials\n")
    assert run.returncode == 1
    assert "vault-role and vault-kv-path are required" in run.output


def test_the_registry_credential_reaches_skopeo_through_a_file_only(harness):
    """Section 10.2. The credential must not appear in the job log or in any
    argv the stub recorded; skopeo reads it from the docker config instead."""
    harness.env = variables()
    harness.stub("skopeo")
    run = harness.run(
        MIRROR,
        LIBRARY,
        then='ci_tpl_registry_credentials\ncat "$CI_TPL_AUTHFILE"\n',
    )
    assert run.returncode == 0, run.output
    config = json.loads(run.stdout[run.stdout.index("{"):])
    entry = config["auths"]["registry.example.com"]["auth"]
    import base64

    assert base64.b64decode(entry).decode() == f"robot:{CREDENTIAL}"
    # The log reports a length, never the value.
    assert CREDENTIAL not in run.output.replace(entry, "")
    assert f"credential length {len(CREDENTIAL)}" in run.stdout
    assert not any(CREDENTIAL in call for call in run.calls)


def test_the_docker_config_is_not_world_readable(harness):
    harness.env = variables()
    run = harness.run(
        MIRROR, LIBRARY, then='ci_tpl_registry_credentials\nprintf %s "$CI_TPL_AUTHFILE" > "$CI_PROJECT_DIR/where"\n'
    )
    assert run.returncode == 0, run.output
    authfile = (harness.project_dir / "where").read_text()
    mode = stat.S_IMODE(os.stat(authfile).st_mode)
    assert mode == 0o600, oct(mode)


def test_a_vault_login_posts_its_jwt_from_a_file_not_an_argument(harness):
    """The id_token is a bearer credential. On a command line it is readable by
    every process on the runner for as long as curl runs."""
    harness.env = variables(
        CI_TPL_VAULT_ADDR="https://vault.example.com",
        CI_TPL_VAULT_ROLE="ci",
        CI_TPL_VAULT_KV_PATH="kv/apps/mirror/runtime",
    )
    harness.env["VAULT_JWT"] = "jwt-header.jwt-payload.jwt-signature"
    harness.stub("curl", stdout=json.dumps({"auth": {"client_token": "vault-token-value"}}))
    run = harness.run(MIRROR, LIBRARY, then="ci_tpl_vault_login\n")
    assert run.returncode == 0, run.output
    assert not any("jwt-signature" in call for call in run.calls)
    assert "jwt-signature" not in run.output
    assert "Vault token obtained (length 17)" in run.stdout


def test_a_vault_login_that_returns_no_token_fails(harness):
    harness.env = variables(
        CI_TPL_VAULT_ADDR="https://vault.example.com",
        CI_TPL_VAULT_ROLE="ci",
        CI_TPL_VAULT_KV_PATH="kv/apps/mirror/runtime",
    )
    harness.env["VAULT_JWT"] = "a.b.c"
    harness.stub("curl", stdout='{"errors": ["permission denied"]}')
    run = harness.run(MIRROR, LIBRARY, then="ci_tpl_vault_login\n")
    assert run.returncode == 1
    assert "Vault JWT login returned no token" in run.output


def test_a_vault_read_keeps_the_vault_token_off_the_command_line(harness):
    harness.env = variables(CI_TPL_VAULT_ADDR="https://vault.example.com")
    harness.env["CI_TPL_VAULT_TOKEN"] = "hvs.some-vault-token"
    harness.stub("curl", stdout=json.dumps({"data": {"data": {"username": "robot"}}}))
    run = harness.run(
        MIRROR,
        LIBRARY,
        then='ci_tpl_vault_read kv/apps/mirror/runtime "$CI_PROJECT_DIR/secret.json"\n'
        'ci_tpl_vault_field "$CI_PROJECT_DIR/secret.json" username\n',
    )
    assert run.returncode == 0, run.output
    assert run.stdout.strip().endswith("robot")
    assert not any("hvs.some-vault-token" in call for call in run.calls)


# --------------------------------------------------------------------------
# The trust bundle
# --------------------------------------------------------------------------

def test_a_ca_bundle_with_no_certificate_fails(harness):
    """curl -f rejects an error status, not an HTTP 200 carrying a login page.
    A bundle with no certificate in it would otherwise be trusted as empty and
    the job would fail later naming the registry instead."""
    harness.env = variables(CI_TPL_CA_BUNDLE_URL="https://pki.example.com/bundle.pem")
    harness.stub("curl")
    harness.stub("update-ca-certificates")
    harness.write_stub(
        "curl",
        '#!/bin/sh\nprintf "curl %s\\n" "$*" >>"$CALL_LOG"\n'
        'while [ $# -gt 0 ]; do case "$1" in -o) : > "$2"; shift 2 ;; *) shift ;; esac; done\n',
    )
    run = harness.run(MIRROR, LIBRARY, then="ci_tpl_trust_bundle\n")
    assert run.returncode == 1
    assert "served no certificate" in run.output


# --------------------------------------------------------------------------
# Copying
# --------------------------------------------------------------------------

def copy_env(harness, **overrides):
    harness.env = variables(**overrides)
    harness.env["CI_TPL_AUTHFILE"] = str(harness.project_dir / "auth.json")
    harness.file("auth.json", "{}")


def test_a_copy_uses_oci_media_types(harness):
    """The export component stores these manifests in an OCI layout, which takes
    OCI media types only. --preserve-digests here would carry a docker v2
    manifest the receiving side cannot read."""
    copy_env(harness)
    harness.stub("skopeo", stdout="sha256:" + "a" * 64)
    run = harness.run(
        MIRROR, LIBRARY, then='ci_tpl_copy_ref docker.io/library/nginx:1.27 registry.example.com/mirror/library/nginx:1.27\n'
    )
    assert run.returncode == 0, run.output
    copies = [call for call in run.calls if " copy " in call]
    assert copies and "--format oci" in copies[0]
    assert "--preserve-digests" not in copies[0]


def test_a_dry_run_inspects_and_copies_nothing(harness):
    copy_env(harness, CI_TPL_DRY_RUN="true")
    harness.stub("skopeo", stdout="sha256:" + "b" * 64)
    run = harness.run(
        MIRROR, LIBRARY, then='ci_tpl_copy_ref docker.io/library/nginx:1.27 registry.example.com/mirror/library/nginx:1.27\n'
    )
    assert run.returncode == 0, run.output
    assert not [call for call in run.calls if " copy " in call]
    assert [call for call in run.calls if " inspect " in call]


def test_a_copy_that_never_succeeds_fails_the_job(harness):
    """Three rounds, then stop. The predecessor of this component recorded a
    digest line whether or not the copy worked."""
    copy_env(harness, )
    harness.env["CI_TPL_RETRY_DELAY"] = "0"
    harness.stub("skopeo", exit_code=1)
    run = harness.run(
        MIRROR, LIBRARY, then='ci_tpl_copy_ref docker.io/library/nginx:1.27 registry.example.com/mirror/library/nginx:1.27\n'
    )
    assert run.returncode == 1
    assert "failed three times" in run.output


# --------------------------------------------------------------------------
# Egress
# --------------------------------------------------------------------------

def test_no_proxy_means_direct_egress(harness):
    harness.env = variables()
    run = harness.run(MIRROR, EGRESS, then="echo \"[${HTTP_PROXY:-unset}]\"\n")
    assert run.returncode == 0
    assert "egress: direct" in run.stdout
    assert "[unset]" in run.stdout


def test_a_proxy_is_exported_under_both_spellings(harness):
    """curl reads the lower case names, skopeo and the AWS CLI the upper case
    ones. Exporting one of each pair is a proxy half the job ignores."""
    harness.env = variables(CI_TPL_EGRESS_PROXY="http://proxy.example.com:3128",
                           CI_TPL_NO_PROXY="s3.example.com")
    run = harness.run(
        MIRROR, EGRESS,
        then='echo "$HTTP_PROXY $HTTPS_PROXY $http_proxy $https_proxy $NO_PROXY $no_proxy"\n',
    )
    assert run.returncode == 0
    fields = run.stdout.strip().splitlines()[-1].split()
    assert fields == ["http://proxy.example.com:3128"] * 4 + ["s3.example.com"] * 2


def test_the_export_component_carries_the_same_library(harness):
    """Both components embed one copy of one file. A test that only drove the
    mirror would let the export drift."""
    harness.env = variables()
    run = harness.run(EXPORT, LIBRARY, then='ci_tpl_destination "quay.io/coreos/etcd:v3.5.0"\n')
    assert run.stdout.strip() == "coreos/etcd:v3.5.0"
