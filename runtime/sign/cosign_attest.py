"""Sign a built image with cosign and attach the evidence the release requires.

    cosign_attest.py sign --identity image.json --sbom sbom.cdx.json
                          --subject subject.json --vulnerability-report F
                          --key hashivault://cosign --vault-address URL
                          --vault-auth-path jwt --vault-role gitlab-ci
                          --output attestation-result.json

Every failure mode in audit 5.6 is a hard failure here:

  * no Vault credential                -> fail. The template this replaces printed
    "WARN: No VAULT_TOKEN - skipping cosign signing" and exited 0, so a broken
    Vault role produced a green job and an unsigned image (section 10.1:
    "Signing enabled but credentials unavailable -> Job fails").
  * JWT login rejected                 -> fail, rather than falling through.
  * SBOM or vulnerability report absent -> fail (section 10.1: "Required SBOM or
    attestation evidence absent").
  * cosign binary checksum mismatch    -> fail before it is made executable.

The subject is the digest reference from the builder's image.json, and the SBOM
is only attested once its own subject record agrees with that reference.

No SLSA provenance is produced. The predecessor assembled a provenance document
from CI variables in a heredoc and attached it as `--type slsaprovenance`;
section 11.1 forbids labelling hand-written source metadata as a verified SLSA
level, and this repository has no builder-produced provenance to attach instead.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpjson import HttpError, request  # noqa: E402

COSIGN_VERSION = "v2.4.1"
# Where the pinned binary is fetched from, one directory above the version. A
# site with no path to the upstream releases sets CI_TPL_COSIGN_RELEASE_URL to
# its own mirror. The checksum in cosign_checksums.txt is verified either way,
# so pointing this elsewhere changes where the bytes come from and not which
# bytes are accepted.
COSIGN_RELEASE_URL = os.environ.get(
    "CI_TPL_COSIGN_RELEASE_URL", "https://github.com/sigstore/cosign/releases/download"
).rstrip("/")
ARCHITECTURES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


class SigningFailure(RuntimeError):
    """Signing did not complete, or its evidence cannot be shown."""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_identity(path: Path) -> dict:
    if not path.is_file():
        raise SigningFailure(f"image identity record not found: {path}")
    try:
        identity = json.loads(path.read_text())
    except ValueError as error:
        raise SigningFailure(f"{path} is not valid JSON: {error}") from error
    digest = identity.get("digest", "")
    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        raise SigningFailure(f"{path} carries no sha256 digest; refusing to sign a tag")
    if identity.get("reference") != f"{identity.get('repository', '')}@{digest}":
        raise SigningFailure(f"{path} is inconsistent: reference is not repository@digest")
    return identity


def expected_checksum(checksums: Path, asset: str) -> str:
    for line in checksums.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        digest, _, name = line.partition("  ")
        if name.strip() == asset:
            return digest.strip()
    raise SigningFailure(f"{checksums} pins no checksum for {asset}")


def install_cosign(destination: Path, checksums: Path, machine: str) -> str:
    """Download cosign and refuse to run it unless it matches the pinned hash."""
    architecture = ARCHITECTURES.get(machine)
    if architecture is None:
        raise SigningFailure(
            f"no cosign checksum is pinned for machine {machine!r}; add one from "
            "the release's cosign_checksums.txt rather than skipping verification"
        )

    asset = f"cosign-linux-{architecture}"
    wanted = expected_checksum(checksums, asset)

    response = request("GET", f"{COSIGN_RELEASE_URL}/{COSIGN_VERSION}/{asset}", timeout=180)
    if not response.ok():
        raise SigningFailure(f"downloading {asset} returned HTTP {response.status}")

    actual = hashlib.sha256(response.body).hexdigest()
    if actual != wanted:
        raise SigningFailure(
            f"{asset} does not match the pinned checksum. "
            f"expected {wanted}, got {actual}"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.body)
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"cosign {COSIGN_VERSION} ({asset}) verified against the pinned checksum")
    return actual


def vault_token(args: argparse.Namespace) -> str:
    """Exchange the job's ID token for a Vault token. No token, no signing."""
    jwt = os.environ.get(args.id_token_variable, "")
    if not jwt:
        raise SigningFailure(
            f"{args.id_token_variable} is empty. The component declares it through "
            "id_tokens; without it there is no identity to sign with."
        )

    response = request(
        "POST",
        f"{args.vault_address.rstrip('/')}/v1/auth/{args.vault_auth_path}/login",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"jwt": jwt, "role": args.vault_role}).encode(),
        timeout=args.request_timeout,
    )
    if not response.ok():
        raise SigningFailure(
            f"Vault rejected the JWT login for role {args.vault_role!r}: "
            f"HTTP {response.status}: {response.text[:300]}"
        )

    body = response.json()
    token = (body.get("auth") or {}).get("client_token") if isinstance(body, dict) else None
    if not token:
        raise SigningFailure("Vault returned no client token for the JWT login")
    print(f"Vault login succeeded for role {args.vault_role} (token length {len(token)})")
    return token


