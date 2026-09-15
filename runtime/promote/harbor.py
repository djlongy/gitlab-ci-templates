"""Promote an image between Harbor projects by digest, then prove it arrived.

    harbor.py promote --identity image.json --destination-project prod
                      --username-variable HARBOR_USER
                      --password-variable HARBOR_PASSWORD
                      --output promotion-result.json [--tag v1 ...]

Three defects in `promote/harbor-promote.yml` shaped this (audit 8.1):

  * it promoted `repo:${BUILDKIT_TAG}` — whatever that tag pointed at when the
    manual button was pressed, up to the job's seven-day timeout later. Section
    11.1: "A tag can be a human-readable alias; deployment consumes the
    immutable identity." Here the source is always `repo@sha256:...` read from
    the builder's own identity record.
  * success was `HTTP 201` and nothing else. Section 11.1 requires the
    destination digest to be verified after the copy, so the destination is
    read back and compared.
  * cosign signatures and attestations are separate tagged artifacts that a
    manifest copy does not carry. They are now copied explicitly, and their
    absence fails the promotion unless the caller opted out.

Nothing is rebuilt: Harbor mounts the existing manifest, so the digest is
preserved by construction and then checked rather than assumed.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpjson import HttpError, request  # noqa: E402


class PromotionFailure(RuntimeError):
    """The image was not promoted, or cannot be proven to have been."""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_identity(path: Path) -> dict:
    if not path.is_file():
        raise PromotionFailure(f"image identity record not found: {path}")
    try:
        identity = json.loads(path.read_text())
    except ValueError as error:
        raise PromotionFailure(f"{path} is not valid JSON: {error}") from error

    repository = identity.get("repository", "")
    digest = identity.get("digest", "")
    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        raise PromotionFailure(
            f"{path} carries no sha256 digest. Promotion by tag is what this "
            "component exists to prevent."
        )
    if identity.get("reference") != f"{repository}@{digest}":
        raise PromotionFailure(f"{path} is inconsistent: reference is not repository@digest")
    if repository.count("/") < 2:
        raise PromotionFailure(
            f"repository {repository!r} is not <registry>/<project>/<path>, so the "
            "source project cannot be identified"
        )
    return identity


def split_repository(repository: str) -> tuple[str, str, str]:
    """registry, harbor project, repository path below the project."""
    registry, project, repo_path = repository.split("/", 2)
    return registry, project, repo_path


def encode_repository(repo_path: str) -> str:
    """Harbor expects the repository name double-encoded inside the path."""
    return urllib.parse.quote(urllib.parse.quote(repo_path, safe=""), safe="")


class Harbor:
    def __init__(self, registry: str, username: str, password: str, timeout: int):
        self.base = f"https://{registry}/api/v2.0"
        credential = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        self.headers = {"Authorization": f"Basic {credential}"}
        self.timeout = timeout

    def artifact(self, project: str, repo_path: str, reference: str):
        return request(
            "GET",
            f"{self.base}/projects/{project}/repositories/"
            f"{encode_repository(repo_path)}/artifacts/{urllib.parse.quote(reference, safe='')}",
            headers=self.headers,
            timeout=self.timeout,
        )

    def copy(self, destination_project: str, repo_path: str, source: str):
        url = (
            f"{self.base}/projects/{destination_project}/repositories/"
            f"{encode_repository(repo_path)}/artifacts?from={urllib.parse.quote(source, safe='')}"
        )
        return request("POST", url, headers=self.headers, timeout=self.timeout)

    def tag(self, project: str, repo_path: str, digest: str, name: str):
        url = (
            f"{self.base}/projects/{project}/repositories/"
            f"{encode_repository(repo_path)}/artifacts/{digest}/tags"
        )
        return request(
            "POST",
            url,
            headers={**self.headers, "Content-Type": "application/json"},
            body=json.dumps({"name": name}).encode(),
            timeout=self.timeout,
        )


def referrer_tags(digest: str) -> list[str]:
    """The tags cosign uses for an image's signature and attestations."""
    stem = digest.replace(":", "-")
    return [f"{stem}.sig", f"{stem}.att"]


