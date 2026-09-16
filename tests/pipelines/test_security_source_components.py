"""Contract and merged-configuration checks for the source-verification components.

Two layers. The structural tests read the template files and need nothing but
the checkout: they hold the standard's component rules (sections 5, 6, 8, 9, 10)
in place for these six components. The lint tests send the composed
configuration to the real CI Lint API on gitlab.example.com and assert the job
names GitLab says it would create; they skip without GITLAB_TOKEN, because a
missing credential is not evidence of a broken pipeline.

Inputs are supplied through the spec header by tests/pipelines/compose_fixture.py,
so GitLab performs the defaults, the regex validation and the substitution
rather than this suite re-implementing them. What it does not prove is that
`include: local:` with `inputs:` resolves, because an include resolves against
the branch on the server and this work is not pushed. It is a lint. Nothing here
runs a pipeline and no scanner is executed.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import re
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

import compose_fixture as cf  # noqa: E402

# The resolvers live in tools/resolve/ so the lint harness and the local
# runner share one implementation; see that package's docstring.
from tools.resolve import render_component as rc  # noqa: E402

REPO_ROOT = HERE.parent.parent
TEMPLATES = REPO_ROOT / "templates"

COMPONENTS = [
    "security-secrets-gitleaks",
    "security-sast-semgrep",
    "security-filesystem-trivy",
    "security-filesystem-grype",
    "quality-dependency-lockfiles",
    "quality-sonarqube",
]

STAGE_VOCABULARY = {
    "verify", "build", "test", "scan", "plan",
    "attest", "publish", "deploy", "verify-deploy",
}

# Runtime variables a job may set that are not CI_TPL_-prefixed. Each one
# configures the tool or the runner rather than carrying the component's own
# state, which is what the CI_TPL_ prefix reserves.
EXTERNAL_VARIABLES = {
    "FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR",
    "GIT_DEPTH",
    "SONAR_USER_HOME",
    "TRIVY_NO_PROGRESS",
    "SEMGREP_SEND_METRICS",
}

# The minimal inputs each component needs: only those with no default.
MINIMAL = {
    "security-secrets-gitleaks": {"instance": "demo"},
    "security-sast-semgrep": {"instance": "demo", "rules-ref": "ci/semgrep-rules.yml"},
    "security-filesystem-trivy": {"instance": "demo"},
    "security-filesystem-grype": {"instance": "demo"},
    "quality-dependency-lockfiles": {
        "instance": "demo",
        "lockfiles": "requirements.txt,services/api/package-lock.json",
    },
    "quality-sonarqube": {
        "instance": "demo",
        "sarif-report-jobs": [
            {"job": "demo:security-sast-semgrep", "artifacts": True},
        ],
    },
}

STAGES = ["verify"]


def load_lint_module():
    spec = importlib.util.spec_from_file_location("ci_lint", HERE / "lint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_module = load_lint_module()

needs_token = pytest.mark.skipif(
    not os.environ.get("GITLAB_TOKEN"),
    reason="GITLAB_TOKEN is not set; see README.md, Running the tests",
)


def lint(content: str) -> dict:
    return lint_module.lint(content, include_jobs=True, dry_run=True)


def component(name: str) -> tuple[dict, dict]:
    documents = list(yaml.safe_load_all((TEMPLATES / name / "template.yml").read_text()))
    return documents[0]["spec"]["inputs"], documents[1]


def job_logic(job: dict) -> str:
    """The job's own shell, with the embedded runtime region removed.

    The runtime helpers quote the forbidden patterns in their own comments and
    own the advisory `exit 0`; they are covered by tests/runtime/scan/. What this
    returns is the logic the template itself contributes.
    """
    script = "\n".join(job.get("before_script", []) + job.get("script", []))
    region = re.compile(
        r"^(?P<indent>[ ]*)# BEGIN embed [^\n]+\n.*?^(?P=indent)# END embed$",
        re.DOTALL | re.MULTILINE,
    )
    return region.sub("", script)


@pytest.mark.parametrize("name", COMPONENTS)
def test_the_public_input_surface(name: str):
    inputs, _ = component(name)

    assert inputs["instance"]["regex"] == "^[a-z][a-z0-9-]{0,47}$"
    assert "default" not in inputs["instance"], "instance identifies the job and cannot be defaulted"
    assert inputs["stage"]["default"] in STAGE_VOCABULARY
    assert inputs["runner-tags"] == {
        "type": "array",
        "description": "Runner selection from the estate profile.",
        "default": [],
    }
    assert inputs["run-rules"]["type"] == "array"
    assert inputs["run-rules"]["default"], "a component must declare safe source rules"
    assert inputs["working-directory"]["default"] == "."

    image = inputs["execution-image"]
    assert image["regex"] == "^.+@sha256:[0-9a-f]{64}$"
    assert "@sha256:" in image["default"], "the shipped default must be a digest, not a tag"


@pytest.mark.parametrize("name", COMPONENTS)
def test_the_template_owns_no_global_configuration(name: str):
    _, jobs = component(name)
    for key in ("stages", "workflow", "default", "variables", "image", "cache", "before_script"):
        assert key not in jobs, f"{key} at the top level belongs to a composition, not a component"
    assert len(jobs) == 1, "each of these components emits exactly one job"


@pytest.mark.parametrize("name", COMPONENTS)
def test_the_job_name_follows_the_grammar(name: str):
    _, jobs = component(name)
    assert list(jobs) == [f"$[[ inputs.instance ]]:{name}"]


@pytest.mark.parametrize("name", COMPONENTS)
def test_the_dependency_graph_is_declared_once(name: str):
    _, jobs = component(name)
    job = next(iter(jobs.values()))
    assert not ("needs" in job and "dependencies" in job), "needs and dependencies never coexist"
    if "needs" in job:
        # quality-sonarqube consumes producer artifacts, so its needs list is a
        # required input rather than a literal. `needs: []` is never emitted by
        # the template itself.
        assert job["needs"] == "$[[ inputs.sarif-report-jobs ]]"
    else:
        assert job["dependencies"] == [], "a source-only job declares dependencies: []"


@pytest.mark.parametrize("name", COMPONENTS)
def test_no_outcome_is_swallowed(name: str):
    """Section 10.1, the rule the semgrep and sonarqube templates broke."""
    _, jobs = component(name)
    job = next(iter(jobs.values()))
    script = job_logic(job)
    assert job["allow_failure"] is False
    assert "|| true" not in script
    assert "exit 0" not in script, "an advisory outcome is decided by the wrapper, not by exit 0"


@pytest.mark.parametrize("name", COMPONENTS)
def test_artifacts_are_namespaced_and_carry_no_native_report(name: str):
    _, jobs = component(name)
    artifacts = next(iter(jobs.values()))["artifacts"]
    root = f".ci-artifacts/$[[ inputs.instance ]]/{name}/"
    assert artifacts["paths"] == [root]
    assert "$[[ inputs.instance ]]" in artifacts["name"] and name in artifacts["name"]
    assert artifacts["when"] == "always", "evidence must survive a failing gate"
    # Section 10.3 and estate report_mode artifact-only: raw tool SARIF is not a
    # GitLab native report, and this estate renders none of it.
    assert "reports" not in artifacts


@pytest.mark.parametrize("name", COMPONENTS)
def test_internal_variables_are_reserved_and_the_working_directory_is_validated(name: str):
    _, jobs = component(name)
    job = next(iter(jobs.values()))
    for variable in job.get("variables", {}):
        assert variable.startswith("CI_TPL_") or variable in EXTERNAL_VARIABLES, variable
    guard = "ERROR: working-directory escapes the checkout"
    assert guard in "\n".join(job["before_script"])
    assert "eval " not in job_logic(job)


def test_the_sonarqube_gate_is_waited_on_and_read_back():
    """Section 10.3: an upload is not a gate result."""
    _, jobs = component("quality-sonarqube")
    script = job_logic(next(iter(jobs.values())))
    assert "-Dsonar.qualitygate.wait=true" in script
    assert "sonar_gate.py\" gate" in script


def with_producers(name: str) -> list[tuple[str, dict]]:
    """The component plus whatever it declares as a producer.

    quality-sonarqube cannot compile alone, by design: its `needs` are required
    inputs and GitLab refuses a pipeline that does not create them.
    """
    if name == "quality-sonarqube":
        return [("security-sast-semgrep", MINIMAL["security-sast-semgrep"]), (name, MINIMAL[name])]
    return [(name, MINIMAL[name])]


@needs_token
@pytest.mark.parametrize("name", COMPONENTS)
def test_a_single_instance_with_defaults_compiles(name: str):
    result = lint(cf.compose(STAGES, with_producers(name)))
    assert result["valid"], result.get("errors")
    assert f"demo:{name}" in lint_module.job_names(result)
    job = next(j for j in result["jobs"] if j["name"] == f"demo:{name}")
    assert job["stage"] == "verify"
    assert job["allow_failure"] is False


@needs_token
@pytest.mark.parametrize("name", COMPONENTS)
def test_two_instances_emit_distinct_job_names(name: str):
    """Section 5.2: two instances of one component never collide."""
    alpha = dict(MINIMAL[name], instance="alpha")
    beta = dict(MINIMAL[name], instance="beta")
    if name == "quality-sonarqube":
        # Each instance waits on its own producer, so the two graphs stay
        # separate; the producers themselves come from the fixture below.
        alpha["sarif-report-jobs"] = [{"job": "alpha:security-sast-semgrep", "artifacts": True}]
        beta["sarif-report-jobs"] = [{"job": "beta:security-sast-semgrep", "artifacts": True}]
        components = [
            ("security-sast-semgrep", dict(MINIMAL["security-sast-semgrep"], instance="alpha")),
            ("security-sast-semgrep", dict(MINIMAL["security-sast-semgrep"], instance="beta")),
            (name, alpha),
            (name, beta),
        ]
    else:
        components = [(name, alpha), (name, beta)]

    result = lint(cf.compose(STAGES, components))
    assert result["valid"], result.get("errors")
    emitted = lint_module.job_names(result)
    assert len(set(emitted)) == len(emitted)
    assert f"alpha:{name}" in emitted and f"beta:{name}" in emitted


@needs_token
def test_the_whole_source_verification_set_compiles_together():
    result = lint(cf.compose(STAGES, [(name, MINIMAL[name]) for name in COMPONENTS]))
    assert result["valid"], result.get("errors")
    assert sorted(lint_module.job_names(result)) == sorted(f"demo:{name}" for name in COMPONENTS)


@needs_token
def test_a_missing_sarif_producer_fails_pipeline_creation():
    """The contract that replaces the old `optional: true` degradation.

    GitLab refuses the pipeline when quality-sonarqube names a producer the
    pipeline does not create, so dropping the producing job is visible at once
    instead of silently turning the SARIF import off. A consumer `needs:`
    override REPLACES the list rather than merging with it, which is how
    platform/demo-app stopped importing SARIF without anything going red.
    """
    result = lint(cf.compose(STAGES, [("quality-sonarqube", MINIMAL["quality-sonarqube"])]))
    assert not result["valid"]
    assert any("undefined need" in message for message in result["errors"]), result["errors"]


@needs_token
def test_gitlab_rejects_an_instance_that_breaks_the_grammar():
    """The regex is GitLab's to enforce, so it is proved on GitLab."""
    result = lint(
        cf.compose(STAGES, [("security-secrets-gitleaks", {"instance": "Demo_1"})])
    )
    assert not result["valid"]
    assert any("RegEx" in message for message in result["errors"]), result["errors"]


