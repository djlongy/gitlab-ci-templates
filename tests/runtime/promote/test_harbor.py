"""Behaviour tests for runtime/promote/harbor.py.

Audit 8.1 found three defects in `promote/harbor-promote.yml`, and each has a
test here that the old behaviour would fail:

  * it promoted `repo:${BUILDKIT_TAG}` and so shipped whatever that tag pointed
    at when the manual button was pressed. Promotion is now by digest, read from
    the builder's identity record, and a record without one is refused.
  * success was `HTTP 201`. The destination is now read back and its digest
    compared.
  * cosign signatures and attestations were never copied. They are copied
    explicitly and their absence fails unless the caller opted out.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_helpers = importlib.util.spec_from_file_location(
    "runtime_helpers", Path(__file__).resolve().parents[1] / "runtime_helpers.py"
)
helpers = importlib.util.module_from_spec(_helpers)
_helpers.loader.exec_module(helpers)

FakeResponse = helpers.FakeResponse
harbor = helpers.load_runtime_module("promote/harbor.py")

DIGEST = "sha256:" + "1" * 64
REGISTRY = "registry.example.com"
REPOSITORY = f"{REGISTRY}/dev/platform/demo"
REFERENCE = f"{REPOSITORY}@{DIGEST}"
SIGNATURE_TAG = DIGEST.replace(":", "-") + ".sig"
ATTESTATION_TAG = DIGEST.replace(":", "-") + ".att"


def write_identity(tmp_path: Path, **overrides) -> Path:
    record = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "digest": DIGEST,
        "reference": REFERENCE,
        "source_commit": "0" * 40,
    }
    record.update(overrides)
    path = tmp_path / "image.json"
    path.write_text(json.dumps(record))
    return path


class FakeHarbor:
    """A Harbor that holds a known set of artifacts per project."""

    def __init__(self, source_artifacts, destination_artifacts=(), copy_status=201):
        self.artifacts = {"dev": set(source_artifacts), "prod": set(destination_artifacts)}
        self.copy_status = copy_status
        self.copies = []
        self.tags = []

    def install(self, monkeypatch):
        outer = self

        def artifact(self, project, repo_path, reference):
            if reference in outer.artifacts.get(project, set()):
                return FakeResponse(200, {"digest": DIGEST if reference == DIGEST else reference})
            return FakeResponse(404, {"errors": [{"message": "not found"}]})

        def copy(self, destination_project, repo_path, source):
            outer.copies.append((destination_project, repo_path, source))
            if outer.copy_status in (201, 409):
                outer.artifacts.setdefault(destination_project, set()).add(source.split("@")[-1]
                                                                          if "@" in source
                                                                          else source.split(":", 2)[-1])
            return FakeResponse(outer.copy_status, {})

        def tag(self, project, repo_path, digest, name):
            outer.tags.append((project, name))
            return FakeResponse(201, {})

        monkeypatch.setattr(harbor.Harbor, "artifact", artifact)
        monkeypatch.setattr(harbor.Harbor, "copy", copy)
        monkeypatch.setattr(harbor.Harbor, "tag", tag)


def arguments(tmp_path: Path, identity: Path, *extra: str) -> list[str]:
    return [
        "promote", "--identity", str(identity), "--destination-project", "prod",
        "--username-variable", "HARBOR_USER", "--password-variable", "HARBOR_PASSWORD",
        "--output", str(tmp_path / "promotion-result.json"), *extra,
    ]


def with_credentials(monkeypatch):
    monkeypatch.setenv("HARBOR_USER", "robot$promote")
    monkeypatch.setenv("HARBOR_PASSWORD", "not-a-real-token")


def test_promotion_copies_the_digest_and_its_referrers(tmp_path: Path, monkeypatch):
    fake = FakeHarbor([DIGEST, SIGNATURE_TAG, ATTESTATION_TAG])
    fake.install(monkeypatch)
    with_credentials(monkeypatch)
    identity = write_identity(tmp_path)

    assert harbor.main(arguments(tmp_path, identity, "--tag", "1.0.0")) == 0

    record = json.loads((tmp_path / "promotion-result.json").read_text())
    assert record["digest"] == DIGEST
    assert record["verified_destination_digest"] == DIGEST
    assert record["destination_reference"] == f"{REGISTRY}/prod/platform/demo@{DIGEST}"
    assert sorted(record["referrers_copied"]) == sorted([SIGNATURE_TAG, ATTESTATION_TAG])
    assert record["tags_applied"] == ["1.0.0"]
    assert fake.tags == [("prod", "1.0.0")]
    # Every copy names the digest or a cosign referrer tag; never a release tag.
    assert all("1.0.0" not in source for _, _, source in fake.copies)


def test_promotion_refuses_an_identity_without_a_digest(tmp_path: Path, monkeypatch, capsys):
    """This is the old behaviour: promote whatever the tag points at."""
    with_credentials(monkeypatch)
    identity = write_identity(
        tmp_path, digest="v1.0.0", reference=f"{REPOSITORY}:v1.0.0"
    )
    assert harbor.main(arguments(tmp_path, identity)) == 1
    assert "no sha256 digest" in capsys.readouterr().err


def test_promotion_fails_when_the_destination_cannot_be_read_back(tmp_path: Path, monkeypatch, capsys):
    """HTTP 201 is not proof that the destination holds the digest."""
    fake = FakeHarbor([DIGEST, SIGNATURE_TAG])
    fake.install(monkeypatch)
    monkeypatch.setattr(
        harbor.Harbor, "copy", lambda self, *a, **k: FakeResponse(201, {})
    )
    with_credentials(monkeypatch)
    identity = write_identity(tmp_path)

    assert harbor.main(arguments(tmp_path, identity)) == 1
    assert "could not be read back" in capsys.readouterr().err


def test_promotion_fails_when_the_image_is_unsigned(tmp_path: Path, monkeypatch, capsys):
    fake = FakeHarbor([DIGEST])
    fake.install(monkeypatch)
    with_credentials(monkeypatch)
    identity = write_identity(tmp_path)

    assert harbor.main(arguments(tmp_path, identity)) == 1
    assert "refusing to promote an unsigned image" in capsys.readouterr().err


def test_unsigned_promotion_is_possible_only_by_explicit_opt_out(tmp_path: Path, monkeypatch):
    fake = FakeHarbor([DIGEST])
    fake.install(monkeypatch)
    with_credentials(monkeypatch)
    identity = write_identity(tmp_path)

    assert harbor.main(
        arguments(tmp_path, identity, "--require-signatures", "false")
    ) == 0
    record = json.loads((tmp_path / "promotion-result.json").read_text())
    assert sorted(record["referrers_absent"]) == sorted([SIGNATURE_TAG, ATTESTATION_TAG])


def test_promotion_fails_when_the_candidate_is_absent(tmp_path: Path, monkeypatch, capsys):
    FakeHarbor([]).install(monkeypatch)
    with_credentials(monkeypatch)
    identity = write_identity(tmp_path)
    assert harbor.main(arguments(tmp_path, identity)) == 1
    assert "not in the candidate project" in capsys.readouterr().err


def test_promotion_fails_without_registry_credentials(tmp_path: Path, monkeypatch, capsys):
    FakeHarbor([DIGEST, SIGNATURE_TAG]).install(monkeypatch)
    monkeypatch.delenv("HARBOR_USER", raising=False)
    monkeypatch.delenv("HARBOR_PASSWORD", raising=False)
    identity = write_identity(tmp_path)
    assert harbor.main(arguments(tmp_path, identity)) == 1
    assert "must both be" in capsys.readouterr().err


def test_promotion_refuses_a_same_project_promotion(tmp_path: Path, monkeypatch, capsys):
    FakeHarbor([DIGEST, SIGNATURE_TAG]).install(monkeypatch)
    with_credentials(monkeypatch)
    identity = write_identity(tmp_path)
    assert harbor.main(
        ["promote", "--identity", str(identity), "--destination-project", "dev",
         "--username-variable", "HARBOR_USER", "--password-variable", "HARBOR_PASSWORD",
         "--output", str(tmp_path / "out.json")]
    ) == 1
    assert "not a promotion" in capsys.readouterr().err


def test_repository_is_split_into_registry_project_and_path():
    assert harbor.split_repository("registry.example.com/dev/team/app") == (
        "registry.example.com", "dev", "team/app"
    )


def test_repository_path_is_double_encoded_for_harbor():
    assert harbor.encode_repository("team/app") == "team%252Fapp"


def test_referrer_tags_follow_the_cosign_naming():
    assert harbor.referrer_tags(DIGEST) == [SIGNATURE_TAG, ATTESTATION_TAG]