def copy_artifact(harbor: Harbor, source_project: str, destination_project: str,
                  repo_path: str, reference: str) -> None:
    response = harbor.copy(destination_project, repo_path, f"{source_project}/{repo_path}@{reference}"
                           if reference.startswith("sha256:")
                           else f"{source_project}/{repo_path}:{reference}")
    # 201 is a fresh copy; 409 means the destination already holds this exact
    # artifact, which is the same end state for an idempotent promotion.
    if response.status not in (201, 409):
        raise PromotionFailure(
            f"Harbor refused to copy {reference} into {destination_project}: "
            f"HTTP {response.status}: {response.text[:300]}"
        )


def promote(args: argparse.Namespace) -> int:
    username = os.environ.get(args.username_variable, "")
    password = os.environ.get(args.password_variable, "")
    if not username or not password:
        raise PromotionFailure(
            f"{args.username_variable} and {args.password_variable} must both be "
            "set as masked CI variables; this component never takes a credential "
            "as an input."
        )

    identity = read_identity(Path(args.identity))
    registry, source_project, repo_path = split_repository(identity["repository"])
    digest = identity["digest"]
    destination_project = args.destination_project

    if destination_project == source_project:
        raise PromotionFailure(
            f"source and destination project are both {source_project!r}; that is "
            "not a promotion"
        )

    harbor = Harbor(registry, username, password, args.request_timeout)

    source = harbor.artifact(source_project, repo_path, digest)
    if source.status != 200:
        raise PromotionFailure(
            f"{registry}/{source_project}/{repo_path}@{digest} is not in the "
            f"candidate project: HTTP {source.status}"
        )

    copied_referrers = []
    missing_referrers = []
    for tag in referrer_tags(digest):
        found = harbor.artifact(source_project, repo_path, tag)
        if found.status == 200:
            copy_artifact(harbor, source_project, destination_project, repo_path, tag)
            copied_referrers.append(tag)
        else:
            missing_referrers.append(tag)

    if args.require_signatures and f"{digest.replace(':', '-')}.sig" in missing_referrers:
        raise PromotionFailure(
            f"no cosign signature ({digest.replace(':', '-')}.sig) exists beside "
            f"{digest} in {source_project}; refusing to promote an unsigned image"
        )

    copy_artifact(harbor, source_project, destination_project, repo_path, digest)

    # Verify rather than trust: the copy reporting success is not the same as
    # the destination holding this digest.
    destination = harbor.artifact(destination_project, repo_path, digest)
    if destination.status != 200:
        raise PromotionFailure(
            f"after the copy, {destination_project}/{repo_path}@{digest} could not "
            f"be read back: HTTP {destination.status}"
        )
    destination_digest = destination.json().get("digest")
    if destination_digest != digest:
        raise PromotionFailure(
            f"the destination holds {destination_digest}, not the promoted "
            f"{digest}; the copy did not preserve the identity"
        )

    applied_tags = []
    for tag in args.tag:
        if not tag:
            continue
        response = harbor.tag(destination_project, repo_path, digest, tag)
        if response.status not in (201, 409):
            raise PromotionFailure(
                f"could not apply tag {tag!r} in {destination_project}: "
                f"HTTP {response.status}: {response.text[:300]}"
            )
        applied_tags.append(tag)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "promoted",
                "registry": registry,
                "repository_path": repo_path,
                "source_project": source_project,
                "destination_project": destination_project,
                "digest": digest,
                "source_reference": identity["reference"],
                "destination_reference": f"{registry}/{destination_project}/{repo_path}@{digest}",
                "verified_destination_digest": destination_digest,
                "referrers_copied": copied_referrers,
                "referrers_absent": missing_referrers,
                "tags_applied": applied_tags,
                "source_commit": identity.get("source_commit", ""),
                "created_at": now(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    print(
        f"promoted {digest} into {destination_project}/{repo_path}; "
        f"referrers copied: {copied_referrers or 'none'}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    promote_parser = sub.add_parser("promote", help="copy a digest into the release project")
    promote_parser.add_argument("--identity", required=True)
    promote_parser.add_argument("--destination-project", required=True)
    promote_parser.add_argument("--username-variable", required=True)
    promote_parser.add_argument("--password-variable", required=True)
    promote_parser.add_argument("--output", required=True)
    promote_parser.add_argument("--tag", action="append", default=[])
    promote_parser.add_argument("--require-signatures", default="true")
    promote_parser.add_argument("--request-timeout", type=int, default=60)
    promote_parser.set_defaults(handler=promote)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if isinstance(getattr(args, "require_signatures", None), str):
        args.require_signatures = args.require_signatures.lower() == "true"
    try:
        return args.handler(args)
    except (PromotionFailure, HttpError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
