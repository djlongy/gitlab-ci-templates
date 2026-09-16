"""What the compositions in pipelines/ create, checked against the real server.

Every test here drives the composition files themselves, not a copy of their
wiring. `composition.resolve` reads `pipelines/<name>.yml`, applies its
`spec:inputs`, honours the `rules:` on each include and renders every included
component, which is the pair of substitutions GitLab performs. The result goes
to the CI Lint API, so the job names, the stage list and every `needs:` edge are
resolved by GitLab and not by this file.

The consumer examples under examples/ are the fixtures. That is deliberate: an
example that no test resolves is an example nobody has checked, and the audit
found two of those shipping a job name no template defined.

Limits, stated rather than implied:

  * A lint is not an execution. No pipeline runs here.
  * The Lint API's dry run simulates a default-branch push, so a tag-only or
    web-only job does not appear in its job list. Those shapes are asserted on
    the resolved configuration instead, by reading the job's own `rules:`.
  * GitLab is not asked to resolve `include: local:` with `inputs:`, because
    that needs the branch on the server. Release-time check, not this one.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# The resolvers live in tools/resolve/ so the lint harness and the local
# runner share one implementation; see that package's docstring.
from tools.resolve import composition  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "examples"
PIPELINES = REPO_ROOT / "pipelines"

STAGE_VOCABULARY = [
    "verify", "build", "test", "scan", "plan",
    "attest", "publish", "deploy", "verify-deploy",
]

MERGE_REQUEST = '$CI_PIPELINE_SOURCE == "merge_request_event"'
DEFAULT_BRANCH = '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
TAG = '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_TAG'
PROTECTED_TAG = (
    '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_TAG '
    '&& $CI_COMMIT_REF_PROTECTED == "true"'
)

# Every composition this lane owns, plus the audit composition it reviewed.
COMPOSITIONS = sorted(
    p.stem
    for p in PIPELINES.glob("*.yml")
    if p.stem != "devsecops" and not p.name.endswith(".contract.yml")
)
EXAMPLE_FILES = sorted(EXAMPLES.glob("*/.gitlab-ci.yml"))


def load_lint_module():
    spec = importlib.util.spec_from_file_location(
        "ci_lint", Path(__file__).parent / "lint.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_module = load_lint_module()

needs_token = pytest.mark.skipif(
    not os.environ.get("GITLAB_TOKEN"),
    reason="GITLAB_TOKEN is not set; see README.md, Running the tests",
)


def example_includes(path: Path) -> list[tuple[str, dict]]:
    """The (composition name, inputs) pairs an example asks for."""
    body = composition.load_yaml(path.read_text())
    return [(Path(e["file"]).stem, e["inputs"]) for e in body["include"]]


def example_config(path: Path) -> dict:
    """The configuration this example's includes produce, merged."""
    merged: dict = {}
    for name, inputs in example_includes(path):
        for key, value in composition.resolve(name, **inputs).items():
            if key in ("workflow", "stages"):
                merged.setdefault(key, value)
            else:
                assert key not in merged, f"{path}: two includes emit {key}"
                merged[key] = value
    return merged


# --------------------------------------------------------------------------
# Shape of the composition files themselves
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", COMPOSITIONS)
def test_exactly_one_workflow_and_one_stages(name: str):
    """Section 2: exactly one root composition owns workflow and stages."""
    _, body = composition.load(name)
    assert "workflow" in body, f"{name} declares no workflow"
    assert "stages" in body, f"{name} declares no stages"
    text = (PIPELINES / f"{name}.yml").read_text()
    assert sum(1 for line in text.splitlines() if line == "workflow:") == 1
    assert sum(1 for line in text.splitlines() if line == "stages:") == 1


@pytest.mark.parametrize("name", COMPOSITIONS)
def test_stages_are_an_ordered_subset_of_the_vocabulary(name: str):
    _, body = composition.load(name)
    stages = body["stages"]
    assert set(stages) <= set(STAGE_VOCABULARY), (
        f"{name} declares stages outside section 7.1: "
        f"{sorted(set(stages) - set(STAGE_VOCABULARY))}"
    )
    order = [STAGE_VOCABULARY.index(s) for s in stages]
    assert order == sorted(order), f"{name} declares {stages} out of section 7.1 order"


