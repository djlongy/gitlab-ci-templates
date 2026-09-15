#!/usr/bin/env python3
"""Validate merged GitLab CI configuration through the CI Lint API.

This is the only check in the repository that resolves `include:` for real. A
local YAML parse says a file is well formed; it cannot say whether the include
resolves, whether the merged config compiles, or which jobs it creates. Section
14.1 of docs/gitlab-ci-agent-standard.md requires the Lint API or equivalent for
merged configuration.

A lint is not an execution. Nothing here runs a pipeline, and a passing lint
must never be reported as one.

Which server: GITLAB_URL, defaulting to https://gitlab.com, and
GITLAB_PROJECT_ID, defaulting to the shared_ci.project_id recorded in
.ci/estate.yml. The endpoint is project-scoped because `include:` resolution
depends on the project context, so point both at the project that holds YOUR
copy of these templates. `--host` and `--project-id` override them per run.

Authentication: GITLAB_TOKEN, a personal or group access token with api scope.
Set it as a masked project or group variable in CI — CI_JOB_TOKEN is not
accepted by this endpoint. Without it every test that lints is skipped, so a run
with no token is green and proves nothing about merged configuration.

Usage:
    GITLAB_URL=https://gitlab.example.com GITLAB_PROJECT_ID=42 \
        python3 tests/pipelines/lint.py path/to/.gitlab-ci.yml
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

def _default_host() -> str:
    """The GitLab to lint against. GITLAB_URL may carry a scheme; strip it."""
    raw = os.environ.get("GITLAB_URL", "https://gitlab.com").strip()
    return raw.split("://", 1)[-1].rstrip("/")


def _default_project_id() -> int:
    """The project that holds these templates.

    The CI Lint API is project-scoped because `include:` resolution depends on
    the project context. GITLAB_PROJECT_ID wins; otherwise the id recorded in
    .ci/estate.yml is used, which is what a fork edits once.
    """
    override = os.environ.get("GITLAB_PROJECT_ID")
    if override:
        return int(override)
    profile = Path(__file__).resolve().parents[2] / ".ci" / "estate.yml"
    try:
        import yaml

        return int(yaml.safe_load(profile.read_text())["shared_ci"]["project_id"])
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        # No id recorded, or no yaml available. 1 is a valid id shape; the
        # request will fail loudly against the wrong project rather than here.
        return 1


DEFAULT_HOST = _default_host()
DEFAULT_PROJECT_ID = _default_project_id()
TIMEOUT_SECONDS = 60


class LintError(RuntimeError):
    """The lint request itself failed, which is not the same as invalid YAML."""


def lint(
    content: str,
    *,
    include_jobs: bool = True,
    dry_run: bool = True,
    ref: str | None = None,
    project_id: int = DEFAULT_PROJECT_ID,
    host: str = DEFAULT_HOST,
    token: str | None = None,
) -> dict:
    """POST configuration to /projects/:id/ci/lint and return the parsed result.

    `dry_run` asks GitLab to simulate pipeline creation rather than only parse,
    so `rules:` are evaluated and the job list reflects what would actually be
    created on the default branch.

    `ref` names the branch or tag that simulation runs against, so a rule on
    `$CI_COMMIT_BRANCH` or `$CI_COMMIT_TAG` can be tested for real. The ref must
    exist on the server: GitLab resolves it to decide whether the context is a
    branch or a tag, and that is what sets `CI_COMMIT_TAG`. Without it the
    default branch is assumed, which is why a tag-only job looks absent
    everywhere until this is passed.
    """
    token = token or os.environ.get("GITLAB_TOKEN")
    if not token:
        raise LintError(
            "GITLAB_TOKEN is not set. It needs api scope on the project named "
            "by GITLAB_URL and GITLAB_PROJECT_ID; see README.md."
        )

    url = f"https://{host}/api/v4/projects/{project_id}/ci/lint"
    body = {"content": content, "include_jobs": include_jobs, "dry_run": dry_run}
    if ref is not None:
        body["ref"] = ref
    payload = json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "PRIVATE-TOKEN": token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        # The body carries GitLab's own explanation; a bare status code sends the
        # reader looking in the wrong place.
        body = error.read().decode(errors="replace")[:500]
        raise LintError(f"{url} returned HTTP {error.code}: {body}") from error
    except urllib.error.URLError as error:
        raise LintError(f"{url} unreachable: {error.reason}") from error


def job_names(result: dict) -> list[str]:
    """Job names from a lint result, or an empty list if jobs were not returned."""
    return [job["name"] for job in result.get("jobs", [])]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="the .gitlab-ci.yml to validate")
    parser.add_argument(
        "--project-id",
        type=int,
        default=DEFAULT_PROJECT_ID,
        help="overrides GITLAB_PROJECT_ID and .ci/estate.yml",
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help="overrides GITLAB_URL (default gitlab.com)"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="parse only, do not simulate pipeline creation",
    )
    parser.add_argument(
        "--ref",
        help="branch or tag to simulate against; must exist on the server",
    )
    args = parser.parse_args(argv)

    try:
        result = lint(
            args.path.read_text(),
            dry_run=not args.no_dry_run,
            ref=args.ref,
            project_id=args.project_id,
            host=args.host,
        )
    except LintError as error:
        print(error, file=sys.stderr)
        return 2

    if not result.get("valid"):
        print(f"INVALID {args.path}", file=sys.stderr)
        for message in result.get("errors", []):
            print(f"  error:   {message}", file=sys.stderr)
        for message in result.get("warnings", []):
            print(f"  warning: {message}", file=sys.stderr)
        return 1

    names = job_names(result)
    print(f"valid {args.path} — {len(names)} job(s): {', '.join(names) or 'none'}")
    for message in result.get("warnings", []):
        print(f"  warning: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
