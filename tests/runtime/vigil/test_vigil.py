"""Behaviour tests for runtime/vigil/vigil.py.

The two templates these replace both turned an outage into a pass:

  vigil-notify.yml  `|| { echo "WARN: Vigil unreachable ..."; exit 0; }`, under
                    `allow_failure: true` (audit 6.1).
  vigil-check.yml   `if [ -z "${RESPONSE}" ]; then echo "... skipping gate";
                    exit 0; fi`, and readiness parsed with `grep -o` so a field
                    the server never sent compared unequal to "red" and passed
                    (audit 6.2).

Every test below fails if either behaviour comes back.
"""

from __future__ import annotations

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
vigil = helpers.load_runtime_module("vigil/vigil.py")
# vigil imported HttpError into its own namespace; raising a second copy loaded
# here would not be caught by its except clause.
HttpError = vigil.HttpError

DIGEST = "sha256:" + "c" * 64
REPOSITORY = "registry.example.com/dev/platform/demo"
REFERENCE = f"{REPOSITORY}@{DIGEST}"


def write_identity(tmp_path: Path, **overrides) -> Path:
    record = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "digest": DIGEST,
        "reference": REFERENCE,
        "source_commit": "0" * 40,
        "pipeline_id": "7",
    }
    record.update(overrides)
    path = tmp_path / "image.json"
    path.write_text(json.dumps(record))
    return path


# -------------------------------------------------------------------- sync


def test_sync_posts_the_digest_and_records_the_outcome(tmp_path: Path, monkeypatch):
    seen = {}

    def fake_request(method, url, *, headers=None, body=None, timeout=30):
        seen.update(method=method, url=url, body=json.loads(body))
        return FakeResponse(202, {"accepted": True})

    monkeypatch.setattr(vigil, "request", fake_request)
    identity = write_identity(tmp_path)
    output = tmp_path / "vigil-sync-result.json"

    assert vigil.main(["sync", "--url", "https://vigil.example", "--identity",
                       str(identity), "--output", str(output)]) == 0

    assert seen["method"] == "POST"
    assert seen["body"]["digest"] == DIGEST
    assert seen["body"]["reference"] == REFERENCE
    record = json.loads(output.read_text())
    assert record["status"] == "synced"
    assert record["subject"] == REFERENCE


def test_sync_fails_when_vigil_is_unreachable(tmp_path: Path, monkeypatch, capsys):
    def unreachable(*args, **kwargs):
        raise HttpError("connection refused")

    monkeypatch.setattr(vigil, "request", unreachable)
    identity = write_identity(tmp_path)

    assert vigil.main(["sync", "--url", "https://vigil.example", "--identity",
                       str(identity), "--output", str(tmp_path / "out.json")]) == 1
    assert "connection refused" in capsys.readouterr().err


def test_sync_fails_on_a_rejected_webhook(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        vigil, "request", lambda *a, **k: FakeResponse(500, body=b"boom")
    )
    identity = write_identity(tmp_path)
    assert vigil.main(["sync", "--url", "https://vigil.example", "--identity",
                       str(identity), "--output", str(tmp_path / "out.json")]) == 1


def test_sync_refuses_an_identity_without_a_digest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(vigil, "request", lambda *a, **k: FakeResponse(200))
    identity = write_identity(tmp_path, digest="v1.2.3", reference=f"{REPOSITORY}:v1.2.3")
    assert vigil.main(["sync", "--url", "https://vigil.example", "--identity",
                       str(identity), "--output", str(tmp_path / "out.json")]) == 1


# ------------------------------------------------------------------ verify


def verify_with(tmp_path: Path, monkeypatch, record, *extra: str) -> tuple[int, Path]:
    monkeypatch.setattr(vigil, "get_json", lambda *a, **k: record)
    identity = write_identity(tmp_path)
    output = tmp_path / "gate-result.json"
    code = vigil.main(
        ["verify", "--url", "https://vigil.example", "--identity", str(identity),
         "--output", str(output), "--max-wait", "0", "--poll-interval", "0", *extra]
    )
    return code, output


def test_verify_passes_a_green_image(tmp_path: Path, monkeypatch):
    code, output = verify_with(
        tmp_path, monkeypatch,
        {"readiness": "green", "deployable": True, "digest": DIGEST},
    )
    assert code == 0
    record = json.loads(output.read_text())
    assert record["result"] == "pass"
    assert record["readiness"] == "green"
    assert record["subject"] == REFERENCE


def test_verify_fails_a_red_image(tmp_path: Path, monkeypatch):
    code, output = verify_with(
        tmp_path, monkeypatch,
        {"readiness": "red", "reason": "unsigned", "digest": DIGEST},
    )
    assert code == 1
    assert json.loads(output.read_text())["result"] == "fail"


def test_verify_fails_an_amber_image_under_the_default_policy(tmp_path: Path, monkeypatch):
    code, _ = verify_with(tmp_path, monkeypatch, {"readiness": "amber", "digest": DIGEST})
    assert code == 1


def test_verify_accepts_amber_when_the_policy_allows_it(tmp_path: Path, monkeypatch):
    code, _ = verify_with(
        tmp_path, monkeypatch, {"readiness": "amber", "digest": DIGEST},
        "--require", "amber",
    )
    assert code == 0


def test_verify_fails_when_the_verdict_field_is_missing(tmp_path: Path, monkeypatch, capsys):
    """grep -o returned "" here and the old gate passed (audit 6.2)."""
    code, _ = verify_with(tmp_path, monkeypatch, {"deployable": True})
    assert code == 1
    assert "no usable readiness verdict" in capsys.readouterr().err


def test_verify_fails_when_vigil_answers_about_another_image(tmp_path: Path, monkeypatch):
    code, _ = verify_with(
        tmp_path, monkeypatch,
        {"readiness": "green", "digest": "sha256:" + "d" * 64},
    )
    assert code == 1


def test_verify_fails_when_the_image_never_appears(tmp_path: Path, monkeypatch, capsys):
    """A Vigil outage must not turn the release gate green (section 17)."""
    def unreachable(*args, **kwargs):
        raise HttpError("connection refused")

    monkeypatch.setattr(vigil, "get_json", unreachable)
    identity = write_identity(tmp_path)
    code = vigil.main(
        ["verify", "--url", "https://vigil.example", "--identity", str(identity),
         "--output", str(tmp_path / "gate-result.json"),
         "--max-wait", "0", "--poll-interval", "0"]
    )
    assert code == 1
    assert "no readiness record" in capsys.readouterr().err


def test_verify_retries_until_the_record_appears(tmp_path: Path, monkeypatch):
    attempts = {"count": 0}

    def flaky(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise HttpError("not yet")
        return {"readiness": "green", "digest": DIGEST}

    monkeypatch.setattr(vigil, "get_json", flaky)
    identity = write_identity(tmp_path)
    code = vigil.main(
        ["verify", "--url", "https://vigil.example", "--identity", str(identity),
         "--output", str(tmp_path / "gate-result.json"),
         "--max-wait", "30", "--poll-interval", "0"]
    )
    assert code == 0
    assert attempts["count"] == 3


@pytest.mark.parametrize("payload", ["not-an-object", ["green"], 3])
def test_verify_rejects_a_readiness_record_that_is_not_an_object(
    tmp_path: Path, monkeypatch, payload
):
    code, _ = verify_with(tmp_path, monkeypatch, payload)
    assert code == 1