@pytest.mark.parametrize("name", COMPOSITIONS)
def test_a_composition_never_exposes_run_rules(name: str):
    """Section 6.2: source eligibility is the composition's, not the consumer's."""
    declared, _ = composition.load(name)
    assert "run-rules" not in declared, (
        f"{name} exposes run-rules, which hands source eligibility to the consumer"
    )


@pytest.mark.parametrize("name", COMPOSITIONS)
def test_the_workflow_prevents_duplicate_pipelines(name: str):
    """A branch pipeline must not run beside the merge request pipeline."""
    _, body = composition.load(name)
    conditions = [r.get("if", "") for r in body["workflow"]["rules"]]
    if MERGE_REQUEST not in conditions:
        # No merge request pipelines at all, so there is nothing to duplicate.
        return
    guard = [
        r for r in body["workflow"]["rules"]
        if r.get("if") == "$CI_COMMIT_BRANCH && $CI_OPEN_MERGE_REQUESTS"
    ]
    assert guard and guard[0].get("when") == "never", (
        f"{name} admits merge request pipelines with no branch-pipeline guard"
    )
    assert body["workflow"]["rules"][-1] == {"when": "never"}, (
        f"{name} does not close its workflow rules with `when: never`"
    )


@pytest.mark.parametrize("name", COMPOSITIONS)
def test_at_most_one_builder(name: str):
    """Section 6.2: one builder per image.

    Counted by builder, not by include entry: a composition includes the same
    builder once per build-sources set, and only one of those entries is ever
    active. That the authoritative SBOM producer is also single is asserted on
    the resolved configuration, in
    test_one_authoritative_sbom_producer_per_source_set, because two active
    copies of one component is a job-name clash the resolver raises on and two
    inactive copies is how the source sets are expressed.
    """
    text = (PIPELINES / f"{name}.yml").read_text()
    builders = {
        tool for tool in ("buildkit", "ko", "jib")
        if f"/templates/container-build-{tool}/template.yml" in text
    }
    assert len(builders) <= 1, f"{name} includes several builders: {sorted(builders)}"


# --------------------------------------------------------------------------
# The graph each example produces
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.parent.name)
def test_an_example_is_an_include_and_nothing_else(path: Path):
    """Section 13.4: a project, a ref, a file and inputs. Nothing else."""
    body = composition.load_yaml(path.read_text())
    assert set(body) == {"include"}, (
        f"{path.parent.name} declares {sorted(set(body) - {'include'})} beside its include"
    )
    for entry in body["include"]:
        assert set(entry) == {"project", "ref", "file", "inputs"}, sorted(entry)
        assert entry["ref"] == "REPLACE_WITH_APPROVED_SHARED_CI_REF", (
            "an example pins the placeholder, never a branch name"
        )
        assert entry["file"].startswith("/pipelines/")


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.parent.name)
def test_every_example_resolves(path: Path):
    """Input names, regexes and required inputs, checked against the real spec."""
    config = example_config(path)
    assert composition.jobs_only(config), f"{path.parent.name} creates no jobs"


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.parent.name)
def test_every_job_belongs_to_a_declared_stage(path: Path):
    config = example_config(path)
    stages = set(config["stages"])
    for name, job in composition.jobs_only(config).items():
        assert job["stage"] in stages, f"{name} is in stage {job['stage']}, not in {stages}"


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.parent.name)
def test_every_need_names_a_job_that_exists(path: Path):
    config = example_config(path)
    jobs = composition.jobs_only(config)
    for name, job in jobs.items():
        for needed in composition.needs_of(job):
            assert needed in jobs, f"{name} needs {needed}, which nothing creates"


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.parent.name)
def test_no_required_producer_is_optional(path: Path):
    """Section 8.3: required producers and mandatory gates are never optional."""
    for name, job in composition.jobs_only(example_config(path)).items():
        for need in job.get("needs", []) or []:
            if isinstance(need, dict):
                assert not need.get("optional"), (
                    f"{name} marks {need['job']} optional; a gate that may be "
                    "absent is not a gate"
                )


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.parent.name)
def test_a_job_never_combines_needs_and_dependencies(path: Path):
    """Section 8.5."""
    for name, job in composition.jobs_only(example_config(path)).items():
        assert not ("needs" in job and "dependencies" in job), name


