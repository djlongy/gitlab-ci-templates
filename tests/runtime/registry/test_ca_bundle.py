"""Behaviour tests for runtime/registry/ca-bundle.sh.

A registry whose certificate an internal authority issued is invisible to the
job container's trust store, and every client in these components then fails
with a TLS error that names the registry. The bundle arrives as a CI variable
rather than a download, because the site that needs it is the site with no
egress to fetch it from.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "runtime" / "registry" / "ca-bundle.sh"

PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBkTCB+wIJAKZ2n0000000MA0GCSqGSIb3DQEBCwUAMBQxEjAQBgNVBAMMCWxv\n"
    "-----END CERTIFICATE-----\n"
)


def run(script: str, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    full_env.update(env or {})
    return subprocess.run(
        ["/bin/sh", "-c", f". '{HELPER}'\n{script}"],
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
    )


def store(tmp_path: Path) -> Path:
    path = tmp_path / "ca-certificates.crt"
    path.write_text("# existing anchors\n")
    return path


def test_an_unset_variable_is_a_skip_and_not_a_failure(tmp_path):
    """Most consumers push to a registry with a publicly issued certificate."""
    result = run("ci_tpl_trust_ca_bundle", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_a_file_variable_is_read_from_the_path_it_holds(tmp_path):
    """GitLab file variables set the variable to a path, not to the content."""
    pem_file = tmp_path / "bundle.pem"
    pem_file.write_text(PEM)
    target = store(tmp_path)
    result = run(
        "ci_tpl_trust_ca_bundle",
        tmp_path,
        env={
            "CI_TPL_CA_BUNDLE_VARIABLE": "ESTATE_CA",
            "ESTATE_CA": str(pem_file),
            "CI_TPL_CA_STORE": str(target),
            "CI_TPL_WORK": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "trusted 1 extra certificate(s)" in result.stdout
    assert PEM.strip() in target.read_text()
    assert "# existing anchors" in target.read_text()


def test_an_ordinary_variable_carrying_the_pem_works_too(tmp_path):
    target = store(tmp_path)
    result = run(
        "ci_tpl_trust_ca_bundle",
        tmp_path,
        env={
            "CI_TPL_CA_BUNDLE_VARIABLE": "ESTATE_CA",
            "ESTATE_CA": PEM,
            "CI_TPL_CA_STORE": str(target),
            "CI_TPL_WORK": str(tmp_path),
        },
    )
    assert result.returncode == 0, result.stderr
    assert PEM.strip() in target.read_text()


def test_a_variable_holding_no_certificate_is_an_error(tmp_path):
    """Otherwise the job fails later with a TLS error naming the registry, and
    the bundle that was silently empty is the last place anyone looks."""
    target = store(tmp_path)
    result = run(
        "ci_tpl_trust_ca_bundle",
        tmp_path,
        env={
            "CI_TPL_CA_BUNDLE_VARIABLE": "ESTATE_CA",
            "ESTATE_CA": "not a certificate",
            "CI_TPL_CA_STORE": str(target),
            "CI_TPL_WORK": str(tmp_path),
        },
    )
    assert result.returncode != 0
    assert "carries no PEM certificate" in result.stderr
    assert target.read_text() == "# existing anchors\n"


def test_a_named_variable_that_is_empty_is_an_error(tmp_path):
    """Naming a variable and leaving it empty is a misconfiguration, not a
    decision to skip: the skip is spelled by naming nothing at all."""
    result = run(
        "ci_tpl_trust_ca_bundle",
        tmp_path,
        env={"CI_TPL_CA_BUNDLE_VARIABLE": "ESTATE_CA", "ESTATE_CA": ""},
    )
    assert result.returncode != 0
    assert "ESTATE_CA" in result.stderr


def test_an_unwritable_trust_store_falls_back_to_the_environment(tmp_path):
    """A non-root execution image cannot write /etc/ssl/certs. Refusing there
    would make this input work only for images that run as root, which is the
    opposite of what a site with an internal CA needs.

    The store here is a path the append cannot create, rather than a read-only
    file: root ignores the permission bits, and this suite runs as root in CI
    and as a user on a workstation. What is being tested is the branch taken
    when the append fails, whatever made it fail.
    """
    store = tmp_path / "no-such-directory" / "ca-certificates.crt"
    work = tmp_path / "work"
    work.mkdir()
    result = run(
        'ci_tpl_trust_ca_bundle && echo "SSL_CERT_FILE=$SSL_CERT_FILE"',
        tmp_path,
        env={
            "CI_TPL_CA_BUNDLE_VARIABLE": "ESTATE_CA",
            "ESTATE_CA": PEM,
            "CI_TPL_CA_STORE": str(store),
            "CI_TPL_WORK": str(work),
        },
    )
    assert result.returncode == 0, result.stderr
    combined = work / "ci-tpl-ca-combined.pem"
    assert f"SSL_CERT_FILE={combined}" in result.stdout
    assert PEM.strip() in combined.read_text()
    assert not store.exists()


def test_a_writable_store_keeps_the_anchors_already_in_it(tmp_path):
    """The ordinary path: the extra certificate is added to the system store
    and the ones the image shipped with stay."""
    store = tmp_path / "ca-certificates.crt"
    store.write_text("# system anchors\n")
    work = tmp_path / "work"
    work.mkdir()
    result = run(
        "ci_tpl_trust_ca_bundle",
        tmp_path,
        env={
            "CI_TPL_CA_BUNDLE_VARIABLE": "ESTATE_CA",
            "ESTATE_CA": PEM,
            "CI_TPL_CA_STORE": str(store),
            "CI_TPL_WORK": str(work),
        },
    )
    assert result.returncode == 0, result.stderr
    body = store.read_text()
    assert body.startswith("# system anchors") and PEM.strip() in body