@needs_token
def test_gitlab_requires_the_inputs_that_have_no_default():
    """semgrep's ruleset is a deliberate choice, not a default nobody reviewed."""
    header, body = cf.split_template("security-sast-semgrep")
    result = lint(header + "\n---\n" + "stages:\n  - verify\n" + body)
    assert not result["valid"]
    assert any("required value has not been provided" in message for message in result["errors"])


@pytest.mark.parametrize("name", COMPONENTS)
def test_no_component_declares_a_cache(name: str):
    """Section 9.4: a cache scoped by project alone mixes trust levels.

    sonarqube.yml cached .sonar under sonar-$CI_PROJECT_ID, with no tool
    version, architecture or protected/untrusted separation. The acceleration is
    not worth an unscoped cache, so none is declared.
    """
    _, jobs = component(name)
    assert "cache" not in next(iter(jobs.values()))


def test_gitleaks_sets_its_own_history_depth():
    """`gitleaks detect` scans commits, so an inherited shallow clone narrows it.

    The previous template took whatever GIT_DEPTH the consumer had configured
    and called the result history scanning.
    """
    inputs, jobs = component("security-secrets-gitleaks")
    assert inputs["history-depth"]["default"] == 0, "0 fetches the full history"
    assert next(iter(jobs.values()))["variables"]["GIT_DEPTH"] == "$[[ inputs.history-depth ]]"