# The one implication these compositions rely on, written down rather than
# inferred. A protected tag push is a tag push, so a job gated on the protected
# tag runs in a pipeline where a job gated on any tag also runs. Nothing else is
# treated as implying anything: a general rule engine written here could agree
# with itself and be confidently wrong about GitLab.
IMPLIES = {PROTECTED_TAG: TAG}


def sources_of(job: dict) -> set[str]:
    return {IMPLIES.get(c, c) for c in composition.rule_conditions(job)}


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.parent.name)
def test_a_producer_runs_wherever_its_consumer_does(path: Path):
    """The missing-producer class, caught structurally rather than by simulation.

    A job that needs another must not be eligible on a pipeline source the
    producer is not eligible on: GitLab would then be asked to create a job
    whose producer the rules excluded, and pipeline creation fails.

    Conditions are compared as written, through the single documented
    implication above. That is deliberately less clever than evaluating them:
    an unrecognised condition shows up as a failure to investigate rather than
    as a silent pass.
    """
    jobs = composition.jobs_only(example_config(path))
    for name, job in jobs.items():
        mine = sources_of(job)
        if not mine:
            continue
        for needed in composition.needs_of(job):
            theirs = sources_of(jobs[needed])
            assert mine <= theirs, (
                f"{name} is eligible on {sorted(mine - theirs)}, which its "
                f"producer {needed} is not"
            )


# --------------------------------------------------------------------------
# Release controls
# --------------------------------------------------------------------------

def test_promotion_is_manual_and_needs_a_protected_tag():
    config = example_config(EXAMPLES / "container-buildkit" / ".gitlab-ci.yml")
    promote = composition.jobs_only(config)["api:container-promote-harbor"]
    rules = promote["rules"]
    assert rules[0]["if"] == PROTECTED_TAG, rules
    assert rules[0]["when"] == "manual", rules
    assert rules[-1] == {"when": "never"}, rules
    gated = set(composition.needs_of(promote))
    assert {"api:security-image-trivy", "api:container-sign-attest-cosign"} <= gated, gated


def test_terraform_apply_is_manual_and_names_its_plan():
    config = example_config(EXAMPLES / "terraform-deploy" / ".gitlab-ci.yml")
    apply = composition.jobs_only(config)["network:terraform-apply"]
    assert composition.needs_of(apply) == ["network:terraform-plan"], apply["needs"]
    assert all(r.get("when") == "manual" for r in apply["rules"]), apply["rules"]


def test_signing_waits_for_the_smoke_test_when_it_is_enabled():
    """The gate list is chosen by the include's own rules, so prove both branches."""
    off = composition.resolve(
        "container-buildkit",
        instance="api",
        **{"image-repository": "registry.example.com/dev/x/api",
           "semgrep-rules": "ci/rules.yml", "lockfiles": "go.sum"},
    )
    on = composition.resolve(
        "container-buildkit",
        instance="api",
        **{"image-repository": "registry.example.com/dev/x/api",
           "semgrep-rules": "ci/rules.yml", "lockfiles": "go.sum",
           "smoke-test": "true"},
    )
    assert "api:container-smoke-test" not in off
    assert "api:container-smoke-test" in on
    sign = "api:container-sign-attest-cosign"
    assert "api:container-smoke-test" not in composition.needs_of(off[sign])
    assert "api:container-smoke-test" in composition.needs_of(on[sign])


def test_promotion_waits_for_the_vigil_verdict_when_vigil_is_enabled():
    base = {"image-repository": "registry.example.com/dev/x/api",
            "semgrep-rules": "ci/rules.yml", "lockfiles": "go.sum"}
    on = composition.resolve(
        "container-buildkit", instance="api",
        **base, **{"vigil": "true", "vigil-url": "https://vigil.example.com"},
    )
    promote = on["api:container-promote-harbor"]
    assert "api:security-verify-vigil" in composition.needs_of(promote)


def test_the_release_scanners_are_blocking_by_default():
    """Section 10.1: a release example must not ship a non-blocking gate."""
    config = example_config(EXAMPLES / "container-buildkit" / ".gitlab-ci.yml")
    jobs = composition.jobs_only(config)
    modes = {
        "api:security-sast-semgrep": "blocking",
        "api:security-filesystem-trivy": "blocking",
        "api:security-image-trivy": "blocking",
        "api:security-secrets-gitleaks": "blocking",
    }
    for name, expected in modes.items():
        variables = jobs[name]["variables"]
        assert variables["CI_TPL_POLICY_MODE"] == expected, (name, variables)
        assert not jobs[name].get("allow_failure"), name


