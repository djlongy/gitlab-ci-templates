"""Behaviour tests for runtime/promote/skopeo.sh.

The promotion is the last gate before an artifact is a release, so the parts
worth proving are the ones that could report success having published the wrong
thing: a digest that came from somewhere other than the stated source, a tag the
consumer did not write, and a target nobody read back.

skopeo is stubbed. A stub lets a test say "the registry answered with a
different manifest", which a real registry will not do on demand and which is
exactly the case that must fail.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
IMAGE_JSON = REPO_ROOT / "runtime" / "registry" / "image-json.sh"
CA_BUNDLE = REPO_ROOT / "runtime" / "registry" / "ca-bundle.sh"
HELPER = REPO_ROOT / "runtime" / "promote" / "skopeo.sh"

SOURCE = "registry.example/team/app"
TARGET = "other.example/team/app"
MANIFEST = json.dumps({"schemaVersion": 2, "manifests": []}, separators=(",", ":"))
DIGEST = "sha256:" + hashlib.sha256(MANIFEST.encode()).hexdigest()
OTHER = "sha256:" + "a" * 64


def env(tmp_path: Path, **extra) -> dict:
    base = {
        "PATH": f"{tmp_path}/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "CI_TPL_WORK": str(tmp_path),
        "CI_TPL_SOURCE_REPOSITORY": SOURCE,
        "CI_TPL_TARGET_REPOSITORY": TARGET,
        "CI_TPL_SOURCE_USERNAME_VARIABLE": "SRC_USER",
        "CI_TPL_SOURCE_PASSWORD_VARIABLE": "SRC_TOKEN",
        "CI_TPL_TARGET_USERNAME_VARIABLE": "DST_USER",
        "CI_TPL_TARGET_PASSWORD_VARIABLE": "DST_TOKEN",
    }
    base.update(extra)
    return base


def run(script: str, tmp_path: Path, **extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/sh", "-c", f". '{IMAGE_JSON}'\n. '{CA_BUNDLE}'\n. '{HELPER}'\n{script}"],
        cwd=tmp_path,
        env=env(tmp_path, **extra),
        capture_output=True,
        text=True,
    )


def stub_skopeo(tmp_path: Path, manifest: str = MANIFEST) -> Path:
    """A skopeo that records its arguments and serves one manifest."""
    binary = tmp_path / "bin" / "skopeo"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{tmp_path}/skopeo.log"\n'
        'if [ "$1" = "inspect" ]; then\n'
        f"  printf '%s' '{manifest}'\n"
        "fi\n"
        "exit 0\n"
    )
    binary.chmod(0o755)
    return binary


def image_json(tmp_path: Path, digest: str = DIGEST) -> Path:
    path = tmp_path / "image.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": SOURCE,
                "digest": digest,
                "reference": f"{SOURCE}@{digest}",
                "source_commit": "0" * 40,
                "pipeline_id": "1",
                "job_id": "2",
                "platforms": "linux/amd64",
                "subject_kind": "index",
                "created_at": "2026-09-17T00:00:00Z",
            },
            indent=2,
        )
    )
    return path


def test_an_explicit_digest_is_used_as_given(tmp_path):
    result = run("ci_tpl_promote_source_digest", tmp_path, CI_TPL_SOURCE_DIGEST=OTHER)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == OTHER


def test_a_source_digest_that_is_a_tag_is_refused(tmp_path):
    """Section 1: a tag is not an identity, and a promotion is where that bites."""
    result = run("ci_tpl_promote_source_digest", tmp_path, CI_TPL_SOURCE_DIGEST="v1.2.3")
    assert result.returncode != 0
    assert "not a sha256 digest" in result.stderr


def test_the_digest_comes_from_the_named_build_when_no_digest_is_given(tmp_path):
    identity = image_json(tmp_path)
    result = run(
        "ci_tpl_promote_source_digest", tmp_path, CI_TPL_IDENTITY_FILE=str(identity)
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == DIGEST


def test_neither_source_is_a_failure_and_not_an_empty_digest(tmp_path):
    result = run("ci_tpl_promote_source_digest", tmp_path)
    assert result.returncode != 0
    assert "neither source-digest nor a readable image.json" in result.stderr


def test_an_image_json_that_fails_its_own_schema_is_refused(tmp_path):
    identity = image_json(tmp_path)
    identity.write_text(identity.read_text().replace('"schema_version": 1', '"schema_version": 9'))
    result = run(
        "ci_tpl_promote_source_digest", tmp_path, CI_TPL_IDENTITY_FILE=str(identity)
    )
    assert result.returncode != 0
    assert "not image.json schema 1" in result.stderr


def test_tags_are_read_from_the_array_input(tmp_path):
    result = run("ci_tpl_promote_tags", tmp_path, CI_TPL_TARGET_TAGS='["1.2.3", "stable"]')
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["1.2.3", "stable"]


def test_an_empty_tag_list_is_refused(tmp_path):
    """skopeo cannot copy to a digest, so no tag means nothing would be published."""
    result = run("ci_tpl_promote_tags", tmp_path, CI_TPL_TARGET_TAGS="[]")
    assert result.returncode != 0
    assert "at least one tag" in result.stderr


def test_a_tag_carrying_shell_metacharacters_is_refused(tmp_path):
    result = run("ci_tpl_promote_tags", tmp_path, CI_TPL_TARGET_TAGS='["ok", "a;rm -rf /"]')
    assert result.returncode != 0
    assert "is not a valid tag name" in result.stderr


def test_the_two_auth_files_hold_the_two_identities(tmp_path):
    result = run(
        f'ci_tpl_promote_authfiles "{tmp_path}/auth" && '
        'cat "$CI_TPL_SOURCE_AUTHFILE" && echo && cat "$CI_TPL_TARGET_AUTHFILE"',
        tmp_path,
        SRC_USER="src", SRC_TOKEN="src-token", DST_USER="dst", DST_TOKEN="dst-token",
    )
    assert result.returncode == 0, result.stderr
    source, target = (json.loads(line) for line in result.stdout.strip().splitlines())
    assert list(source["auths"]) == ["registry.example"]
    assert list(target["auths"]) == ["other.example"]
    assert source["auths"]["registry.example"]["auth"] != target["auths"]["other.example"]["auth"]


def test_a_missing_target_credential_names_the_variable_it_read(tmp_path):
    """The 1.2.0 lesson: "credentials are not set" hid WHICH name was read."""
    result = run(
        f'ci_tpl_promote_authfiles "{tmp_path}/auth"',
        tmp_path,
        SRC_USER="src", SRC_TOKEN="src-token", DST_USER="dst",
    )
    assert result.returncode != 0
    assert "$DST_TOKEN" in result.stderr


def test_the_copy_names_the_digest_on_one_side_and_the_tag_on_the_other(tmp_path):
    stub_skopeo(tmp_path)
    result = run(
        f'CI_TPL_SOURCE_AUTHFILE={tmp_path}/s.json CI_TPL_TARGET_AUTHFILE={tmp_path}/t.json '
        f'ci_tpl_promote_copy "{DIGEST}" 1.2.3',
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    invocation = (tmp_path / "skopeo.log").read_text()
    assert "--all" in invocation
    assert f"docker://{SOURCE}@{DIGEST}" in invocation
    assert f"docker://{TARGET}:1.2.3" in invocation


def test_verification_passes_when_the_target_manifest_hashes_to_the_source_digest(tmp_path):
    stub_skopeo(tmp_path)
    result = run(
        f'CI_TPL_TARGET_AUTHFILE={tmp_path}/t.json ci_tpl_promote_verify "{DIGEST}" 1.2.3',
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert DIGEST in result.stdout


def test_verification_fails_when_the_target_holds_a_different_manifest(tmp_path):
    """The whole point of the component: a copy that reported success is not proof."""
    stub_skopeo(tmp_path, manifest='{"schemaVersion":2,"manifests":[{"other":true}]}')
    result = run(
        f'CI_TPL_TARGET_AUTHFILE={tmp_path}/t.json ci_tpl_promote_verify "{DIGEST}" 1.2.3',
        tmp_path,
    )
    assert result.returncode != 0
    assert "not the" in result.stderr


def test_the_result_file_records_the_digest_and_every_tag(tmp_path):
    result = run(
        f'ci_tpl_promote_result "{tmp_path}/out/promote-result.json" "{DIGEST}" 1.2.3 stable',
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads((tmp_path / "out" / "promote-result.json").read_text())
    assert record["schema_version"] == 1
    assert record["source"] == f"{SOURCE}@{DIGEST}"
    assert record["target"] == f"{TARGET}@{DIGEST}"
    assert record["digest"] == DIGEST
    assert record["tags"] == ["1.2.3", "stable"]
    assert record["created_at"].endswith("Z")
