"""A listing the job cannot read is not a listing with nothing in it.

TE's rc.2 review left this open: `count=$(grep -c ...) || count=0` gives the
same answer for "matched nothing" (grep exit 1) and "could not look" (exit 2 or
more). The second is a scanner or runner failure, and turning it into zero
produces a scan-result.json that says the image is clean, with every count at
zero and `at_or_above_threshold: 0`, so the gate passes. `|| true` has the same
defect, which is why the fix is not that either.

Two ways in, because neither alone covers both environments:

  stub   grep is replaced on PATH and told to exit 2, so the wrapper's decision
         is under test whatever the tool and whoever the user is. This is the
         case that runs in CI, where the job is root and a mode has no effect.
  real   a mode-000 file and the real grep, skipped for root. This is the
         failure as it actually arrives.
"""

from __future__ import annotations

import json
import os

import pytest

BLOCKS = ("runtime/registry/image-json.sh", "runtime/scan/scan.sh")
TEMPLATE = "security-sbom-syft"

RESULT_CALL = (
    'ci_tpl_write_scan_result "severities.txt" "scan-result.json" grype v0.118.0 '
    '"registry.example.com/dev/demo@sha256:' + "a" * 64 + '" sbom high blocking '
    'false "2026-09-15T06:31:36Z" report.json'
)

skip_as_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="a file mode does not stop root from reading it"
)


def prepare(harness, severities: str = "High\n"):
    harness.file("severities.txt", severities)
    harness.file("report.json", '{"matches": []}')


def test_a_counting_grep_that_errors_fails_the_scan_stubbed(harness):
    prepare(harness)
    # Only the counting greps: the helper always calls `grep -c`, so keying the
    # stub on the first argument leaves `grep -q` and the rest alone.
    harness.stub("grep", subcommand_exits={"-c": 2})
    run = harness.run(TEMPLATE, *BLOCKS, then=RESULT_CALL)

    assert run.returncode != 0, run.output
    assert "grep exited 2" in run.output
    assert not (harness.project_dir / "scan-result.json").exists(), (
        "a scan whose counts could not be read must write no result at all"
    )


@skip_as_root
def test_an_unreadable_severity_listing_fails_the_scan(harness):
    prepare(harness)
    (harness.project_dir / "severities.txt").chmod(0o000)
    run = harness.run(TEMPLATE, *BLOCKS, then=RESULT_CALL)

    assert run.returncode != 0, run.output
    assert "is not a count of zero" in run.output
    assert not (harness.project_dir / "scan-result.json").exists()


@skip_as_root
def test_the_old_behaviour_would_have_reported_a_clean_image(harness):
    """Names what the defect produced, so the test is not just an exit code.

    The same run with the findings readable writes a result whose counts are
    all zero. That document is indistinguishable from the one the old code
    wrote for an unreadable listing, which is the whole problem.
    """
    prepare(harness, severities="")
    run = harness.run(TEMPLATE, *BLOCKS, then=RESULT_CALL)
    assert run.returncode == 0, run.output
    record = json.loads((harness.project_dir / "scan-result.json").read_text())
    assert record["counts"]["total"] == 0
    assert record["counts"]["at_or_above_threshold"] == 0


def test_an_unreadable_component_listing_fails_the_sbom_subject(harness):
    """The same count in the other function: `grep -c .` over syft's listing."""
    harness.file("components.txt", "pkg:one\npkg:two\n")
    harness.file(
        "sbom.cdx.json",
        json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.5", "components": []}),
    )
    harness.file(
        "image.json",
        json.dumps(
            {
                "schema_version": 1,
                "repository": "registry.example.com/dev/demo",
                "digest": "sha256:" + "a" * 64,
                "reference": "registry.example.com/dev/demo@sha256:" + "a" * 64,
                "source_commit": "0" * 40,
            },
            indent=2,
        )
        + "\n",
    )
    harness.stub("grep", subcommand_exits={"-c": 2})
    run = harness.run(
        TEMPLATE,
        *BLOCKS,
        then='ci_tpl_write_sbom_subject sbom.cdx.json components.txt image.json subject.json',
    )
    assert run.returncode != 0, run.output
    assert "grep exited 2" in run.output
    assert not (harness.project_dir / "subject.json").exists()