def test_only_the_onboarding_example_relaxes_a_gate():
    onboarding = example_config(
        EXAMPLES / "container-buildkit-onboarding" / ".gitlab-ci.yml"
    )
    jobs = composition.jobs_only(onboarding)
    assert jobs["api:security-sast-semgrep"]["variables"]["CI_TPL_POLICY_MODE"] == "advisory"
    # Everything else stays blocking, including secret detection.
    for name in (
        "api:security-secrets-gitleaks",
        "api:security-filesystem-trivy",
        "api:security-image-trivy",
    ):
        assert jobs[name]["variables"]["CI_TPL_POLICY_MODE"] == "blocking", name
    assert "onboarding" in (EXAMPLES / "container-buildkit-onboarding").name


def test_two_instances_of_one_composition_do_not_collide():
    """Section 8.10, repeat inclusion. The terraform-verify example does this."""
    config = example_config(EXAMPLES / "terraform-verify" / ".gitlab-ci.yml")
    names = sorted(composition.jobs_only(config))
    assert names == [
        "dns:security-filesystem-trivy",
        "dns:terraform-fmt",
        "dns:terraform-validate",
        "network:security-filesystem-trivy",
        "network:terraform-fmt",
        "network:terraform-validate",
    ], names
    artifacts = [
        path
        for job in composition.jobs_only(config).values()
        for path in (job.get("artifacts", {}) or {}).get("paths", [])
    ]
    assert len(set(artifacts)) == len(artifacts), f"two instances share an artifact path: {artifacts}"


@needs_token
def test_a_module_release_validates_without_a_committed_lockfile():
    """The composition, not the consumer, chooses the module lockfile mode.

    A consumer pipeline on the originating estate, 2026/09, failed every *:terraform-validate
    job with "Provider dependency changes detected ... the lock file is
    read-only", because a module repository commits no .terraform.lock.hcl and
    terraform-module-publish excludes it from the archive anyway.

    The variable is asserted on the configuration that is sent: the Lint API
    returns each job's name, stage, scripts and rules, and does not echo
    `variables:`. What the server adds is that this configuration compiles.
    Its dry run simulates a default-branch push, so the two verify jobs appear
    and the protected-tag publish job does not.
    """
    config = composition.resolve(
        "terraform-module",
        instance="hypervisor-vm",
        **{"module-name": "vm", "module-system": "hypervisor"},
    )
    validate = config["hypervisor-vm:terraform-validate"]
    assert validate["variables"]["CI_TPL_LOCKFILE_MODE"] == "module"
    assert "lockfile-mode" not in composition.load("terraform-module")[0], (
        "the mode is a property of a module repository, not a consumer choice"
    )
    # terraform-verify is the root-module composition and must keep the default.
    verify = composition.resolve("terraform-verify", instance="dns")
    assert verify["dns:terraform-validate"]["variables"]["CI_TPL_LOCKFILE_MODE"] == "readonly"

    result = lint_module.lint(
        composition.dump_yaml(config), include_jobs=True, dry_run=True
    )
    assert result["valid"], result.get("errors")
    assert set(lint_module.job_names(result)) == {
        "hypervisor-vm:terraform-fmt",
        "hypervisor-vm:terraform-validate",
    }, lint_module.job_names(result)


def test_merge_request_pipelines_carry_verification_and_no_mutation():
    """Section 7.2: a merge request verifies; it does not build or publish."""
    config = example_config(EXAMPLES / "container-buildkit" / ".gitlab-ci.yml")
    jobs = composition.jobs_only(config)
    on_merge_request = {
        name for name, job in jobs.items()
        if MERGE_REQUEST in composition.rule_conditions(job)
    }
    assert on_merge_request == {
        "api:security-secrets-gitleaks",
        "api:security-sast-semgrep",
        "api:security-filesystem-trivy",
        "api:quality-dependency-lockfiles",
    }, sorted(on_merge_request)


