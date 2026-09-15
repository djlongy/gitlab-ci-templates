"""Behaviour tests for runtime/registry/image-json.sh.

Every test below names the audit defect it prevents. The three build templates
this component family replaces all wrote a TAG into a field called digest and
carried on with exit 0 when no digest could be found; section 9.1 forbids the
first and section 10.1 forbids the second. A test that only checked the happy
path would pass against that old behaviour too, so each case here is written to
fail against it.

The helper is POSIX sh because the builder images (moby/buildkit,
golang:*-alpine, gradle:*-alpine) are busybox userlands with no jq and no
python3. It is exercised through /bin/sh for the same reason.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "runtime" / "registry" / "image-json.sh"

VALID_DIGEST = "sha256:" + "ab" * 32
OTHER_DIGEST = "sha256:" + "cd" * 32
REPOSITORY = "registry.example.com/dev/platform/demo-app"
COMMIT = "0" * 39 + "1"


def run(script: str, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    """Source the helper and run a shell snippet against it."""
    full_env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "CI_COMMIT_SHA": COMMIT,
        "CI_PIPELINE_ID": "4242",
        "CI_JOB_ID": "9001",
    }
    full_env.update(env or {})
    return subprocess.run(
        ["/bin/sh", "-c", f". '{HELPER}'\n{script}"],
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
    )


# --- digest validation: the defect in build/buildkit.yml and build/jib.yml ---


def test_a_tag_is_refused_where_a_digest_is_required(tmp_path):
    """build/jib.yml wrote `IMAGE_DIGEST=${JIB_TARGET_IMAGE}`, a tag."""
    result = run(f"ci_tpl_require_digest '{REPOSITORY}:1.2.3'", tmp_path)
    assert result.returncode != 0
    assert "not a sha256 digest" in result.stderr


def test_an_empty_digest_is_refused(tmp_path):
    result = run("ci_tpl_require_digest ''", tmp_path)
    assert result.returncode != 0
    assert "refusing to continue" in result.stderr


def test_a_truncated_digest_is_refused(tmp_path):
    result = run("ci_tpl_require_digest 'sha256:abcdef'", tmp_path)
    assert result.returncode != 0


def test_a_real_digest_is_accepted(tmp_path):
    assert run(f"ci_tpl_require_digest '{VALID_DIGEST}'", tmp_path).returncode == 0


# --- repository validation: image-repository must carry no tag ---


@pytest.mark.parametrize(
    "value",
    [
        f"{REPOSITORY}:1.2.3",
        f"{REPOSITORY}@{VALID_DIGEST}",
        "",
        "noregistry",
    ],
)
def test_a_repository_that_is_not_a_bare_repository_is_refused(tmp_path, value):
    assert run(f"ci_tpl_require_repository '{value}'", tmp_path).returncode != 0


@pytest.mark.parametrize(
    "value",
    [REPOSITORY, "registry.example.com:8443/dev/platform/demo-app"],
)
def test_a_bare_repository_is_accepted(tmp_path, value):
    assert run(f"ci_tpl_require_repository '{value}'", tmp_path).returncode == 0


# --- BuildKit metadata: the defect that the metadata file was never read ---


def buildkit_metadata(tmp_path: Path, *, manifest_digest: str | None, media_type: str) -> Path:
    body: dict[str, object] = {
        "containerimage.config.digest": OTHER_DIGEST,
        "containerimage.descriptor": {
            "mediaType": media_type,
            "digest": manifest_digest or OTHER_DIGEST,
            "size": 1234,
        },
        "image.name": f"{REPOSITORY}:1.2.3",
    }
    if manifest_digest is not None:
        body["containerimage.digest"] = manifest_digest
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(body, indent=2))
    return path


def test_the_manifest_digest_is_read_from_the_metadata_file_not_the_config_digest(tmp_path):
    """build/buildkit.yml scraped a wget header dump instead, and its comment
    claimed the metadata file only held the config digest. It holds both."""
    path = buildkit_metadata(tmp_path, manifest_digest=VALID_DIGEST, media_type="application/vnd.oci.image.manifest.v1+json")
    result = run(f"ci_tpl_buildkit_digest '{path}'", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == VALID_DIGEST
    assert result.stdout.strip() != OTHER_DIGEST


def test_a_metadata_file_without_a_manifest_digest_fails(tmp_path):
    """The old template printed a warning here and exited 0 with a tag."""
    path = buildkit_metadata(tmp_path, manifest_digest=None, media_type="application/vnd.oci.image.manifest.v1+json")
    result = run(f"ci_tpl_buildkit_digest '{path}'", tmp_path)
    assert result.returncode != 0
    assert "refusing to continue" in result.stderr


def test_subject_kind_comes_from_the_pushed_descriptor_media_type(tmp_path):
    """The platform list is what was requested; the descriptor is what was
    pushed. A single-platform request that BuildKit still exported as an index
    must be recorded as an index."""
    path = buildkit_metadata(
        tmp_path,
        manifest_digest=VALID_DIGEST,
        media_type="application/vnd.oci.image.index.v1+json",
    )
    result = run(f"ci_tpl_subject_kind_from_metadata '{path}' 'linux/amd64'", tmp_path)
    assert result.stdout.strip() == "index"


def test_subject_kind_falls_back_to_the_platform_list(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    assert run(f"ci_tpl_subject_kind_from_metadata '{empty}' 'linux/amd64,linux/arm64'", tmp_path).stdout.strip() == "index"
    assert run(f"ci_tpl_subject_kind_from_metadata '{empty}' 'linux/amd64'", tmp_path).stdout.strip() == "manifest"


# --- ko: the defect that the last line of merged stdout/stderr became the identity ---


def test_a_build_log_line_is_not_accepted_as_an_image_reference(tmp_path):
    """build/ko.yml ran `ko build ... 2>&1 | tail -1` and wrote the result into
    IMAGE_DIGEST. Any warning on the final line became the image identity."""
    result = run(
        "ci_tpl_reference_digest 'WARNING: falling back to the default base image'",
        tmp_path,
    )
    assert result.returncode != 0
    assert "not a repository@digest reference" in result.stderr


def test_a_reference_carrying_a_tag_instead_of_a_digest_is_refused(tmp_path):
    result = run(f"ci_tpl_reference_digest '{REPOSITORY}:latest'", tmp_path)
    assert result.returncode != 0


def test_a_real_reference_yields_its_digest(tmp_path):
    result = run(f"ci_tpl_reference_digest '{REPOSITORY}@{VALID_DIGEST}'", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == VALID_DIGEST


# --- image.json: the file none of the three builders produced ---


def test_image_json_is_written_and_validates(tmp_path):
    out = tmp_path / ".ci-artifacts" / "api" / "container-build-buildkit"
    result = run(
        f"ci_tpl_write_image_json '{out}' '{REPOSITORY}' '{VALID_DIGEST}' 'linux/amd64' 'manifest'",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    written = json.loads((out / "image.json").read_text())
    assert written == {
        "schema_version": 1,
        "repository": REPOSITORY,
        "digest": VALID_DIGEST,
        "reference": f"{REPOSITORY}@{VALID_DIGEST}",
        "source_commit": COMMIT,
        "pipeline_id": "4242",
        "job_id": "9001",
        "platforms": ["linux/amd64"],
        "subject_kind": "manifest",
        "created_at": written["created_at"],
    }
    assert written["created_at"].endswith("Z")
    # Section 9.1: the dotenv is a convenience, never the sole identity.
    assert (out / "build.env").read_text().splitlines() == [
        f"CI_TPL_IMAGE_REFERENCE={REPOSITORY}@{VALID_DIGEST}",
        "CI_TPL_IMAGE_PUSHED=true",
    ]


def test_multiple_platforms_are_recorded_as_a_json_array(tmp_path):
    out = tmp_path / "a"
    run(
        f"ci_tpl_write_image_json '{out}' '{REPOSITORY}' '{VALID_DIGEST}' 'linux/amd64,linux/arm64' 'index'",
        tmp_path,
    )
    written = json.loads((out / "image.json").read_text())
    assert written["platforms"] == ["linux/amd64", "linux/arm64"]
    assert written["subject_kind"] == "index"


def test_writing_image_json_with_a_tag_as_the_digest_fails(tmp_path):
    out = tmp_path / "a"
    result = run(
        f"ci_tpl_write_image_json '{out}' '{REPOSITORY}' '1.2.3' 'linux/amd64' 'manifest'",
        tmp_path,
    )
    assert result.returncode != 0
    assert not (out / "image.json").exists()


def test_pipeline_and_job_ids_are_serialised_as_strings(tmp_path):
    """Section 9.1 requires them serialised consistently as strings, so a
    consumer never has to guess whether to compare 42 or "42"."""
    out = tmp_path / "a"
    run(
        f"ci_tpl_write_image_json '{out}' '{REPOSITORY}' '{VALID_DIGEST}' 'linux/amd64' 'manifest'",
        tmp_path,
    )
    raw = (out / "image.json").read_text()
    assert '"pipeline_id": "4242"' in raw
    assert '"job_id": "9001"' in raw


# --- validation of a downloaded image.json, which consumers run ---


def write_image_json(tmp_path: Path, **overrides) -> Path:
    body = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "digest": VALID_DIGEST,
        "reference": f"{REPOSITORY}@{VALID_DIGEST}",
        "source_commit": COMMIT,
        "pipeline_id": "4242",
        "job_id": "9001",
        "platforms": ["linux/amd64"],
        "subject_kind": "manifest",
        "created_at": "2026-09-15T00:00:00Z",
    }
    body.update(overrides)
    path = tmp_path / "image.json"
    path.write_text(json.dumps(body, indent=2))
    return path


def test_a_well_formed_image_json_validates(tmp_path):
    path = write_image_json(tmp_path)
    assert run(f"ci_tpl_validate_image_json '{path}'", tmp_path).returncode == 0


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"reference": f"{REPOSITORY}@{OTHER_DIGEST}"}, "disagrees"),
        ({"digest": f"{REPOSITORY}:1.2.3"}, "not a sha256 digest"),
        ({"source_commit": "abc"}, "not a full commit sha"),
        ({"subject_kind": "blob"}, "neither manifest nor index"),
        ({"schema_version": 2}, "not image.json schema 1"),
        ({"created_at": ""}, "records no created_at"),
    ],
)
def test_a_corrupt_image_json_is_refused(tmp_path, overrides, expected):
    path = write_image_json(tmp_path, **overrides)
    result = run(f"ci_tpl_validate_image_json '{path}'", tmp_path)
    assert result.returncode != 0
    assert expected in result.stderr


def test_a_missing_image_json_is_refused(tmp_path):
    result = run(f"ci_tpl_validate_image_json '{tmp_path}/absent.json'", tmp_path)
    assert result.returncode != 0
    assert "does not exist" in result.stderr


def test_the_reference_helper_validates_before_printing(tmp_path):
    good = write_image_json(tmp_path)
    assert run(f"ci_tpl_image_reference '{good}'", tmp_path).stdout.strip() == (
        f"{REPOSITORY}@{VALID_DIGEST}"
    )
    bad = write_image_json(tmp_path, digest="1.2.3")
    result = run(f"ci_tpl_image_reference '{bad}'", tmp_path)
    assert result.returncode != 0
    assert result.stdout.strip() == ""


# --- registry auth ---


def test_registry_auth_is_written_without_a_wrapped_base64_line(tmp_path):
    """base64 wraps at 76 columns. A long robot credential wrapped mid-string
    produces a config.json no client can parse, and the build then fails with an
    authentication error that points nowhere near the cause."""
    long_password = "p" * 120
    result = run(
        f"ci_tpl_write_registry_auth '{tmp_path}/dockercfg' 'registry.example.com'",
        tmp_path,
        env={
            "CI_TPL_REGISTRY_USER": "robot$dev+ci",
            "CI_TPL_REGISTRY_PASSWORD": long_password,
        },
    )
    assert result.returncode == 0, result.stderr
    raw = (tmp_path / "dockercfg" / "config.json").read_text()
    assert "\n" not in raw
    auth = json.loads(raw)["auths"]["registry.example.com"]["auth"]
    import base64

    assert base64.b64decode(auth).decode() == f"robot$dev+ci:{long_password}"


def test_registry_auth_fails_when_credentials_are_absent(tmp_path):
    """Section 10.1: a push job whose credentials are unavailable fails."""
    result = run(
        f"ci_tpl_write_registry_auth '{tmp_path}/dockercfg' 'registry.example.com'",
        tmp_path,
    )
    assert result.returncode != 0
    assert "cannot push" in result.stderr


def test_the_registry_host_is_the_first_path_segment(tmp_path):
    assert run(f"ci_tpl_registry_host '{REPOSITORY}'", tmp_path).stdout.strip() == (
        "registry.example.com"
    )


# --- array inputs, which GitLab renders as a JSON array literal in a string ---


def test_a_json_array_literal_splits_into_one_value_per_line(tmp_path):
    result = run("""ci_tpl_json_list '["one", "two three"]'""", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["one", "two three"]


def test_an_empty_json_array_yields_no_values(tmp_path):
    result = run("""ci_tpl_json_list '[]'""", tmp_path)
    assert result.returncode == 0
    assert result.stdout == ""


def test_the_prefixed_form_the_components_pass_is_read_the_same_way(tmp_path):
    """A component cannot put a placeholder alone in a variable value: GitLab
    substitutes the array itself and rejects a variable holding a list. It sends
    `json ["1.2.3"]` instead, so the prefix must be ignored here."""
    plain = run("""ci_tpl_json_list '["1.2.3", "latest"]'""", tmp_path)
    prefixed = run("""ci_tpl_json_list 'json ["1.2.3", "latest"]'""", tmp_path)
    assert prefixed.returncode == 0, prefixed.stderr
    assert prefixed.stdout.splitlines() == ["1.2.3", "latest"]
    assert prefixed.stdout == plain.stdout
    assert run("""ci_tpl_json_list 'json []'""", tmp_path).stdout == ""


def test_an_unexpanded_variable_is_not_accepted_as_a_tag(tmp_path):
    """The tag default is $CI_COMMIT_SHORT_SHA. If GitLab has not expanded it by
    the time the job runs, pushing the literal string is worse than failing."""
    result = run("""ci_tpl_require_tag '$CI_COMMIT_SHORT_SHA'""", tmp_path)
    assert result.returncode != 0
    assert "not a usable image tag" in result.stderr


@pytest.mark.parametrize("tag", ["1.2.3", "abc1234", "v1.2.3-rc.1", "latest"])
def test_real_tags_are_accepted(tmp_path, tag):
    assert run(f"ci_tpl_require_tag '{tag}'", tmp_path).returncode == 0


@pytest.mark.parametrize("value", ["not-an-assignment", "=novalue", "1BAD=x"])
def test_a_malformed_build_argument_is_refused(tmp_path, value):
    assert run(f"ci_tpl_require_build_arg '{value}'", tmp_path).returncode != 0


def test_a_name_value_build_argument_is_accepted(tmp_path):
    assert run("ci_tpl_require_build_arg 'BASE_IMAGE=nginx:1.27'", tmp_path).returncode == 0


def test_the_repository_half_of_a_reference_can_be_compared_to_the_input(tmp_path):
    """ko and Jib report the reference they actually pushed. Comparing its
    repository to image-repository is what turns "the tool printed something"
    into "the tool pushed what this job asked for"."""
    result = run(
        f"ci_tpl_reference_repository '{REPOSITORY}@{VALID_DIGEST}'", tmp_path
    )
    assert result.stdout.strip() == REPOSITORY


def test_a_build_that_did_not_push_records_that_and_writes_no_image_json(tmp_path):
    out = tmp_path / "a"
    result = run(f"ci_tpl_write_unpushed_env '{out}'", tmp_path)
    assert result.returncode == 0, result.stderr
    assert (out / "build.env").read_text().strip() == "CI_TPL_IMAGE_PUSHED=false"
    assert not (out / "image.json").exists()
