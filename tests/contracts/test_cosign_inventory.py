"""The committed signing public key verifies the runner-image catalogue.

A reviewer who takes the public key from the sign job's log is verifying a
pipeline with a key that pipeline presented. A key committed here is the
independent copy: reviewed, with history, and changed only by merge request.

Both checks skip while no key is committed, which is the state this repository
ships in. Export your signer's public half to cosign.pub beside the component
and they start running. templates/container-sign-attest-cosign/README.md is
the guide.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
KEY = REPO_ROOT / "templates" / "container-sign-attest-cosign" / "cosign.pub"
CATALOGUE = REPO_ROOT / "images" / "runner-images.yml"


def test_the_committed_key_is_a_pem_public_key():
    if not KEY.exists():
        pytest.skip("no public key is committed beside the component")
    text = KEY.read_text()
    assert text.startswith("-----BEGIN PUBLIC KEY-----\n")
    assert text.rstrip().endswith("-----END PUBLIC KEY-----")
    assert "PRIVATE" not in text


def test_the_committed_key_verifies_the_inventory():
    """Live check. Skips with no key, no cosign, or a registry that refuses us."""
    if not KEY.exists():
        pytest.skip("no public key is committed beside the component")
    if shutil.which("cosign") is None:
        pytest.skip("cosign not on PATH")
    images = yaml.safe_load(CATALOGUE.read_text())["images"]
    if not images:
        pytest.skip("the image catalogue is empty")
    for image in images:
        ref = f"{image['repository']}@{image['digest']}"
        completed = subprocess.run(
            [
                "cosign",
                "verify",
                "--key",
                str(KEY),
                "--insecure-ignore-tlog",
                ref,
            ],
            capture_output=True,
            text=True,
        )
        err = completed.stderr
        if completed.returncode != 0 and (
            "401" in err or "UNAUTHORIZED" in err or "denied" in err.lower()
        ):
            pytest.skip(f"registry auth required for {ref}")
        assert completed.returncode == 0, err
        assert "signatures were verified" in err, err