def test_tag_pipelines_carry_the_whole_release_chain():
    config = example_config(EXAMPLES / "container-buildkit" / ".gitlab-ci.yml")
    jobs = composition.jobs_only(config)
    on_tag = {
        name for name, job in jobs.items()
        if TAG in composition.rule_conditions(job)
    }
    for required in (
        "api:container-build-buildkit",
        "api:security-sbom-syft",
        "api:security-image-trivy",
        "api:container-sign-attest-cosign",
    ):
        assert required in on_tag, f"{required} does not run on a tag: {sorted(on_tag)}"
    promote = jobs["api:container-promote-harbor"]
    assert composition.rule_conditions(promote) == {PROTECTED_TAG}, promote["rules"]


# --------------------------------------------------------------------------
# The real server
# --------------------------------------------------------------------------

@needs_token
@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.parent.name)
def test_every_example_compiles_on_the_server(path: Path):
    result = lint_module.lint(
        composition.dump_yaml(example_config(path)), dry_run=False
    )
    assert result["valid"], result.get("errors")
    assert lint_module.job_names(result)


@needs_token
def test_a_missing_producer_fails_pipeline_creation():
    """Positive control for the graph tests above.

    Every assertion in this module would pass on a resolver that quietly dropped
    a `needs:` edge. This one asks GitLab to compile a configuration whose scan
    job needs a build job that is not there, and requires it to be rejected. If
    GitLab accepted it, the missing-producer case would not be a real failure
    mode and the tests above would be checking nothing.
    """
    config = composition.resolve(
        "container-buildkit",
        instance="api",
        **{"image-repository": "registry.example.com/dev/x/api",
           "semgrep-rules": "ci/rules.yml", "lockfiles": "go.sum"},
    )
    del config["api:container-build-buildkit"]
    result = lint_module.lint(composition.dump_yaml(config), dry_run=False)
    assert not result["valid"], "GitLab accepted a job whose producer is absent"
    assert any("container-build-buildkit" in e for e in result["errors"]), result["errors"]


# --------------------------------------------------------------------------
# build-sources: which pushes produce a release candidate
# --------------------------------------------------------------------------

CONTAINER_COMPOSITIONS = ["container-buildkit", "container-ko", "container-jib"]

# Enough to resolve any of the three; each takes the same required inputs.
CONTAINER_INPUTS = {
    "image-repository": "registry.example.com/dev/x/api",
    "semgrep-rules": "ci/rules.yml",
    "lockfiles": "go.sum",
}

# The jobs a candidate push produces. Promotion is not one of them: it is gated
# on a protected tag in both modes and is the release set, not the candidate set.
VERIFY_JOBS = {
    "security-secrets-gitleaks",
    "security-sast-semgrep",
    "security-filesystem-trivy",
    "quality-dependency-lockfiles",
}


def container_jobs(name: str, **extra) -> dict:
    return composition.jobs_only(
        composition.resolve(name, instance="api", **CONTAINER_INPUTS, **extra)
    )


def component_of(job_name: str) -> str:
    return job_name.split(":", 1)[1]


@pytest.mark.parametrize("name", CONTAINER_COMPOSITIONS)
def test_build_sources_defaults_to_todays_behaviour(name: str):
    """A consumer that does not set the input gets the pipeline it has today."""
    declared, _ = composition.load(name)
    spec = declared["build-sources"]
    assert spec["default"] == "default-branch-and-tags"
    assert spec["options"] == ["default-branch-and-tags", "tags-only"]
    default = container_jobs(name)
    explicit = container_jobs(name, **{"build-sources": "default-branch-and-tags"})
    assert default == explicit


@pytest.mark.parametrize("name", CONTAINER_COMPOSITIONS)
def test_tags_only_confines_the_candidate_chain_to_a_protected_tag(name: str):
    """Build, smoke test, SBOM, scan, signature and attestations, tag only.

    The verify jobs are deliberately untouched: a merge request and a
    default-branch push still run them, which is the difference between
    releasing on tags and verifying nothing until a tag exists.
    """
    jobs = container_jobs(
        name, **{"build-sources": "tags-only", "smoke-test": "true",
                 "vigil": "true", "vigil-url": "https://vigil.example.com"}
    )
    for job_name, job in jobs.items():
        conditions = composition.rule_conditions(job)
        if component_of(job_name) in VERIFY_JOBS:
            assert MERGE_REQUEST in conditions, job_name
            assert DEFAULT_BRANCH in conditions, job_name
        else:
            assert conditions == {PROTECTED_TAG}, (job_name, conditions)