def registry_auth(registry: str, username_variable: str, password_variable: str) -> bool:
    """Write a docker config so cosign can read manifests from the registry."""
    username = os.environ.get(username_variable, "")
    password = os.environ.get(password_variable, "")
    if not username or not password:
        return False
    credential = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    config = Path.home() / ".docker" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"auths": {registry: {"auth": credential}}}))
    config.chmod(0o600)
    return True


def run_cosign(binary: Path, arguments: list[str], environment: dict[str, str]) -> None:
    completed = subprocess.run(
        [str(binary), *arguments],
        env={**os.environ, **environment},
        check=False,
    )
    if completed.returncode != 0:
        raise SigningFailure(
            f"cosign {arguments[0]} failed with exit code {completed.returncode}"
        )


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_evidence(path: Path, what: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise SigningFailure(
            f"{what} is missing or empty at {path}. Attesting it was requested, so "
            "the release has no evidence and this job fails."
        )


def check_sbom_subject(subject_path: Path, reference: str) -> None:
    require_evidence(subject_path, "the SBOM subject record")
    try:
        subject = json.loads(subject_path.read_text())
    except ValueError as error:
        raise SigningFailure(f"{subject_path} is not valid JSON: {error}") from error
    if subject.get("image_reference") != reference:
        raise SigningFailure(
            f"the SBOM describes {subject.get('image_reference')!r}, not the image "
            f"being signed ({reference})"
        )


def sign(args: argparse.Namespace) -> int:
    identity = read_identity(Path(args.identity))
    reference = identity["reference"]
    registry = identity["repository"].split("/", 1)[0]

    binary = Path(args.install_dir) / "cosign"
    binary_sha256 = install_cosign(binary, Path(args.checksums), os.uname().machine)

    token = vault_token(args)
    environment = {"VAULT_ADDR": args.vault_address, "VAULT_TOKEN": token}

    authenticated = registry_auth(registry, args.username_variable, args.password_variable)
    if not authenticated:
        print(
            f"no {args.username_variable}/{args.password_variable} in the "
            "environment; relying on an anonymous registry read"
        )

    tlog = f"--tlog-upload={'true' if args.tlog_upload else 'false'}"

    # An attestation with no producer is not an attestation the release lost, it
    # is one the consumer never asked for: the component's sbom-job and scan-job
    # are empty. Saying so here is the only record a reader of the job log gets,
    # because attestation-result.json lists what WAS attached and cannot list
    # what was never wired up.
    attest_sbom = args.attest_sbom
    if attest_sbom and not args.sbom_job:
        print("no sbom-job is set: skipping the CycloneDX attestation")
        attest_sbom = False
    attest_vulnerabilities = args.attest_vulnerabilities
    if attest_vulnerabilities and not args.scan_job:
        print("no scan-job is set: skipping the vulnerability attestation")
        attest_vulnerabilities = False

    # An unreadable key is a credential failure, caught before anything is
    # signed rather than halfway through.
    run_cosign(binary, ["public-key", "--key", args.key], environment)

    run_cosign(
        binary,
        [
            "sign", "--key", args.key, "--yes", tlog,
            "-a", f"ci.commit.sha={identity.get('source_commit', '')}",
            "-a", f"ci.pipeline.id={identity.get('pipeline_id', '')}",
            "-a", f"ci.project={os.environ.get('CI_PROJECT_PATH', '')}",
            reference,
        ],
        environment,
    )

    attached = [{"kind": "signature", "predicate": None}]

    if attest_sbom:
        sbom = Path(args.sbom)
        require_evidence(sbom, "the authoritative SBOM")
        check_sbom_subject(Path(args.subject), reference)
        run_cosign(
            binary,
            ["attest", "--key", args.key, "--type", "cyclonedx",
             "--predicate", str(sbom), "--yes", tlog, reference],
            environment,
        )
        attached.append({"kind": "cyclonedx", "predicate": str(sbom),
                         "sha256": file_digest(sbom)})

    if attest_vulnerabilities:
        report = Path(args.vulnerability_report)
        require_evidence(report, "the vulnerability report")
        run_cosign(
            binary,
            ["attest", "--key", args.key, "--type", "vuln",
             "--predicate", str(report), "--yes", tlog, reference],
            environment,
        )
        attached.append({"kind": "vuln", "predicate": str(report),
                         "sha256": file_digest(report)})

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "signed",
                "subject": reference,
                "digest": identity["digest"],
                "signer": {
                    "tool": "cosign",
                    "version": COSIGN_VERSION,
                    "binary_sha256": binary_sha256,
                    "key": args.key,
                    "vault_address": args.vault_address,
                    "vault_auth_path": args.vault_auth_path,
                    "vault_role": args.vault_role,
                },
                "transparency_log_upload": bool(args.tlog_upload),
                "attached": attached,
                "created_at": now(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    print(f"signed {reference} with {len(attached) - 1} attestation(s)")
    return 0


def _boolean(value: str) -> bool:
    return str(value).lower() == "true"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sign_parser = sub.add_parser("sign", help="sign an image and attach its evidence")
    sign_parser.add_argument("--identity", required=True)
    sign_parser.add_argument("--sbom", required=True)
    sign_parser.add_argument("--subject", required=True)
    sign_parser.add_argument("--vulnerability-report", required=True)
    sign_parser.add_argument("--output", required=True)
    sign_parser.add_argument("--key", required=True)
    sign_parser.add_argument("--vault-address", required=True)
    sign_parser.add_argument("--vault-auth-path", required=True)
    sign_parser.add_argument("--vault-role", required=True)
    sign_parser.add_argument("--id-token-variable", default="VAULT_ID_TOKEN")
    sign_parser.add_argument("--username-variable", default="HARBOR_USER")
    sign_parser.add_argument("--password-variable", default="HARBOR_PASSWORD")
    sign_parser.add_argument("--checksums", required=True)
    sign_parser.add_argument("--install-dir", default="/usr/local/bin")
    # The producer job names, not paths: empty means the consumer wired no
    # producer, which is what separates "not asked for" from "asked for and
    # missing" -- the second still fails the job.
    sign_parser.add_argument("--sbom-job", default="")
    sign_parser.add_argument("--scan-job", default="")
    sign_parser.add_argument("--attest-sbom", default="true")
    sign_parser.add_argument("--attest-vulnerabilities", default="true")
    sign_parser.add_argument("--tlog-upload", default="false")
    sign_parser.add_argument("--request-timeout", type=int, default=60)
    sign_parser.set_defaults(handler=sign)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for flag in ("attest_sbom", "attest_vulnerabilities", "tlog_upload"):
        if isinstance(getattr(args, flag, None), str):
            setattr(args, flag, _boolean(getattr(args, flag)))
    try:
        return args.handler(args)
    except (SigningFailure, HttpError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
