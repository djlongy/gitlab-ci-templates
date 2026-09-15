"""Vigil supply-chain sync and release-readiness gate.

Two subcommands, one per component:

    vigil.py sync   --url U --identity image.json --output vigil-sync-result.json
    vigil.py verify --url U --identity image.json --output gate-result.json

Both refuse to pass when the evidence system cannot answer. The template this
replaces did the opposite: `security/vigil-notify.yml` swallowed an unreachable
Vigil with `|| { echo WARN; exit 0; }` and carried `allow_failure: true`, and
`security/vigil-check.yml` printed "skipping gate" and exited 0 when the image
never appeared. Section 17 is explicit that a required gate must not be "hidden
by successful error paths", so an outage here fails the pipeline.

The subject always comes from the builder's image.json, never from a dotenv
value or a tag. `verify` looks the image up by its digest, and a record that
does not carry a readiness verdict is a failure rather than a default pass.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpjson import HttpError, get_json, request  # noqa: E402

READINESS_ORDER = ("red", "amber", "yellow", "green")


class GateFailure(RuntimeError):
    """The gate evaluated and the answer was no."""


def read_identity(path: Path) -> dict:
    """Load and check a builder's image identity record (section 9.1)."""
    if not path.is_file():
        raise GateFailure(f"image identity record not found: {path}")
    try:
        identity = json.loads(path.read_text())
    except ValueError as error:
        raise GateFailure(f"{path} is not valid JSON: {error}") from error

    reference = identity.get("reference", "")
    digest = identity.get("digest", "")
    repository = identity.get("repository", "")

    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        raise GateFailure(f"{path} carries no sha256 digest; refusing to query by tag")
    if reference != f"{repository}@{digest}":
        raise GateFailure(f"{path} is inconsistent: reference is not repository@digest")

    return identity


def write_result(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sync(args: argparse.Namespace) -> int:
    identity = read_identity(Path(args.identity))
    payload = json.dumps(
        {
            "image": identity["repository"],
            "digest": identity["digest"],
            "reference": identity["reference"],
            "source_commit": identity.get("source_commit", ""),
            "source": "gitlab-ci",
            "pipeline_id": str(identity.get("pipeline_id", "")),
        }
    ).encode()

    response = request(
        "POST",
        f"{args.url.rstrip('/')}/api/v1/webhook/image-built",
        headers={"Content-Type": "application/json"},
        body=payload,
        timeout=args.timeout,
    )

    if not response.ok():
        raise GateFailure(
            f"Vigil rejected the sync with HTTP {response.status}: {response.text[:300]}"
        )

    write_result(
        Path(args.output),
        {
            "schema_version": 1,
            "status": "synced",
            "subject": identity["reference"],
            "endpoint": args.url,
            "http_status": response.status,
            "created_at": now(),
        },
    )
    print(f"Vigil sync accepted for {identity['reference']}")
    return 0


def lookup_readiness(url: str, digest: str, timeout: int) -> dict:
    return get_json(
        f"{url.rstrip('/')}/api/v1/images/lookup/readiness?digest={digest}",
        timeout=timeout,
    )


def poll_readiness(args: argparse.Namespace, digest: str) -> dict:
    """Wait for Vigil to hold a record for this digest.

    A transport failure is retried, because Harbor's scan and Vigil's sync take
    time. Running out of time is a failure: "not found" is not "no findings".
    """
    deadline = time.monotonic() + args.max_wait
    last_error = "no attempt completed"
    while True:
        try:
            return lookup_readiness(args.url, digest, args.request_timeout)
        except HttpError as error:
            last_error = str(error)
        if time.monotonic() >= deadline:
            raise GateFailure(
                f"Vigil returned no readiness record for {digest} within "
                f"{args.max_wait}s. Last error: {last_error}"
            )
        print(f"waiting for Vigil to record {digest} ...")
        time.sleep(args.poll_interval)


def verify(args: argparse.Namespace) -> int:
    identity = read_identity(Path(args.identity))
    digest = identity["digest"]

    record = poll_readiness(args, digest)
    if not isinstance(record, dict):
        raise GateFailure("Vigil returned a readiness record that is not an object")

    readiness = record.get("readiness")
    if not isinstance(readiness, str) or readiness.lower() not in READINESS_ORDER:
        raise GateFailure(
            "Vigil returned no usable readiness verdict "
            f"(got {readiness!r}); an unreadable verdict is not a pass"
        )
    readiness = readiness.lower()

    recorded_digest = record.get("digest")
    if isinstance(recorded_digest, str) and recorded_digest and recorded_digest != digest:
        raise GateFailure(
            f"Vigil answered about {recorded_digest}, not the image under test ({digest})"
        )

    required = args.require.lower()
    passed = READINESS_ORDER.index(readiness) >= READINESS_ORDER.index(required)

    write_result(
        Path(args.output),
        {
            "schema_version": 1,
            "evaluator": "vigil",
            "endpoint": args.url,
            "subject": identity["reference"],
            "readiness": readiness,
            "required_readiness": required,
            "deployable": record.get("deployable"),
            "reason": record.get("reason"),
            "signed": record.get("signed"),
            "scan_complete": record.get("scan_complete"),
            "result": "pass" if passed else "fail",
            "created_at": now(),
        },
    )

    if not passed:
        raise GateFailure(
            f"readiness is '{readiness}', policy requires at least '{required}'. "
            f"Reason: {record.get('reason') or 'not stated by Vigil'}"
        )

    print(f"readiness '{readiness}' meets the '{required}' policy for {identity['reference']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser("sync", help="ask Vigil to ingest a freshly built image")
    sync_parser.add_argument("--url", required=True)
    sync_parser.add_argument("--identity", required=True)
    sync_parser.add_argument("--output", required=True)
    sync_parser.add_argument("--timeout", type=int, default=120)
    sync_parser.set_defaults(handler=sync)

    verify_parser = sub.add_parser("verify", help="evaluate Vigil's release readiness verdict")
    verify_parser.add_argument("--url", required=True)
    verify_parser.add_argument("--identity", required=True)
    verify_parser.add_argument("--output", required=True)
    verify_parser.add_argument("--require", default="green", choices=["green", "amber", "yellow"])
    verify_parser.add_argument("--max-wait", type=int, default=300)
    verify_parser.add_argument("--poll-interval", type=int, default=15)
    verify_parser.add_argument("--request-timeout", type=int, default=30)
    verify_parser.set_defaults(handler=verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (GateFailure, HttpError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