@pytest.mark.parametrize("name", CONTAINER_COMPOSITIONS)
def test_the_two_source_sets_differ_in_nothing_but_rules(name: str):
    """The mode picks between two copies of each candidate include.

    A fixed `run-rules` array cannot be varied inside one include entry, so each
    candidate component is included twice. This is what keeps the pair from
    drifting: same job names, same bodies, different rules and nothing else.
    """
    extra = {"smoke-test": "true", "dependency-track": "true",
             "dependency-track-api-url": "https://dtrack.example.com/api",
             "vigil": "true", "vigil-url": "https://vigil.example.com"}
    default = container_jobs(name, **extra)
    tags_only = container_jobs(name, **extra, **{"build-sources": "tags-only"})
    assert sorted(default) == sorted(tags_only)
    for job_name, job in default.items():
        mine = {k: v for k, v in job.items() if k != "rules"}
        theirs = {k: v for k, v in tags_only[job_name].items() if k != "rules"}
        assert mine == theirs, f"{name}: {job_name} differs outside its rules"


@needs_token
@pytest.mark.parametrize("name", CONTAINER_COMPOSITIONS)
def test_a_default_branch_push_builds_only_when_the_sources_say_so(name: str):
    """The server's own verdict, per source set.

    `dry_run` asks GitLab to simulate a default-branch push and return the jobs
    it would create, so this is the rules engine deciding, not this file. The
    tag side cannot be simulated the same way — the Lint API has no tag context
    for an unpushed branch — and is asserted on the resolved rules above.
    """
    def created(mode: str) -> set[str]:
        config = composition.resolve(
            name, instance="api", **CONTAINER_INPUTS, **{"build-sources": mode}
        )
        result = lint_module.lint(
            composition.dump_yaml(config), include_jobs=True, dry_run=True
        )
        assert result["valid"], result.get("errors")
        return set(lint_module.job_names(result))

    builder = f"api:container-build-{name.split('-')[1]}"
    on_default = created("default-branch-and-tags")
    assert builder in on_default
    assert {"api:security-sbom-syft", "api:security-image-trivy",
            "api:container-sign-attest-cosign"} <= on_default

    tags_only = created("tags-only")
    assert tags_only == {f"api:{component}" for component in VERIFY_JOBS}, sorted(tags_only)


@pytest.mark.parametrize("mode", ["default-branch-and-tags", "tags-only"])
@pytest.mark.parametrize("name", CONTAINER_COMPOSITIONS)
def test_one_authoritative_sbom_producer_per_source_set(name: str, mode: str):
    """Section 6.2, on the resolved configuration rather than the file text."""
    jobs = container_jobs(name, **{"build-sources": mode})
    producers = [j for j in jobs if component_of(j) == "security-sbom-syft"]
    assert len(producers) == 1, producers
    builders = [j for j in jobs if component_of(j).startswith("container-build-")]
    assert len(builders) == 1, builders


# --------------------------------------------------------------------------
# terraform-deploy: the three gates a consuming project may need to keep
# --------------------------------------------------------------------------

TERRAFORM_INPUTS = {
    "working-directory": "infrastructure/network",
    "environment": "prod",
    "state-id": "network-prod",
}


def terraform_jobs(**extra) -> dict:
    return composition.jobs_only(
        composition.resolve(
            "terraform-deploy", instance="network", **TERRAFORM_INPUTS, **extra
        )
    )


def test_terraform_deploy_defaults_are_the_pipeline_that_was_there_before():
    """None of the three new inputs changes anything until it is set."""
    jobs = terraform_jobs()
    assert sorted(jobs) == [
        "network:security-filesystem-trivy",
        "network:terraform-apply",
        "network:terraform-fmt",
        "network:terraform-plan",
        "network:terraform-validate",
    ]
    for name, job in jobs.items():
        for rule in job["rules"]:
            assert "changes" not in rule, f"{name} filters on paths by default"
    apply = jobs["network:terraform-apply"]
    assert apply["rules"] == [{"if": DEFAULT_BRANCH, "when": "manual"}]
    assert composition.needs_of(apply) == ["network:terraform-plan"]


