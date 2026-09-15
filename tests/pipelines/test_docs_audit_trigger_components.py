"""Merged-configuration lint for the docs, audit and trigger components.

Each case resolves a component's inputs the way GitLab does (see
resolve_component.py), then sends the resulting jobs to the CI Lint API with
dry_run, so the server decides whether the configuration compiles and which jobs
it would create. A lint is not an execution and nothing here runs a pipeline.

One thing the API does not offer: choosing the pipeline source it simulates.
`dry_run` evaluates rules as a push to the default branch, so a job whose
shipped default is `schedule` or `web` creates nothing and the lint reports
"The pipeline did not run". The lint cases below therefore pass an explicit
`run-rules`, and the shipped defaults are asserted separately in
test_shipped_run_rules_*. That split is deliberate: overriding the rules for
every case would have quietly stopped anyone checking them.

None of these components consumes another job's artifacts, so there is no
producer fixture and no missing-producer case to prove. Their `dependencies: []`
is asserted instead, which is the section 8.6 shape for a source-only job.

Needs GITLAB_TOKEN with api scope, and GITLAB_URL/GITLAB_PROJECT_ID to point
at your own GitLab; see README.md. The suite
skips without it, because a missing credential is not evidence of a broken
pipeline.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
REPO_ROOT = HERE.parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_module = load("ci_lint", HERE / "lint.py")
resolve_component = load("resolve_component", HERE / "resolve_component.py")

needs_token = pytest.mark.skipif(
    not os.environ.get("GITLAB_TOKEN"),
    reason="GITLAB_TOKEN is not set; see README.md, Running the tests",
)

# Inputs with no default, per component.
REQUIRED = {
    "docs-wiki-sync": {},
    "release-trigger-jenkins": {"job-path": "audit/tls-cert-expiry"},
    "release-trigger-semaphore": {
        "project-name": "platform-ops",
        "template-name": "nightly-baseline-drift",
    },
    "security-repository-audit": {"repositories": "platform/terraform-modules"},
}
COMPONENTS = sorted(REQUIRED)

# Rules that fire in the context dry_run simulates. Only for linting.
LINTABLE_RULES = [{"if": "$CI_COMMIT_BRANCH"}]
PERMISSIVE_WORKFLOW = {"rules": [{"if": "$CI_COMMIT_BRANCH"}]}


def inputs_for(component: str, instance: str) -> dict:
    return {
        "instance": instance,
        "run-rules": LINTABLE_RULES,
        **REQUIRED[component],
    }


def lint(content: str) -> dict:
    return lint_module.lint(content, include_jobs=True, dry_run=True)


def run_rules_default(component: str) -> list:
    declared, _ = resolve_component.load_template(component)
    return declared["run-rules"]["default"]


@pytest.mark.parametrize("component", COMPONENTS)
@needs_token
def test_one_instance_with_defaults_lints_and_emits_its_job(component):
    content = resolve_component.merged_config(
        [(component, inputs_for(component, "example"))],
        stages=["verify", "deploy"],
    )
    result = lint(content)
    assert result["valid"], result.get("errors")
    names = lint_module.job_names(result)
    assert names == [f"example:{component}"], names


@pytest.mark.parametrize("component", COMPONENTS)
@needs_token
def test_two_instances_emit_distinct_job_names(component):
    content = resolve_component.merged_config(
        [
            (component, inputs_for(component, "alpha")),
            (component, inputs_for(component, "beta")),
        ],
        stages=["verify", "deploy"],
    )
    result = lint(content)
    assert result["valid"], result.get("errors")
    assert sorted(lint_module.job_names(result)) == [
        f"alpha:{component}",
        f"beta:{component}",
    ]


def composition():
    """The composition's includes, resolved from the file itself.

    Reading the include list out of pipelines/repository-secret-audit.yml rather
    than restating it means an edit there that changes the emitted jobs fails
    these tests instead of quietly changing the estate's audit.
    """
    spec, body = list(
        yaml.safe_load_all(
            (REPO_ROOT / "pipelines" / "repository-secret-audit.yml").read_text()
        )
    )
    values = resolve_component.resolve_values(
        spec["spec"]["inputs"],
        {
            "repositories": "",
            "group-path": "platform",
            "terraform-repositories": "platform/terraform-modules platform/terraform-dns",
        },
    )
    resolved = resolve_component.interpolate(body, values)
    includes = [
        (Path(entry["local"]).parent.name, entry["inputs"]) for entry in resolved["include"]
    ]
    return resolved, includes


@needs_token
def test_the_repository_audit_composition_emits_both_audits():
    resolved, includes = composition()
    assert [component for component, _ in includes] == [
        "security-repository-audit",
        "security-repository-audit",
    ]
    content = resolve_component.merged_config(
        [
            (component, {**inputs, "run-rules": LINTABLE_RULES})
            for component, inputs in includes
        ],
        stages=resolved["stages"],
        workflow=PERMISSIVE_WORKFLOW,
    )
    result = lint(content)
    assert result["valid"], result.get("errors")
    assert sorted(lint_module.job_names(result)) == [
        "secrets:security-repository-audit",
        "terraform:security-repository-audit",
    ]


@needs_token
def test_the_audit_pipeline_creates_nothing_on_a_push():
    """The composition's own workflow, unmodified.

    An estate audit runs on its schedule or when somebody starts it. A push to
    the default branch must create no pipeline at all, which is what GitLab
    reports here.
    """
    resolved, includes = composition()
    content = resolve_component.merged_config(
        includes, stages=resolved["stages"], workflow=resolved["workflow"]
    )
    result = lint(content)
    assert not result["valid"]
    assert any("did not run" in message for message in result.get("errors", []))


@needs_token
def test_a_component_emitting_a_bare_legacy_job_name_is_not_what_we_ship():
    """Positive control for the job-name assertions above.

    They read names out of a lint result. This proves the assertion can fail:
    the same API, asked about the old `wiki` job name, returns it.
    """
    result = lint('wiki:\n  stage: test\n  script:\n    - "true"\n')
    assert result["valid"], result.get("errors")
    assert lint_module.job_names(result) == ["wiki"]


@pytest.mark.parametrize("component", COMPONENTS)
def test_a_source_only_job_declares_no_incidental_artifact_downloads(component):
    """Section 8.6, and not 8.7: `dependencies: []` rather than `needs: []`."""
    jobs = resolve_component.resolve(
        component, instance="example", **REQUIRED[component]
    )
    (job,) = jobs.values()
    assert job["dependencies"] == []
    assert "needs" not in job


@pytest.mark.parametrize(
    "component", ["release-trigger-jenkins", "release-trigger-semaphore"]
)
def test_shipped_run_rules_keep_mutations_off_merge_requests_and_manual(component):
    """The defect: promote/*-trigger.yml carried no rules at all, so extending
    the base produced a job that fired on every pipeline source, merge request
    pipelines included."""
    rules = run_rules_default(component)
    assert rules[0] == {
        "if": '$CI_PIPELINE_SOURCE == "merge_request_event"',
        "when": "never",
    }
    for rule in rules[1:]:
        assert rule["when"] == "manual", rule
        assert "if" in rule, rule


def test_shipped_run_rules_for_the_audit_are_schedule_or_web_only():
    rules = run_rules_default("security-repository-audit")
    assert rules == [
        {"if": '$CI_PIPELINE_SOURCE == "schedule"'},
        {"if": '$CI_PIPELINE_SOURCE == "web"'},
    ]


def test_shipped_run_rules_for_wiki_sync_name_their_pipeline_sources():
    """Section 11.6: "Use explicit pipeline intent for wiki events/schedules"."""
    rules = run_rules_default("docs-wiki-sync")
    sources = [rule["if"].split('"')[1] for rule in rules]
    assert sources == ["push", "trigger", "schedule"]
    for rule in rules:
        assert "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH" in rule["if"]
        assert "$WIKI_TOKEN" in rule["if"]


@pytest.mark.parametrize("component", COMPONENTS)
def test_no_shipped_rule_runs_unconditionally(component):
    """Section 6.1: never default a mutation to unconditional execution."""
    for rule in run_rules_default(component):
        assert isinstance(rule, dict) and rule.get("if"), rule


@needs_token
def test_this_repositorys_own_pipeline_compiles_with_the_audit_components():
    """The .gitlab-ci.yml edit that replaced the two scheduled security/ includes.

    Local includes cannot be resolved by the API against an unmerged branch, so
    they are resolved here and the result is linted. On the default-branch push
    the dry run simulates, the self-test jobs are created and neither audit is:
    both are schedule-or-web, which is the gating the old jobs had.
    """
    config = yaml.safe_load((REPO_ROOT / ".gitlab-ci.yml").read_text())
    includes = config.pop("include")
    for entry in includes:
        component = Path(entry["local"]).parent.name
        config.update(resolve_component.resolve(component, **entry["inputs"]))

    result = lint(yaml.safe_dump(config, sort_keys=False, width=200))
    assert result["valid"], result.get("errors")
    names = lint_module.job_names(result)
    assert {"validate:templates", "contracts", "runtime-tests"} <= set(names), names
    assert not [name for name in names if name.endswith(":security-repository-audit")], names


FORBIDDEN_TOP_LEVEL = {
    "stages", "workflow", "default", "variables", "image", "cache", "before_script"
}


@pytest.mark.parametrize("component", COMPONENTS)
def test_no_template_declares_forbidden_top_level_keys(component):
    """Section 6.1. docs/wiki-sync.yml declared a top-level `variables:` block,
    which merged into every consumer's global variables and overwrote their own
    DOCS_DIR."""
    _, body = resolve_component.load_template(component)
    assert FORBIDDEN_TOP_LEVEL.isdisjoint(body), sorted(FORBIDDEN_TOP_LEVEL & set(body))


@pytest.mark.parametrize("component", COMPONENTS)
def test_artifacts_stay_under_the_components_own_root(component):
    """Sections 5.3 and 9.4. security/terraform-audit.yml published `repos/` —
    three complete clones, state files included — and every other template wrote
    bare repo-root filenames that two instances would collide on."""
    jobs = resolve_component.resolve(component, instance="example", **REQUIRED[component])
    (job,) = jobs.values()
    artifacts = job.get("artifacts")
    if artifacts is None:
        return
    for path in artifacts["paths"]:
        assert path.startswith(f".ci-artifacts/example/{component}"), path
    assert "example" in artifacts["name"] and component in artifacts["name"]


def test_the_wiki_job_installs_only_hash_pinned_dependencies():
    """Section 12. The old job ran `pip install -q pyyaml`: no version, no hash,
    so the artefact it installed was whatever the index served that minute."""
    jobs = resolve_component.resolve("docs-wiki-sync", instance="example")
    (job,) = jobs.values()
    install = [step for step in job["before_script"] if "pip install" in step]
    assert len(install) == 1, install
    assert "--require-hashes" in install[0]
    assert "--no-deps" in install[0]

    requirements = (REPO_ROOT / "runtime" / "wiki" / "requirements.txt").read_text()
    assert "pyyaml==6.0.2" in requirements
    assert requirements.count("--hash=sha256:") >= 1


def test_the_semaphore_trigger_defaults_to_dry_run():
    """Section 11.3. SEMAPHORE_TASK_DRYRUN defaulted to false in the old
    template: launching Ansible against the estate was the default mode."""
    declared, _ = resolve_component.load_template("release-trigger-semaphore")
    assert declared["dry-run"]["default"] is True


# ------------------------------------------------- job-path character classes
#
# platform/infrastructure's seed job is `_seed-all-dsl-jobs`. The first character class
# refused an underscore, so the include failed pipeline creation with "provided
# value does not match required RegEx pattern" -- an input validation error the
# consumer could do nothing about, since Jenkins allows the name.

LEADING_UNDERSCORE_JOB = "_seed-all-dsl-jobs"


@needs_token
def test_a_jenkins_job_path_may_start_with_an_underscore():
    content = resolve_component.merged_config(
        [(
            "release-trigger-jenkins",
            {
                "instance": "seed",
                "run-rules": LINTABLE_RULES,
                "job-path": LEADING_UNDERSCORE_JOB,
            },
        )],
        stages=["verify", "deploy"],
    )
    result = lint(content)
    assert result["valid"], result.get("errors")
    assert lint_module.job_names(result) == ["seed:release-trigger-jenkins"]


def test_the_job_path_regex_admits_an_underscore_only_where_jenkins_does():
    declared, _ = resolve_component.load_template("release-trigger-jenkins")
    pattern = declared["job-path"]["regex"]
    import re

    assert re.match(pattern, LEADING_UNDERSCORE_JOB)
    assert re.match(pattern, "audit/tls-cert-expiry")
    assert re.match(pattern, "folder/_inner")
    # Still not a path: these were refused before and stay refused.
    for refused in ("/absolute", "-leading-dash", ".dotfile", "has space", ""):
        assert not re.match(pattern, refused), refused
