"""Behaviour tests for runtime/dtrack/dtrack.py.

Section 10.3 names the defect this component exists to fix: "Dependency-Track
upload acceptance likewise does not establish completed analysis or policy
compliance." The template it replaces checked for a 2xx, printed "SBOM uploaded
successfully" and ran under `allow_failure: true` (audit 5.5).
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
dtrack = helpers.load_runtime_module("dtrack/dtrack.py")
HttpError = dtrack.HttpError

REFERENCE = "registry.example.com/dev/platform/demo@sha256:" + "e" * 64


def prepare(tmp_path: Path, reference: str = REFERENCE) -> tuple[Path, Path]:
    sbom = tmp_path / "sbom.cdx.json"
    sbom.write_text(json.dumps({"bomFormat": "CycloneDX", "components": []}))
    subject = tmp_path / "subject.json"
    subject.write_text(json.dumps({"image_reference": reference, "sbom_sha256": "f" * 64}))
    return sbom, subject


def arguments(tmp_path: Path, *extra: str) -> list[str]:
    sbom, subject = prepare(tmp_path)
    return [
        "upload", "--url", "https://dtrack.example",
        "--api-key-variable", "DTRACK_API_KEY",
        "--sbom", str(sbom), "--subject", str(subject),
        "--project-name", "demo", "--project-version", "1.0.0",
        "--output", str(tmp_path / "dtrack-upload-result.json"),
        "--poll-interval", "0", *extra,
    ]


def responder(sequence):
    """Return a request double that replays a list of responses in order."""
    calls = []

    def fake_request(method, url, *, headers=None, body=None, timeout=30):
        calls.append((method, url, headers, body))
        return sequence[min(len(calls) - 1, len(sequence) - 1)]

    fake_request.calls = calls
    return fake_request


def test_upload_waits_for_the_analysis_to_finish(tmp_path: Path, monkeypatch):
    request = responder([
        FakeResponse(200, {"token": "abc-123"}),
        FakeResponse(200, {"processing": True}),
        FakeResponse(200, {"processing": False}),
    ])
    monkeypatch.setattr(dtrack, "request", request)
    monkeypatch.setenv("DTRACK_API_KEY", "not-a-real-key")

    assert dtrack.main(arguments(tmp_path)) == 0

    record = json.loads((tmp_path / "dtrack-upload-result.json").read_text())
    assert record["status"] == "analysed"
    assert record["analysis_completed"] is True
    assert record["bom_token"] == "abc-123"
    assert record["subject"] == REFERENCE
    assert [call[0] for call in request.calls] == ["PUT", "GET", "GET"]


def test_upload_sends_the_key_as_a_header_and_never_in_the_url(tmp_path: Path, monkeypatch):
    request = responder([FakeResponse(200, {"token": "t"}), FakeResponse(200, {"processing": False})])
    monkeypatch.setattr(dtrack, "request", request)
    monkeypatch.setenv("DTRACK_API_KEY", "secret-value")

    dtrack.main(arguments(tmp_path))

    method, url, headers, _ = request.calls[0]
    assert headers["X-Api-Key"] == "secret-value"
    assert "secret-value" not in url


def test_upload_fails_when_the_analysis_never_completes(tmp_path: Path, monkeypatch, capsys):
    """An accepted upload is not a completed analysis (section 10.3)."""
    monkeypatch.setattr(dtrack, "request", responder([
        FakeResponse(200, {"token": "t"}),
        FakeResponse(200, {"processing": True}),
    ]))
    monkeypatch.setenv("DTRACK_API_KEY", "k")

    assert dtrack.main(arguments(tmp_path, "--analysis-timeout", "0")) == 1
    assert "still processing" in capsys.readouterr().err


def test_upload_fails_when_the_server_returns_no_token(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(dtrack, "request", responder([FakeResponse(200, {"ok": True})]))
    monkeypatch.setenv("DTRACK_API_KEY", "k")

    assert dtrack.main(arguments(tmp_path)) == 1
    assert "no processing token" in capsys.readouterr().err


def test_upload_fails_on_a_rejected_upload(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(dtrack, "request", responder([FakeResponse(403, body=b"forbidden")]))
    monkeypatch.setenv("DTRACK_API_KEY", "k")
    assert dtrack.main(arguments(tmp_path)) == 1


def test_upload_fails_without_the_api_key(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("DTRACK_API_KEY", raising=False)
    monkeypatch.setattr(dtrack, "request", responder([FakeResponse(200, {"token": "t"})]))
    assert dtrack.main(arguments(tmp_path)) == 1
    assert "DTRACK_API_KEY is not set" in capsys.readouterr().err


def test_upload_fails_without_a_subject_record(tmp_path: Path, monkeypatch, capsys):
    """Two producers could write sbom.cdx.json; the record says which image."""
    monkeypatch.setenv("DTRACK_API_KEY", "k")
    monkeypatch.setattr(dtrack, "request", responder([FakeResponse(200, {"token": "t"})]))
    sbom, subject = prepare(tmp_path)
    subject.unlink()
    assert dtrack.main([
        "upload", "--url", "https://dtrack.example", "--api-key-variable", "DTRACK_API_KEY",
        "--sbom", str(sbom), "--subject", str(subject), "--project-name", "demo",
        "--project-version", "1", "--output", str(tmp_path / "out.json"),
    ]) == 1
    assert "subject record not found" in capsys.readouterr().err


def test_upload_rejects_a_subject_naming_a_tag(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DTRACK_API_KEY", "k")
    monkeypatch.setattr(dtrack, "request", responder([FakeResponse(200, {"token": "t"})]))
    sbom, subject = prepare(tmp_path, reference="registry.example.com/dev/platform/demo:v1")
    assert dtrack.main([
        "upload", "--url", "https://dtrack.example", "--api-key-variable", "DTRACK_API_KEY",
        "--sbom", str(sbom), "--subject", str(subject), "--project-name", "demo",
        "--project-version", "1", "--output", str(tmp_path / "out.json"),
    ]) == 1


def test_upload_fails_on_an_unreadable_processing_state(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(dtrack, "request", responder([
        FakeResponse(200, {"token": "t"}),
        FakeResponse(200, {"unexpected": "shape"}),
    ]))
    monkeypatch.setenv("DTRACK_API_KEY", "k")
    assert dtrack.main(arguments(tmp_path)) == 1
    assert "no processing state" in capsys.readouterr().err