def test_the_path_filter_reaches_every_job_and_leaves_the_sources_alone():
    """Section 11.2: the filter narrows when a job runs, never which refs it runs on."""
    paths = ["infrastructure/network/**/*", "modules/**/*"]
    plain = terraform_jobs()
    filtered = terraform_jobs(**{"changes-filter": "on", "changes-paths": paths})
    assert sorted(plain) == sorted(filtered)
    for name, job in filtered.items():
        assert job["rules"], name
        for rule in job["rules"]:
            assert rule["changes"] == paths, name
        assert composition.rule_conditions(job) == composition.rule_conditions(plain[name])


def test_turning_the_filter_on_without_paths_is_not_a_pipeline_that_matches_nothing():
    """An empty `changes:` list matches nothing, so it is not the default.

    GitLab evaluates `changes:` against the diff, and an empty path list matches
    no file. A default of [] would mean that turning the filter on deleted every
    job; the default matches any change instead, which is a filter that filters
    nothing rather than a pipeline that runs nothing.
    """
    declared, _ = composition.load("terraform-deploy")
    assert declared["changes-paths"]["default"] == ["**/*"]
    assert declared["changes-filter"]["default"] == "off"
    for job in terraform_jobs(**{"changes-filter": "on"}).values():
        assert all(rule["changes"] == ["**/*"] for rule in job["rules"])


def test_the_apply_waits_for_a_person_unless_the_owner_says_otherwise():
    """Section 6.1: a production mutation does not default to unconditional execution."""
    declared, _ = composition.load("terraform-deploy")
    assert declared["apply-mode"]["default"] == "manual"
    assert declared["apply-mode"]["options"] == ["manual", "automatic"]

    manual = terraform_jobs()["network:terraform-apply"]
    automatic = terraform_jobs(**{"apply-mode": "automatic"})["network:terraform-apply"]
    assert manual["rules"] == [{"if": DEFAULT_BRANCH, "when": "manual"}]
    assert automatic["rules"] == [{"if": DEFAULT_BRANCH, "when": "on_success"}]
    # Automatic changes when a person is involved, not which ref may deploy.
    assert composition.rule_conditions(automatic) == {DEFAULT_BRANCH}
    assert composition.needs_of(automatic) == ["network:terraform-plan"]


def test_an_apply_gate_never_replaces_the_plan_producer():
    """Section 8: the gate list is spliced after the producer, not instead of it."""
    gate = {"job": "roles-dev:terraform-apply", "artifacts": False}
    apply = terraform_jobs(**{"apply-gate-jobs": [gate]})["network:terraform-apply"]
    assert apply["needs"] == [
        {"job": "network:terraform-plan", "artifacts": True},
        gate,
    ]


def test_the_two_root_example_orders_prod_behind_dev():
    """The ordering the stage graph cannot express, read off the example itself."""
    config = example_config(EXAMPLES / "terraform-deploy-two-roots" / ".gitlab-ci.yml")
    jobs = composition.jobs_only(config)
    prod = jobs["roles-prod:terraform-apply"]
    assert prod["needs"] == [
        {"job": "roles-prod:terraform-plan", "artifacts": True},
        {"job": "roles-dev:terraform-apply", "artifacts": False},
    ]
    assert prod["rules"][0]["when"] == "on_success"
    assert prod["rules"][0]["changes"] == ["roles/**/*"]
    # Both applies are in one stage, which is why the edge has to be declared.
    assert prod["stage"] == jobs["roles-dev:terraform-apply"]["stage"] == "deploy"


@needs_token
def test_the_two_root_example_compiles_with_its_cross_instance_edge():
    """The server's verdict on the configuration that carries that edge.

    The Lint API returns no `needs` per job, so the edge itself is asserted
    above, on the resolved configuration. What this adds is GitLab accepting it:
    an edge naming a job that does not exist is rejected, which
    test_a_missing_producer_fails_pipeline_creation proves separately.
    """
    config = example_config(EXAMPLES / "terraform-deploy-two-roots" / ".gitlab-ci.yml")
    result = lint_module.lint(
        composition.dump_yaml(config), include_jobs=True, dry_run=True
    )
    assert result["valid"], result.get("errors")
    applies = {
        job["name"]: job for job in result["jobs"] if job["name"].endswith("terraform-apply")
    }
    assert sorted(applies) == ["roles-dev:terraform-apply", "roles-prod:terraform-apply"]
    for name, job in applies.items():
        assert job["when"] == "on_success", name
        assert job["allow_failure"] is False, name
