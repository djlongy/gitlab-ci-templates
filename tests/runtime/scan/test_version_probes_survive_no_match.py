"""A version or database probe that finds nothing must not kill the job.

Same fault as the gitleaks one in test_gitleaks_job_shell.py, in four more
places. Each of these lines captured a value out of a tool's output with
`producer | grep X | sed 's/X//'`. The runner runs every script block under
`set -eo pipefail`, so when X is absent grep exits 1, that becomes the
pipeline's status, the assignment fails and `set -e` ends the job — silently,
before the `${VAR:-unknown}` fallback the author wrote two lines below and before
the explicit error message that fallback feeds. sed does both jobs in one process
and exits 0 whether or not it matched, so the intended handling finally runs.

The bytes under test are the template's own, with one substitution named per
case: the producing command becomes `cat <fixture>`, because `/grype` is an
absolute path inside a scanner image and cannot be stubbed on a workstation. The
pipeline after the producer, which is the part that was wrong, is verbatim.

Run: python3 -m pytest -q tests/runtime/scan/test_version_probes_survive_no_match.py
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = REPO_ROOT / "templates"

# (component, the variable assigned, the producer to replace, output that
#  matches, output that does not, the value expected from the matching output)
GRYPE_STATUS = "Built:\t2026-09-14T03:11:04Z\nStatus:\tvalid\n"

CASES = [
    ("security-filesystem-grype", "CI_TPL_DB_SNAPSHOT", "/grype db status",
     GRYPE_STATUS, "error: no database\n", "2026-09-14T03:11:04Z"),
    ("security-filesystem-grype", "CI_TPL_DB_STATUS", "/grype db status",
     GRYPE_STATUS, "error: no database\n", "valid"),
    ("security-image-grype", "CI_TPL_DB_UPDATED_AT", "/grype db status",
     GRYPE_STATUS, "error: no database\n", "2026-09-14T03:11:04Z"),
]


def script_text(component: str) -> str:
    documents = list(yaml.safe_load_all((TEMPLATES / component / "template.yml").read_text()))
    (job,) = documents[1].values()
    return "\n".join(job.get("before_script", []) + job.get("script", []))


def assignment(component: str, variable: str) -> str:
    """The one line of the component's shell that assigns `variable`."""
    lines = [
        line.strip()
        for line in script_text(component).splitlines()
        if line.strip().startswith(f"{variable}=$(")
    ]
    assert len(lines) == 1, f"{component}: expected one {variable} assignment, saw {lines}"
    return lines[0]


def run(line: str, producer: str, output: str, tmp_path: Path):
    fixture = tmp_path / "producer.out"
    fixture.write_text(output)
    assert producer in line, f"{producer!r} not in {line!r}"
    script = line.replace(producer, f"cat {fixture}", 1)
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", f'{script}\nprintf "%s" "${{{script.split("=")[0]}}}"'],
        capture_output=True, text=True,
    )


@pytest.mark.parametrize(
    "component,variable,producer,matching,empty,expected",
    CASES,
    ids=[f"{case[0]}:{case[1]}" for case in CASES],
)
def test_the_probe_reads_its_value_and_survives_output_without_it(
    component, variable, producer, matching, empty, expected, tmp_path
):
    found = run(assignment(component, variable), producer, matching, tmp_path)
    assert found.returncode == 0, found.stderr
    assert found.stdout.strip() == expected, found.stdout

    absent = run(assignment(component, variable), producer, empty, tmp_path)
    assert absent.returncode == 0, (
        f"{component}: {variable} killed the job on output without the field:\n"
        + absent.stderr
    )
    assert absent.stdout.strip() == "", absent.stdout


def test_the_trivy_database_probe_survives_a_report_without_an_update_time(tmp_path):
    """Same shape, reading a file rather than a pipe: the old line was
    `grep -o ... | head -1 | cut`, and an absent UpdatedAt aborted before
    scan-gate.sh could fail on the missing snapshot with its own message."""
    # The assignment is wrapped across two lines in the template; take both.
    text = script_text("security-filesystem-trivy")
    start = text.index("CI_TPL_DB_SNAPSHOT=$(")
    end = text.index("\n", text.index("trivy-version.json", start))
    line = text[start:end]

    def probe(report: str) -> subprocess.CompletedProcess:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir(exist_ok=True)
        (artifacts / "trivy-version.json").write_text(report)
        return subprocess.run(
            ["bash", "-eo", "pipefail", "-c",
             f'artifacts={artifacts}\n{line}\nprintf "%s" "$CI_TPL_DB_SNAPSHOT"'],
            capture_output=True, text=True,
        )

    found = probe('{"VulnerabilityDB": {"UpdatedAt": "2026-09-15T12:00:00Z"}}')
    assert found.returncode == 0, found.stderr
    assert found.stdout.strip() == "2026-09-15T12:00:00Z", found.stdout

    absent = probe('{"Version": "0.69.3"}')
    assert absent.returncode == 0, (
        "the trivy database probe killed the job on a report with no UpdatedAt:\n"
        + absent.stderr
    )
    assert absent.stdout.strip() == ""


def test_no_probe_still_pipes_a_capture_through_grep():
    """The anti-pattern itself, kept out of the four lines that had it.

    A capture that ends in `| grep ... | sed` reintroduces the abort, and the
    only symptom is a job that stops with no message.
    """
    offenders = []
    for path in sorted(TEMPLATES.glob("*/template.yml")):
        documents = list(yaml.safe_load_all(path.read_text()))
        # Hidden parents included: a probe would hide in one just as well.
        body = "\n".join(
            line
            for job in documents[1].values()
            for line in job.get("before_script", []) + job.get("script", [])
        )
        for line in body.splitlines():
            stripped = line.strip()
            if not re.match(r"^CI_TPL_[A-Z_]+=\$\(", stripped):
                continue
            if "| grep " in stripped or "|grep " in stripped:
                offenders.append(f"{path.parent.name}: {stripped}")
    assert not offenders, "\n".join(offenders)
