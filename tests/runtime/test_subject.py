"""Behaviour tests for runtime/subject.py.

The record it writes has to be the one runtime/registry/image-json.sh writes,
field for field: a consumer may put a shell component and a Python component in
the same chain, and a downstream job reads whichever record arrived.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_helpers = importlib.util.spec_from_file_location(
    "runtime_helpers", Path(__file__).resolve().parent / "runtime_helpers.py"
)
helpers = importlib.util.module_from_spec(_helpers)
_helpers.loader.exec_module(helpers)

subject = helpers.load_runtime_module("subject.py")

DIGEST = "sha256:" + "ab" * 32
REPOSITORY = "registry.example.com/dev/platform/gitseed"
REFERENCE = f"{REPOSITORY}@{DIGEST}"


def test_a_build_job_reads_the_build_s_record_and_writes_nothing(tmp_path):
    result = subject.identity_file(
        "api:container-build-buildkit", "", str(tmp_path / "build/image.json"), str(tmp_path / "own")
    )
    assert result == tmp_path / "build/image.json"
    assert not (tmp_path / "own").exists()


def test_a_named_subject_is_recorded_in_the_job_s_own_artefact_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CI_COMMIT_SHA", "0" * 40)
    monkeypatch.setenv("CI_PIPELINE_ID", "4242")
    monkeypatch.setenv("CI_JOB_ID", "9001")
    result = subject.identity_file("", REFERENCE, "/nowhere/image.json", str(tmp_path / "own"))

    assert result == tmp_path / "own" / "image.json"
    written = json.loads(result.read_text())
    assert written["reference"] == REFERENCE
    assert written["repository"] == REPOSITORY
    assert written["digest"] == DIGEST
    assert written["source_commit"] == "0" * 40
    assert written["platforms"] == ["unresolved"]
    assert written["subject_kind"] == "unresolved"
    assert written["schema_version"] == 1


def test_the_record_matches_the_shell_helper_s_fields(tmp_path, monkeypatch):
    """One contract: the two implementations write the same keys."""
    monkeypatch.setenv("CI_COMMIT_SHA", "0" * 40)
    written = set(subject.subject_record(REFERENCE))
    shell = Path(__file__).resolve().parents[2] / "runtime" / "registry" / "image-json.sh"
    text = shell.read_text()
    for field in written:
        assert f'"{field}"' in text, f"image-json.sh writes no {field}"


def test_naming_both_is_refused(tmp_path):
    with pytest.raises(subject.SubjectError) as error:
        subject.identity_file("api:build", REFERENCE, "x", str(tmp_path))
    assert "build-job" in str(error.value) and "subject-reference" in str(error.value)


def test_naming_neither_is_refused_with_a_message_naming_both(tmp_path):
    with pytest.raises(subject.SubjectError) as error:
        subject.identity_file("", "", "x", str(tmp_path))
    assert "build-job" in str(error.value) and "subject-reference" in str(error.value)


@pytest.mark.parametrize(
    "reference",
    [
        f"{REPOSITORY}:1.2.3",
        f"{REPOSITORY}@sha256:tooshort",
        "gitseed@" + DIGEST,
        f"REGISTRY.EXAMPLE/dev/app@{DIGEST}",
    ],
)
def test_a_reference_that_is_not_repository_at_digest_is_refused(tmp_path, reference):
    with pytest.raises(subject.SubjectError):
        subject.identity_file("", reference, "x", str(tmp_path))
    assert not (tmp_path / "image.json").exists()


def test_the_cli_prints_the_path_and_reports_failure(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CI_COMMIT_SHA", "0" * 40)
    assert subject.main([
        "--subject-reference", REFERENCE,
        "--build-identity", "/nowhere/image.json",
        "--artifact-dir", str(tmp_path / "own"),
    ]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "own" / "image.json")

    assert subject.main([
        "--build-identity", "x", "--artifact-dir", str(tmp_path / "own"),
    ]) == 1
    assert "ERROR:" in capsys.readouterr().err
