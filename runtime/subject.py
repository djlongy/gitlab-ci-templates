#!/usr/bin/env python3
"""Which image.json a job acts on, printed as a path.

A job that signs, publishes or reports on an image usually reads the build
job's image.json: the consumer named `build-job`, its artifact arrived, and the
record is already on disk. A consumer acting on an image this pipeline did not
build names it with `subject-reference` instead, and then there is nothing to
read. This writes the same record into the job's OWN artefact root, so a
downstream job reads one shape whichever way the subject arrived (section 9.1).

Exactly one of the two. Both named is a contradiction nobody can resolve;
neither leaves the job with no subject at all. Either message names both.

Its own script rather than an argument to each component's runtime: the four
Python components that take a subject already parse their own arguments, and
the shell components reach the same behaviour through
`ci_tpl_resolve_identity_file` in runtime/registry/image-json.sh, whose record
this one matches field for field.

    identity=$(python3 subject.py --build-job "$A" --subject-reference "$B" \
      --build-identity "$C" --artifact-dir "$D") || exit 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# The same form the components declare as the input's `regex:`. Checked again
# here because a runtime that trusts its caller is a runtime that writes a
# record naming a tag.
REFERENCE = re.compile(
    r"^[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)+@sha256:[0-9a-f]{64}$"
)


class SubjectError(ValueError):
    """The subject is missing, doubled, or not a repository@digest."""


def subject_record(reference: str) -> dict:
    """An image.json-shaped record for a reference nobody here built."""
    repository, _, digest = reference.partition("@")
    return {
        "schema_version": 1,
        "repository": repository,
        "digest": digest,
        "reference": reference,
        "source_commit": os.environ.get("CI_COMMIT_SHA", ""),
        "pipeline_id": os.environ.get("CI_PIPELINE_ID", ""),
        "job_id": os.environ.get("CI_JOB_ID", ""),
        # Nothing here called the registry, so the platform list and whether
        # the reference points at a manifest or an index are not facts.
        "platforms": ["unresolved"],
        "subject_kind": "unresolved",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def identity_file(build_job: str, reference: str, build_identity: str, artifact_dir: str) -> Path:
    if build_job and reference:
        raise SubjectError(
            f"build-job and subject-reference are both set; this job takes "
            f"exactly one (build-job {build_job!r}, subject-reference {reference!r})"
        )
    if not build_job and not reference:
        raise SubjectError(
            "neither build-job nor subject-reference is set; this job takes "
            "exactly one: build-job names the job whose image.json states the "
            "subject, subject-reference names the image as "
            "repository@sha256:<digest>"
        )
    if not reference:
        return Path(build_identity)
    if not REFERENCE.match(reference):
        raise SubjectError(
            f"subject-reference {reference!r} is not repository@sha256:<64 hex>"
        )
    destination = Path(artifact_dir) / "image.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(subject_record(reference), indent=2, sort_keys=True) + "\n")
    print(f"subject-reference names {reference}; recorded at {destination}", file=sys.stderr)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-job", default="")
    parser.add_argument("--subject-reference", default="")
    parser.add_argument("--build-identity", required=True)
    parser.add_argument("--artifact-dir", required=True)
    args = parser.parse_args(argv)
    try:
        print(identity_file(
            args.build_job, args.subject_reference, args.build_identity, args.artifact_dir
        ))
    except SubjectError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
