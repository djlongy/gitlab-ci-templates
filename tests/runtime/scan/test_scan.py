"""Behaviour tests for the shell the scanner components carry.

These run the block the TEMPLATE embeds, not runtime/scan/scan.sh, because the
embedding is where shell changes meaning: a heredoc terminator or a literal
newline gains the YAML block scalar's indentation.

Each group is named for a defect the audit found in the templates these
components replace, so a regression to the old behaviour fails here:

  subject  audit 5.3 - the only SBOM check was a grep count of '"name"', so a
           truncated or component-free SBOM passed, and no subject record was
           written at all.
  match    audit 5.2 - the scan target was chosen by `[ -f sbom.cdx.json ]`, so
           any earlier job that left a file with that name redirected the scan.
  result   audit 5.1/5.2 - no scan-result.json existed, and an empty report read
           as a clean scan.
"""

from __future__ import annotations

import json

import pytest

BLOCKS = ("runtime/registry/image-json.sh", "runtime/scan/scan.sh")
TEMPLATE = "security-sbom-syft"

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
REPOSITORY = "registry.example.com/dev/platform/demo"
REFERENCE = f"{REPOSITORY}@{DIGEST}"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def identity(**overrides) -> str:
    record = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "digest": DIGEST,
        "reference": REFERENCE,
        "source_commit": COMMIT,
        "pipeline_id": "101",
        "job_id": "202",
        "platforms": "linux/amd64",
        "subject_kind": "manifest",
        "created_at": "2026-09-15T00:00:00Z",
    }
    record.update(overrides)
    # image.json is written with `"schema_version": 1` exactly; the validator
    # greps for that literal.
    body = ",\n".join(
        f'  "{key}": {json.dumps(value)}' for key, value in record.items()
    )
    return "{\n" + body + "\n}\n"


def sbom(components: int = 3) -> str:
    return json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "components": [{"name": f"pkg{n}"} for n in range(components)],
        }
    )


# ----------------------------------------------------------------- subject


def test_subject_record_names_the_image_the_sbom_describes(harness):
    harness.file("image.json", identity())
    harness.file("sbom.cdx.json", sbom())
    harness.file("components.txt", "pkg0\npkg1\npkg2\n")
    harness.env["CI_PIPELINE_ID"] = "101"
    harness.env["CI_JOB_ID"] = "202"

    run = harness.run(
        TEMPLATE, *BLOCKS,
        then='ci_tpl_write_sbom_subject sbom.cdx.json components.txt image.json subject.json',
    )
    assert run.returncode == 0, run.output

    record = json.loads((harness.project_dir / "subject.json").read_text())
    assert record["image_reference"] == REFERENCE
    assert record["digest"] == DIGEST
    assert record["source_commit"] == COMMIT
    assert record["component_count"] == 3
    assert record["pipeline_id"] == "101"
    assert len(record["sbom_sha256"]) == 64


def test_a_truncated_sbom_is_a_producer_failure(harness):
    harness.file("image.json", identity())
    harness.file("sbom.cdx.json", '{"bomFormat": "CycloneDX", "components": [{"name": "pkg0"')
    harness.file("components.txt", "pkg0\n")

    run = harness.run(
        TEMPLATE, *BLOCKS,
        then="ci_tpl_write_sbom_subject sbom.cdx.json components.txt image.json subject.json",
    )
    assert run.returncode != 0
    assert "truncated" in run.output


def test_an_sbom_that_catalogued_nothing_is_a_producer_failure(harness):
    harness.file("image.json", identity())
    harness.file("sbom.cdx.json", sbom(components=0))
    harness.file("components.txt", "")

    run = harness.run(
        TEMPLATE, *BLOCKS,
        then="ci_tpl_write_sbom_subject sbom.cdx.json components.txt image.json subject.json",
    )
    assert run.returncode != 0
    assert "catalogued no components" in run.output


def test_a_document_that_is_not_cyclonedx_is_refused(harness):
    harness.file("image.json", identity())
    harness.file("sbom.cdx.json", json.dumps({"spdxVersion": "SPDX-2.3"}))
    harness.file("components.txt", "pkg0\n")

    run = harness.run(
        TEMPLATE, *BLOCKS,
        then="ci_tpl_write_sbom_subject sbom.cdx.json components.txt image.json subject.json",
    )
    assert run.returncode != 0
    assert "not a CycloneDX document" in run.output


