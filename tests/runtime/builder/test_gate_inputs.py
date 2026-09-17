"""The two gate inputs on the three builders cannot disagree silently.

`gate-jobs` is an array and `gate-jobs-set` is the boolean the include rule
reads, because an array input cannot be tested for emptiness in an include rule.
Nothing in the YAML checks one against the other, and both mismatches are
invisible in a green pipeline: a list nothing reads, or `needs: []`, which is a
job with no edges and no stage barrier either.

Each test extracts the builder's OWN script entry and runs it, so what is
asserted is the guard the component really carries rather than a copy of it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = REPO_ROOT / "templates"
BUILDERS = ["container-build-buildkit", "container-build-jib", "container-build-ko"]
GUARD = "ci_tpl_require_gate_inputs || exit 1"
GATES = 'json [{"job": "api:security-image-trivy", "artifacts": false}]'


def guard_entry(component: str) -> str:
    """The component's before_script embed plus its own guard script entry."""
    _, jobs = list(yaml.safe_load_all((TEMPLATES / component / "template.yml").read_text()))
    (job,) = (value for key, value in jobs.items() if key != "include")
    embeds = [e for e in job["before_script"] if "ci_tpl_require_gate_inputs()" in e]
    entries = [e for e in job["script"] if GUARD in e]
    assert len(embeds) == 1, f"{component} embeds the guard {len(embeds)} times"
    assert len(entries) == 1, f"{component} calls the guard {len(entries)} times"
    return embeds[0] + "\n" + entries[0]


def run(component: str, gates: str, gates_set: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/sh", "-c", guard_entry(component)],
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "CI_TPL_GATE_JOBS": gates,
            "CI_TPL_GATE_JOBS_SET": gates_set,
        },
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("component", BUILDERS)
def test_a_gate_list_with_the_boolean_false_fails_naming_both_inputs(component):
    """The list is read by nothing: the include rule picks the stage barrier."""
    result = run(component, GATES, "false")
    assert result.returncode != 0
    assert "gate-jobs names jobs" in result.stderr
    assert "gate-jobs-set" in result.stderr


@pytest.mark.parametrize("component", BUILDERS)
def test_the_boolean_true_with_an_empty_list_fails_naming_both_inputs(component):
    """`needs: []`: no edges, and no stage barrier holding the build either."""
    result = run(component, "json []", "true")
    assert result.returncode != 0
    assert "gate-jobs-set is true" in result.stderr
    assert "gate-jobs is empty" in result.stderr


@pytest.mark.parametrize("component", BUILDERS)
@pytest.mark.parametrize(
    "gates,gates_set", [("json []", "false"), (GATES, "true")]
)
def test_the_two_agreeing_pass_silently(component, gates, gates_set):
    result = run(component, gates, gates_set)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize("component", BUILDERS)
def test_a_gate_list_that_is_not_a_json_list_reads_as_empty(component):
    """An unreadable list is no list: it must not read as one entry and let a
    `gate-jobs-set: false` build believe it is gated."""
    assert run(component, "api:security-image-trivy", "false").returncode == 0
    assert run(component, "api:security-image-trivy", "true").returncode != 0
