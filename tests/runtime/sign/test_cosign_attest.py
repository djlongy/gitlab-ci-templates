"""Behaviour tests for runtime/sign/cosign_attest.py.

Audit 5.6 called this template's missing-credential path the most serious defect
in the repository:

    if [ -z "${VAULT_TOKEN}" ]; then
      echo "WARN: No VAULT_TOKEN - skipping cosign signing"
      exit 0
    fi

A broken Vault role produced a green job and an unsigned image. Missing SBOM and
missing vulnerability evidence took the same path, and the cosign binary was
curled from GitHub with no checksum at all. Each of those is a failing test here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_helpers = importlib.util.spec_from_file_location(
    "runtime_helpers", Path(__file__).resolve().parents[1] / "runtime_helpers.py"
)
helpers = importlib.util.module_from_spec(_helpers)
_helpers.loader.exec_module(helpers)

FakeResponse = helpers.FakeResponse
cosign = helpers.load_runtime_module("sign/cosign_attest.py")

DIGEST = "sha256:" + "2" * 64
REPOSITORY = "registry.example.com/dev/platform/demo"
REFERENCE = f"{REPOSITORY}@{DIGEST}"
CHECKSUMS = Path(__file__).resolve().parents[3] / "runtime" / "sign" / "cosign_checksums.txt"
BINARY = b"#!/bin/true\n"
BINARY_SHA256 = hashlib.sha256(BINARY).hexdigest()


class Recorder:
    """Collects the cosign invocations instead of running cosign."""

    def __init__(self, exit_codes=None):
        self.calls = []
        self.exit_codes = exit_codes or {}

    def __call__(self, arguments, env=None, check=False):
        self.calls.append(list(arguments))
        verb = arguments[1] if len(arguments) > 1 else ""
        code = self.exit_codes.get(verb, 0)
        return type("Completed", (), {"returncode": code})()


@pytest.fixture
def checksums(tmp_path: Path) -> Path:
    """A checksum file pinning the fake binary these tests download."""
    path = tmp_path / "cosign_checksums.txt"
    path.write_text(
        f"{BINARY_SHA256}  cosign-linux-amd64\n{BINARY_SHA256}  cosign-linux-arm64\n"
    )
    return path


def write_inputs(tmp_path: Path, subject_reference: str = REFERENCE) -> dict:
    identity = tmp_path / "image.json"
    identity.write_text(json.dumps({
        "repository": REPOSITORY, "digest": DIGEST, "reference": REFERENCE,
        "source_commit": "0" * 40, "pipeline_id": "9",
    }))
    sbom = tmp_path / "sbom.cdx.json"
    sbom.write_text(json.dumps({"bomFormat": "CycloneDX", "components": []}))
    subject = tmp_path / "subject.json"
    subject.write_text(json.dumps({"image_reference": subject_reference}))
    report = tmp_path / "scan-result.json"
    report.write_text(json.dumps({"schema_version": 1, "status": "completed"}))
    return {"identity": identity, "sbom": sbom, "subject": subject, "report": report}


def arguments(tmp_path: Path, files: dict, checksums: Path, *extra: str) -> list[str]:
    return [
        "sign",
        "--identity", str(files["identity"]),
        "--sbom", str(files["sbom"]),
        "--subject", str(files["subject"]),
        "--vulnerability-report", str(files["report"]),
        "--output", str(tmp_path / "attestation-result.json"),
        "--key", "hashivault://cosign",
        "--vault-address", "https://vault.example:8200",
        "--vault-auth-path", "jwt",
        "--vault-role", "gitlab-ci-demo",
        "--checksums", str(checksums),
        "--install-dir", str(tmp_path / "bin"),
        *extra,
    ]


def install_doubles(monkeypatch, *, login=None, download=None, recorder=None):
    recorder = recorder or Recorder()

    def fake_request(method, url, *, headers=None, body=None, timeout=30):
        if "releases/download" in url:
            return download if download is not None else FakeResponse(200, body=BINARY)
        if url.endswith("/login"):
            return login if login is not None else FakeResponse(
                200, {"auth": {"client_token": "s.not-a-real-token"}}
            )
        raise AssertionError(f"unexpected request to {url}")

    monkeypatch.setattr(cosign, "request", fake_request)
    monkeypatch.setattr(cosign.subprocess, "run", recorder)
    monkeypatch.setenv("VAULT_ID_TOKEN", "a.jwt.value")
    return recorder


def test_signing_attaches_the_signature_and_both_attestations(tmp_path, monkeypatch, checksums):
    recorder = install_doubles(monkeypatch)
    files = write_inputs(tmp_path)

    assert cosign.main(arguments(tmp_path, files, checksums)) == 0

    verbs = [call[1] for call in recorder.calls]
    assert verbs == ["public-key", "sign", "attest", "attest"]
    assert all(call[-1] == REFERENCE for call in recorder.calls[1:])

    record = json.loads((tmp_path / "attestation-result.json").read_text())
    assert record["subject"] == REFERENCE
    assert record["signer"]["vault_role"] == "gitlab-ci-demo"
    assert record["signer"]["binary_sha256"] == BINARY_SHA256
    assert [item["kind"] for item in record["attached"]] == ["signature", "cyclonedx", "vuln"]
    assert record["transparency_log_upload"] is False


def test_no_slsa_provenance_is_fabricated(tmp_path, monkeypatch, checksums):
    """Section 11.1: hand-written source metadata is not a verified SLSA level."""
    recorder = install_doubles(monkeypatch)
    files = write_inputs(tmp_path)
    cosign.main(arguments(tmp_path, files, checksums))

    flattened = " ".join(" ".join(call) for call in recorder.calls)
    assert "slsaprovenance" not in flattened


def test_missing_vault_credential_fails_the_job(tmp_path, monkeypatch, checksums, capsys):
    install_doubles(monkeypatch)
    monkeypatch.delenv("VAULT_ID_TOKEN", raising=False)
    files = write_inputs(tmp_path)

    assert cosign.main(arguments(tmp_path, files, checksums)) == 1
    assert "is empty" in capsys.readouterr().err
    assert not (tmp_path / "attestation-result.json").exists()


def test_rejected_vault_login_fails_the_job(tmp_path, monkeypatch, checksums, capsys):
    install_doubles(monkeypatch, login=FakeResponse(403, body=b"permission denied"))
    files = write_inputs(tmp_path)

    assert cosign.main(arguments(tmp_path, files, checksums)) == 1
    assert "Vault rejected the JWT login" in capsys.readouterr().err


def test_vault_login_without_a_client_token_fails(tmp_path, monkeypatch, checksums):
    install_doubles(monkeypatch, login=FakeResponse(200, {"auth": {}}))
    files = write_inputs(tmp_path)
    assert cosign.main(arguments(tmp_path, files, checksums)) == 1


def test_a_cosign_binary_that_does_not_match_the_pin_is_never_run(
    tmp_path, monkeypatch, checksums, capsys
):
    recorder = install_doubles(monkeypatch, download=FakeResponse(200, body=b"tampered"))
    files = write_inputs(tmp_path)

    assert cosign.main(arguments(tmp_path, files, checksums)) == 1
    assert "does not match the pinned checksum" in capsys.readouterr().err
    assert recorder.calls == []


def test_missing_sbom_evidence_fails_the_job(tmp_path, monkeypatch, checksums, capsys):
    install_doubles(monkeypatch)
    files = write_inputs(tmp_path)
    files["sbom"].unlink()

    assert cosign.main(arguments(tmp_path, files, checksums)) == 1
    assert "missing or empty" in capsys.readouterr().err


def test_missing_vulnerability_evidence_fails_the_job(tmp_path, monkeypatch, checksums):
    install_doubles(monkeypatch)
    files = write_inputs(tmp_path)
    files["report"].unlink()
    assert cosign.main(arguments(tmp_path, files, checksums)) == 1


def test_an_sbom_for_another_image_is_not_attested(tmp_path, monkeypatch, checksums, capsys):
    install_doubles(monkeypatch)
    files = write_inputs(tmp_path, subject_reference=f"{REPOSITORY}@sha256:{'3' * 64}")

    assert cosign.main(arguments(tmp_path, files, checksums)) == 1
    assert "not the image being signed" in capsys.readouterr().err


def test_a_failing_cosign_command_fails_the_job(tmp_path, monkeypatch, checksums, capsys):
    install_doubles(monkeypatch, recorder=Recorder(exit_codes={"sign": 1}))
    files = write_inputs(tmp_path)

    assert cosign.main(arguments(tmp_path, files, checksums)) == 1
    assert "cosign sign failed" in capsys.readouterr().err


def test_signing_refuses_an_identity_without_a_digest(tmp_path, monkeypatch, checksums):
    install_doubles(monkeypatch)
    files = write_inputs(tmp_path)
    files["identity"].write_text(json.dumps({
        "repository": REPOSITORY, "digest": "v1", "reference": f"{REPOSITORY}:v1",
    }))
    assert cosign.main(arguments(tmp_path, files, checksums)) == 1


def test_an_unpinned_architecture_is_refused(tmp_path, monkeypatch, checksums, capsys):
    install_doubles(monkeypatch)
    monkeypatch.setattr(
        cosign.os, "uname", lambda: type("Uname", (), {"machine": "riscv64"})()
    )
    files = write_inputs(tmp_path)

    assert cosign.main(arguments(tmp_path, files, checksums)) == 1
    assert "no cosign checksum is pinned" in capsys.readouterr().err


def test_the_committed_checksum_file_pins_both_supported_architectures():
    """The shipped pin, not a fixture: a stale file would sign nothing."""
    assert len(cosign.expected_checksum(CHECKSUMS, "cosign-linux-amd64")) == 64
    assert len(cosign.expected_checksum(CHECKSUMS, "cosign-linux-arm64")) == 64


def test_an_unpinned_asset_has_no_checksum():
    with pytest.raises(cosign.SigningFailure):
        cosign.expected_checksum(CHECKSUMS, "cosign-windows-amd64.exe")