def test_a_tag_in_the_identity_record_stops_the_sbom(harness):
    """The old templates took their subject from a tag in a dotenv file."""
    harness.file("image.json", identity(digest="v1.2.3", reference=f"{REPOSITORY}:v1.2.3"))
    harness.file("sbom.cdx.json", sbom())
    harness.file("components.txt", "pkg0\n")

    run = harness.run(
        TEMPLATE, *BLOCKS,
        then="ci_tpl_write_sbom_subject sbom.cdx.json components.txt image.json subject.json",
    )
    assert run.returncode != 0
    assert "not a sha256 digest" in run.output


# ------------------------------------------------------------------- match


def test_a_matching_sbom_prints_the_intended_reference(harness):
    harness.file("image.json", identity())
    harness.file("subject.json", json.dumps({"image_reference": REFERENCE}))

    run = harness.run(
        TEMPLATE, *BLOCKS,
        then="ci_tpl_match_sbom_subject subject.json image.json",
    )
    assert run.returncode == 0, run.output
    assert REFERENCE in run.stdout


def test_an_sbom_for_another_image_never_becomes_the_scan_target(harness):
    harness.file("image.json", identity())
    harness.file("subject.json", json.dumps({"image_reference": f"{REPOSITORY}@{OTHER_DIGEST}"}))

    run = harness.run(
        TEMPLATE, *BLOCKS,
        then="ci_tpl_match_sbom_subject subject.json image.json",
    )
    assert run.returncode != 0
    assert "not the image to scan" in run.output


def test_a_missing_subject_record_fails_rather_than_defaulting(harness):
    harness.file("image.json", identity())
    run = harness.run(
        TEMPLATE, *BLOCKS,
        then="ci_tpl_match_sbom_subject subject.json image.json",
    )
    assert run.returncode != 0
    assert "no subject record" in run.output


# ------------------------------------------------------------------ result


def result_call(**overrides) -> str:
    arguments = {
        "severities": "severities.txt",
        "output": "scan-result.json",
        "scanner": "grype",
        "version": "v0.118.0",
        "subject": REFERENCE,
        "kind": "sbom",
        "threshold": "high",
        "mode": "blocking",
        "unfixed": "false",
        "database": "2026-09-15T06:31:36Z",
    }
    arguments.update(overrides)
    return (
        "ci_tpl_write_scan_result "
        + " ".join(f'"{arguments[key]}"' for key in
                   ("severities", "output", "scanner", "version", "subject",
                    "kind", "threshold", "mode", "unfixed", "database"))
        + " report.json"
    )


def prepare(harness, severities: str, report: str = '{"matches": []}'):
    harness.file("severities.txt", severities)
    harness.file("report.json", report)


def test_a_clean_scan_records_zero_and_passes(harness):
    prepare(harness, "")
    run = harness.run(TEMPLATE, *BLOCKS, then=result_call())
    assert run.returncode == 0, run.output

    record = json.loads((harness.project_dir / "scan-result.json").read_text())
    assert record["status"] == "completed"
    assert record["counts"]["total"] == 0
    assert record["policy"] == {
        "mode": "blocking", "threshold": "high",
        "unfixed_vulnerabilities": "gated", "exceptions": [],
    }
    assert record["subject"] == {"kind": "sbom", "reference": REFERENCE}
    assert record["database"]["updated_at"] == "2026-09-15T06:31:36Z"
    assert len(record["reports"][0]["sha256"]) == 64


def test_every_severity_is_counted_and_the_threshold_gates(harness):
    prepare(harness, "Critical\nHigh\nHigh\nMedium\nLow\nNegligible\nUnknown\n")
    run = harness.run(TEMPLATE, *BLOCKS, then=result_call())
    assert run.returncode != 0
    assert "at or above high" in run.output

    record = json.loads((harness.project_dir / "scan-result.json").read_text())
    assert record["counts"] == {
        "critical": 1, "high": 2, "medium": 1, "low": 1,
        "negligible": 1, "unknown": 1, "total": 7, "at_or_above_threshold": 3,
    }


