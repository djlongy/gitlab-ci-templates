"""Upload an authoritative SBOM to Dependency-Track and wait for its analysis.

    dtrack.py upload --url U --api-key-variable NAME --sbom F --subject F
                     --project-name N --project-version V --output F

Section 10.3 names this failure mode directly: "Dependency-Track upload
acceptance likewise does not establish completed analysis or policy compliance."
The template this replaces checked only for a 2xx and printed "SBOM uploaded
successfully", under `allow_failure: true` (audit 5.5). Here the upload token is
polled until Dependency-Track reports the BOM processed, and a run that is still
processing when the budget expires fails.

The API key is read from the environment by NAME. It is never an input, never
logged and never placed in a URL.

The SBOM's subject record is read alongside it so the evidence says which image
was uploaded, rather than trusting whichever artifact landed last.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpjson import HttpError, request  # noqa: E402


class UploadFailure(RuntimeError):
    """The upload or the analysis did not complete."""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_subject(path: Path) -> dict:
    if not path.is_file():
        raise UploadFailure(
            f"subject record not found: {path}. The SBOM producer must emit one "
            "so this upload can name the image it describes."
        )
    try:
        subject = json.loads(path.read_text())
    except ValueError as error:
        raise UploadFailure(f"{path} is not valid JSON: {error}") from error
    reference = subject.get("image_reference", "")
    if "@sha256:" not in reference:
        raise UploadFailure(f"{path} names no immutable image reference")
    return subject


def upload_bom(args: argparse.Namespace, api_key: str, sbom: bytes) -> str:
    payload = json.dumps(
        {
            "projectName": args.project_name,
            "projectVersion": args.project_version,
            "autoCreate": True,
            "bom": base64.b64encode(sbom).decode("ascii"),
        }
    ).encode()

    response = request(
        "PUT",
        f"{args.url.rstrip('/')}/api/v1/bom",
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
        body=payload,
        timeout=args.request_timeout,
    )
    if not response.ok():
        raise UploadFailure(
            f"Dependency-Track rejected the SBOM with HTTP {response.status}: "
            f"{response.text[:300]}"
        )

    body = response.json()
    token = body.get("token") if isinstance(body, dict) else None
    if not token:
        raise UploadFailure(
            "Dependency-Track accepted the upload but returned no processing "
            "token, so completion cannot be established"
        )
    return token


def wait_for_analysis(args: argparse.Namespace, api_key: str, token: str) -> None:
    """Poll the BOM token until Dependency-Track stops processing it."""
    deadline = time.monotonic() + args.analysis_timeout
    while True:
        response = request(
            "GET",
            f"{args.url.rstrip('/')}/api/v1/bom/token/{token}",
            headers={"X-Api-Key": api_key},
            timeout=args.request_timeout,
        )
        if not response.ok():
            raise UploadFailure(
                f"could not read the processing state of token {token}: "
                f"HTTP {response.status}"
            )
        body = response.json()
        if not isinstance(body, dict) or "processing" not in body:
            raise UploadFailure(
                "Dependency-Track returned no processing state; an unreadable "
                "answer is not a completed analysis"
            )
        if not body["processing"]:
            return
        if time.monotonic() >= deadline:
            raise UploadFailure(
                f"Dependency-Track was still processing token {token} after "
                f"{args.analysis_timeout}s; the upload is not an analysis"
            )
        time.sleep(args.poll_interval)


def upload(args: argparse.Namespace) -> int:
    api_key = os.environ.get(args.api_key_variable, "")
    if not api_key:
        raise UploadFailure(
            f"{args.api_key_variable} is not set. Define it as a masked CI "
            "variable; this component never takes a secret as an input."
        )

    sbom_path = Path(args.sbom)
    if not sbom_path.is_file() or sbom_path.stat().st_size == 0:
        raise UploadFailure(f"no SBOM to upload at {sbom_path}")

    subject = read_subject(Path(args.subject))
    sbom = sbom_path.read_bytes()

    token = upload_bom(args, api_key, sbom)
    analysed = False
    if args.wait_for_analysis:
        wait_for_analysis(args, api_key, token)
        analysed = True

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "analysed" if analysed else "accepted",
                "subject": subject["image_reference"],
                "endpoint": args.url,
                "project_name": args.project_name,
                "project_version": args.project_version,
                "bom_token": token,
                "sbom_sha256": subject.get("sbom_sha256", ""),
                "analysis_completed": analysed,
                "created_at": now(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    state = "analysed" if analysed else "accepted (analysis not awaited)"
    print(f"Dependency-Track {state}: {args.project_name} {args.project_version}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    upload_parser = sub.add_parser("upload", help="upload an SBOM and await its analysis")
    upload_parser.add_argument("--url", required=True)
    upload_parser.add_argument("--api-key-variable", required=True)
    upload_parser.add_argument("--sbom", required=True)
    upload_parser.add_argument("--subject", required=True)
    upload_parser.add_argument("--project-name", required=True)
    upload_parser.add_argument("--project-version", required=True)
    upload_parser.add_argument("--output", required=True)
    upload_parser.add_argument("--wait-for-analysis", default="true")
    upload_parser.add_argument("--analysis-timeout", type=int, default=600)
    upload_parser.add_argument("--poll-interval", type=int, default=10)
    upload_parser.add_argument("--request-timeout", type=int, default=60)
    upload_parser.set_defaults(handler=upload)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if isinstance(getattr(args, "wait_for_analysis", None), str):
        args.wait_for_analysis = args.wait_for_analysis.lower() == "true"
    try:
        return args.handler(args)
    except (UploadFailure, HttpError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