def test_the_two_filesystem_scanners_read_their_threshold_the_same_way():
    """grype-fs.yml called itself a drop-in replacement for trivy-fs.yml.

    It was not: GRYPE_SEVERITY was a minimum severity and TRIVY_SEVERITY a
    severity list, so swapping the include changed which findings gated without
    changing a variable. Both components now take one canonical scale.
    """
    trivy, _ = component("security-filesystem-trivy")
    grype, _ = component("security-filesystem-grype")
    assert trivy["severity-threshold"]["options"] == grype["severity-threshold"]["options"]
    assert trivy["severity-threshold"]["default"] == grype["severity-threshold"]["default"]
    # The defaults that do differ are the ones preserving today's behaviour, and
    # each contract says so.
    assert trivy["ignore-unfixed"]["default"] is False
    assert grype["ignore-unfixed"]["default"] is True


@needs_token
def test_a_project_with_no_lockfile_declares_none_and_compiles():
    """The `none` sentinel is a real input value, not just runtime behaviour.

    The container compositions always instantiate
    `quality-dependency-lockfiles`, so before `none` existed a project with no
    third-party dependencies could not get a green pipeline: platform/demo-app is a
    stdlib-only Go module and its go.sum is empty. This lints the include the
    way such a consumer writes it.
    """
    result = lint(cf.compose(
        STAGES,
        [("quality-dependency-lockfiles", {"instance": "demo", "lockfiles": "none"})],
    ))
    assert result["valid"], result.get("errors")
    assert "demo:quality-dependency-lockfiles" in lint_module.job_names(result)


def test_the_none_sentinel_reaches_the_job_as_written():
    """A value GitLab rewrote on the way through would not reach the shell."""
    inputs, _ = component("quality-dependency-lockfiles")
    assert "default" not in inputs["lockfiles"], "declaring the set stays required"
    assert "none" in inputs["lockfiles"]["description"]
    rendered = rc.render(
        TEMPLATES / "quality-dependency-lockfiles" / "template.yml",
        instance="demo",
        lockfiles="none",
    )
    job = next(iter(rendered.values()))
    assert job["variables"]["CI_TPL_LOCKFILES"] == "none"