def test_the_threshold_is_a_minimum_not_an_exact_match(harness):
    prepare(harness, "Medium\nLow\n")
    assert harness.run(TEMPLATE, *BLOCKS, then=result_call(threshold="high")).returncode == 0
    assert harness.run(TEMPLATE, *BLOCKS, then=result_call(threshold="medium")).returncode != 0


def test_advisory_mode_records_findings_without_gating(harness):
    prepare(harness, "Critical\n")
    run = harness.run(TEMPLATE, *BLOCKS, then=result_call(mode="advisory"))
    assert run.returncode == 0, run.output
    record = json.loads((harness.project_dir / "scan-result.json").read_text())
    assert record["policy"]["mode"] == "advisory"
    assert record["counts"]["at_or_above_threshold"] == 1


@pytest.mark.parametrize("mode", ["blocking", "advisory"])
def test_an_empty_report_is_a_scanner_failure_in_both_modes(harness, mode):
    """A client that cannot hydrate its database writes a report like this."""
    prepare(harness, "", report="")
    run = harness.run(
        TEMPLATE, *BLOCKS,
        then='ci_tpl_require_report report.json \'"matches"\'',
    )
    assert run.returncode != 0
    assert "not a clean scan" in run.output


def test_a_report_without_its_marker_is_a_scanner_failure(harness):
    prepare(harness, "", report="<html>gateway timeout</html>")
    run = harness.run(
        TEMPLATE, *BLOCKS,
        then='ci_tpl_require_report report.json \'"matches"\'',
    )
    assert run.returncode != 0
    assert "expected marker" in run.output


def test_a_missing_severity_listing_fails(harness):
    harness.file("report.json", '{"matches": []}')
    run = harness.run(TEMPLATE, *BLOCKS, then=result_call())
    assert run.returncode != 0
    assert "no severity listing" in run.output


def test_an_unknown_policy_mode_is_refused(harness):
    prepare(harness, "")
    run = harness.run(TEMPLATE, *BLOCKS, then=result_call(mode="off"))
    assert run.returncode != 0
    assert "blocking or advisory" in run.output


def test_ignored_unfixed_treatment_is_recorded(harness):
    prepare(harness, "")
    harness.run(TEMPLATE, *BLOCKS, then=result_call(unfixed="true"))
    record = json.loads((harness.project_dir / "scan-result.json").read_text())
    assert record["policy"]["unfixed_vulnerabilities"] == "ignored"


def test_the_scanner_templates_are_written_where_the_job_points_the_tools(harness):
    run = harness.run(
        TEMPLATE, *BLOCKS,
        then='ci_tpl_write_scan_templates runtime && cat runtime/grype-severities.tmpl',
    )
    assert run.returncode == 0, run.output
    assert "{{range .Matches}}{{.Vulnerability.Severity}}" in run.stdout
    for name in ("syft-components.tmpl", "grype-severities.tmpl", "trivy-severities.tmpl"):
        assert (harness.project_dir / "runtime" / name).is_file()


# --------------------------------------------------------------------------
# policy-mode, in every template that carries this gate
#
# The image scanner has its own policy-mode input, so the decision is exercised
# through its embedded copy of the block as well as through the SBOM producer's.
# --------------------------------------------------------------------------

GATE_TEMPLATES = ["security-sbom-syft", "security-image-trivy"]


@pytest.mark.parametrize("template", GATE_TEMPLATES)
def test_blocking_mode_fails_on_a_finding_at_the_threshold(harness, template):
    prepare(harness, "Critical\n")
    run = harness.run(template, *BLOCKS, then=result_call())
    assert run.returncode != 0, run.output
    assert json.loads((harness.project_dir / "scan-result.json").read_text())["counts"][
        "at_or_above_threshold"
    ] == 1


@pytest.mark.parametrize("template", GATE_TEMPLATES)
def test_advisory_mode_passes_on_findings_but_not_on_an_incomplete_scan(harness, template):
    """Advisory covers findings. It does not cover a scan that did not finish."""
    prepare(harness, "Critical\n")
    assert harness.run(template, *BLOCKS, then=result_call(mode="advisory")).returncode == 0

    (harness.project_dir / "severities.txt").unlink()
    run = harness.run(template, *BLOCKS, then=result_call(mode="advisory"))
    assert run.returncode != 0, run.output
    assert "no severity listing" in run.output
