#!/usr/bin/env python3
"""Cover the parts of tools/ci-local.py that decide what the container runs.

No docker here. What is worth pinning is the resolution and the environment
assembly: the script the job would execute, the image it would execute in, and
the variables it would see. Whether alpine can run `sh` is not in doubt.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "ci_local", REPO_ROOT / "tools" / "ci-local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_local = _load()


class Args:
    """The attributes `environment()` and `run()` read off the parsed args."""

    def __init__(self, **kwargs):
        self.project_path = "local/demo"
        self.env_file = None
        self.artifacts_from = None
        self.checkout = "."
        self.input = []
        self.template = self.composition = self.config = self.fixture = None
        self.__dict__.update(kwargs)


# --- script assembly -------------------------------------------------------


def test_before_and_script_run_under_set_e_together():
    script = ci_local.script_of(
        {"before_script": ["setup"], "script": ["work"]}
    )
    assert script.splitlines()[:4] == ["(", "set -e", "setup", "work"]


def test_after_script_cannot_turn_a_failed_gate_green():
    """The runner's semantics: after_script runs, the job's status is the script's.

    A cleanup step returning 0 after a failed scan must not mask the failure,
    which is what a single flat script under `set -e` would do.
    """
    script = ci_local.script_of(
        {"script": ["scan"], "after_script": ["cleanup"]}
    )
    # The whole script, not its suffix: `cleanup` appearing a second time inside
    # the `set -e` block would still end correctly and still mask the failure.
    assert script == "(\nset -e\nscan\n)\nstatus=$?\nset +e\ncleanup\nexit $status\n"


def test_a_job_without_after_script_still_exits_on_the_script_status():
    assert ci_local.script_of({"script": ["work"]}).endswith(
        ")\nstatus=$?\nexit $status\n"
    )


def test_a_string_script_is_accepted_like_a_list():
    assert "work" in ci_local.script_of({"script": "work"})


# --- image selection -------------------------------------------------------


def test_image_comes_out_of_the_mapping_form_components_use():
    assert ci_local.image_of({"image": {"name": "alpine@sha256:0", "entrypoint": [""]}}) == (
        "alpine@sha256:0"
    )


def test_image_comes_out_of_the_string_form():
    assert ci_local.image_of({"image": "alpine:3.20"}) == "alpine:3.20"


def test_a_job_with_no_image_is_refused_rather_than_guessed_at():
    """A shell-executor job has no image; running it in some default would be a lie."""
    with pytest.raises(ci_local.UsageError, match="declares no image"):
        ci_local.image_of({"script": ["work"]})


# --- environment assembly --------------------------------------------------


def test_job_variables_override_the_predefined_stand_ins():
    env = ci_local.environment(
        {"variables": {"CI_PIPELINE_SOURCE": "merge_request_event"}},
        Args(),
        "demo:job",
        "/builds/local/demo",
    )
    assert env["CI_PIPELINE_SOURCE"] == "merge_request_event"


def test_the_env_file_wins_over_the_job_and_the_predefined_values(tmp_path: Path):
    """So a real token can be supplied without editing the component."""
    env_file = tmp_path / "vars.env"
    env_file.write_text("# a comment\n\nCI_TPL_REGISTRY_PASSWORD=secret\nCI_JOB_TOKEN=tok\n")
    env = ci_local.environment(
        {"variables": {"CI_TPL_REGISTRY_PASSWORD": "from-the-job"}},
        Args(env_file=str(env_file)),
        "demo:job",
        "/builds/local/demo",
    )
    assert env["CI_TPL_REGISTRY_PASSWORD"] == "secret"
    assert env["CI_JOB_TOKEN"] == "tok"


def test_project_path_drives_namespace_name_and_the_build_directory():
    env = ci_local.environment(
        {}, Args(project_path="platform/infrastructure"), "j", "/builds/platform/infrastructure"
    )
    assert env["CI_PROJECT_PATH"] == "platform/infrastructure"
    assert env["CI_PROJECT_NAMESPACE"] == "platform"
    assert env["CI_PROJECT_NAME"] == "infrastructure"
    assert env["CI_PROJECT_DIR"] == "/builds/platform/infrastructure"


def test_commit_tag_is_unset_not_empty():
    """`${CI_COMMIT_TAG:-}` on a branch pipeline must see unset.

    An empty string in the env file would make a tag-gated branch of a script
    take the same path as a real tag pipeline, which is the opposite of what a
    branch run proves.
    """
    assert "CI_COMMIT_TAG" not in ci_local.environment({}, Args(), "j", "/builds/local/demo")


def test_a_variable_set_to_nothing_becomes_an_empty_string_not_the_word_none():
    env = ci_local.environment({"variables": {"CI_TPL_EGRESS_PROXY": None}}, Args(), "j", "/d")
    assert env["CI_TPL_EGRESS_PROXY"] == ""


def test_every_predefined_name_the_templates_read_has_a_stand_in():
    """The list in PREDEFINED is generated from the templates; keep it that way.

    A template reading a predefined variable that is not here gets an empty
    string from the shell and usually carries on, so the run proves less than
    it appears to.
    """
    import re

    used = set()
    for template in (REPO_ROOT / "templates").glob("*/template.yml"):
        used |= set(re.findall(r"\$\{?(CI_[A-Z0-9_]+)", template.read_text()))
    predefined = {name for name in used if not name.startswith("CI_TPL")}
    missing = predefined - set(ci_local.PREDEFINED) - {"CI_COMMIT_TAG"}
    assert not missing, f"tools/ci-local.py has no stand-in for {sorted(missing)}"


# --- resolution ------------------------------------------------------------


def test_a_component_resolves_to_its_instance_named_job():
    jobs, _ = ci_local.resolve_jobs(
        Args(template="quality-dependency-lockfiles", input=["instance=demo", "lockfiles=none"])
    )
    assert "demo:quality-dependency-lockfiles" in jobs


def test_a_consumer_config_resolves_its_local_includes(tmp_path: Path):
    config = tmp_path / ".gitlab-ci.yml"
    config.write_text(
        "stages: [verify]\n"
        "include:\n"
        "  - local: '/templates/quality-dependency-lockfiles/template.yml'\n"
        "    inputs: {instance: demo, stage: verify, lockfiles: none}\n"
    )
    jobs, stages = ci_local.resolve_config(config, {})
    assert list(jobs) == ["demo:quality-dependency-lockfiles"]
    assert stages == ["verify"]
    assert jobs["demo:quality-dependency-lockfiles"]["stage"] == "verify"


def test_a_project_include_of_this_repository_resolves_against_the_working_tree(tmp_path: Path):
    """Which is the case worth running: a pinned consumer, against what is next."""
    config = tmp_path / ".gitlab-ci.yml"
    config.write_text(
        "stages: [verify]\n"
        "include:\n"
        "  - project: 'platform/gitlab-ci-templates'\n"
        "    ref: '1.0.0'\n"
        "    file: '/templates/quality-dependency-lockfiles/template.yml'\n"
        "    inputs: {instance: demo, stage: verify, lockfiles: none}\n"
    )
    assert "demo:quality-dependency-lockfiles" in ci_local.resolve_config(config, {}).jobs


def test_a_project_include_of_another_repository_is_refused(tmp_path: Path):
    """Nothing on disk can answer for it, and pretending otherwise would run the wrong code."""
    config = tmp_path / ".gitlab-ci.yml"
    config.write_text(
        "stages: [verify]\n"
        "include:\n"
        "  - project: 'other/templates'\n"
        "    ref: '1.0.0'\n"
        "    file: '/templates/whatever.yml'\n"
    )
    with pytest.raises(ci_local.UsageError, match="cannot be resolved locally"):
        ci_local.resolve_config(config, {})


def test_an_input_failing_its_declared_regex_is_refused():
    """The same check GitLab applies when it resolves the include."""
    with pytest.raises(ValueError, match="regex"):
        ci_local.resolve_jobs(
            Args(template="quality-dependency-lockfiles", input=["instance=Demo", "lockfiles=none"])
        )


def test_a_composition_resolves_to_the_jobs_it_wires():
    jobs, stages = ci_local.resolve_jobs(Args(composition="docs-wiki", input=["instance=docs"]))
    assert "docs:docs-wiki-sync" in jobs
    assert "stages" not in jobs and "workflow" not in jobs
    assert stages, "the composition's declared stage order must survive resolution"


def test_the_resolved_script_of_a_real_component_carries_its_embedded_runtime():
    """The end the runner depends on: what `--show` prints is what would run."""
    jobs, _ = ci_local.resolve_jobs(
        Args(template="quality-dependency-lockfiles", input=["instance=demo", "lockfiles=none"])
    )
    script = ci_local.script_of(jobs["demo:quality-dependency-lockfiles"])
    assert "CI_TPL_EMBED_EOF" in script
    assert "lockfiles.sh" in script


def test_the_lint_payload_keeps_the_hidden_parents_a_job_extends():
    """Sending only the public jobs makes the API reject a valid configuration.

    docs-wiki-sync selects between a docker and a shell shape with two hidden
    parents, and its public job `extends:` one of them. Filtering them out of
    the payload got "unknown keys in `extends`" from a config that is fine.
    """
    jobs, _ = ci_local.resolve_jobs(Args(composition="docs-wiki", input=["instance=docs"]))
    hidden = [name for name in jobs if name.startswith(".")]
    assert hidden, "this test needs a composition that has hidden parents"
    payload = ci_local.merged_config(jobs)
    for name in hidden:
        assert name in payload


def test_the_lint_payload_declares_the_stages_its_public_jobs_use():
    jobs, _ = ci_local.resolve_jobs(
        Args(template="quality-dependency-lockfiles", input=["instance=demo", "lockfiles=none"])
    )
    assert "stages:\n- verify\n" in ci_local.merged_config(jobs)


# --- one fixture set, two drivers ------------------------------------------


def test_every_consumer_fixture_resolves_through_the_local_runner():
    """The files the CI Lint harness posts are the files this runs.

    If a fixture only ever went through one of the two, the local loop and the
    gate could drift into disagreeing about a consumer's shape, and the drift
    would surface in the consumer's pipeline rather than here.
    """
    fixtures = ci_local.consumer_fixtures()
    assert fixtures, "tests/pipelines/fixtures/ has no *.consumer.gitlab-ci.yml"
    for path in fixtures:
        jobs, _ = ci_local.resolve_config(path, {})
        assert jobs, f"{path.name} resolved to no jobs"


def test_the_fixture_shortcut_names_the_same_file_the_lint_harness_uses():
    name = ci_local.consumer_fixtures()[0].name[: -len(ci_local.FIXTURE_SUFFIX)]
    assert ci_local.fixture_path(name) == ci_local.consumer_fixtures()[0]
    assert ci_local.resolve_jobs(Args(fixture=name)).jobs


def test_an_unknown_fixture_lists_the_ones_that_exist():
    with pytest.raises(ci_local.UsageError, match="available:"):
        ci_local.fixture_path("no-such-consumer")


def test_a_data_fixture_is_not_offered_as_a_pipeline():
    """security-image-components.yml is a component matrix a test reads, not a
    consumer pipeline. Offering it would invite running something that is not a
    pipeline at all."""
    names = [p.name for p in ci_local.consumer_fixtures()]
    assert "security-image-components.yml" not in names


# --- the quickstart's concrete claims -------------------------------------


QUICKSTART = REPO_ROOT / "docs" / "howto" / "new-admin-quickstart.md"


def _quickstart_job_names() -> list[str]:
    """The job names the quickstart tells a new admin to expect on an MR."""
    import re

    text = QUICKSTART.read_text()
    match = re.search(
        r"The MR pipeline runs \w+ jobs?: (.+?)\.\n", text, re.DOTALL
    )
    assert match, "the quickstart no longer states which jobs the MR pipeline runs"
    return re.findall(r"`([^`]+)`", match.group(1))


def test_the_quickstart_lists_the_jobs_the_pipeline_actually_creates():
    """A walkthrough that names the wrong jobs teaches the wrong thing.

    The list rots the moment a job is added or renamed, and nothing else here
    would notice: the pipeline would still be green and the doc still wrong.
    """
    import yaml

    config = yaml.safe_load((REPO_ROOT / ".gitlab-ci.yml").read_text())
    defined = {
        name
        for name, job in config.items()
        if isinstance(job, dict) and "script" in job and name != "release"
    }
    assert set(_quickstart_job_names()) == defined, (
        "docs/howto/new-admin-quickstart.md lists the MR pipeline's jobs; "
        ".gitlab-ci.yml now defines a different set"
    )


def test_the_quickstart_counts_the_jobs_it_lists():
    """The sentence gives a number and then the names; they must agree."""
    import re

    words = {"four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
    match = re.search(r"The MR pipeline runs (\w+) jobs?:", QUICKSTART.read_text())
    assert match, "the quickstart no longer states how many jobs the MR pipeline runs"
    stated = words.get(match.group(1), match.group(1))
    assert int(stated) == len(_quickstart_job_names())


def test_the_quickstart_does_not_hardcode_the_approved_ref():
    """`.ci/estate.yml` moves every release; a literal in prose goes stale silently."""
    import yaml

    approved = str(
        yaml.safe_load((REPO_ROOT / ".ci" / "estate.yml").read_text())["shared_ci"][
            "approved_ref"
        ]
    )
    prose = [
        line
        for line in QUICKSTART.read_text().splitlines()
        if approved in line and not line.lstrip().startswith(("ref:", "#"))
    ]
    assert not prose, (
        f"the quickstart states the approved ref {approved!r} in prose; name "
        "`.ci/estate.yml`'s approved_ref instead so it cannot go stale"
    )


def test_the_lint_payload_keeps_the_declared_stage_order():
    """Sorting the stage list breaks `needs:` and blames the consumer for it.

    container-mirror declares `verify` then `publish`, and its mirror job needs
    the list job in the earlier stage. Alphabetical order puts `publish` first,
    and the API rejects a valid configuration with "need
    lab:container-list-rke2 is not defined in current or prior stages".
    """
    jobs, stages = ci_local.resolve_config(ci_local.fixture_path("container-mirror"), {})
    assert stages[0] == "verify" and "publish" in stages
    payload = ci_local.merged_config(jobs, stages)
    assert payload.index("- verify") < payload.index("- publish")


def test_stage_order_falls_back_to_first_seen_not_alphabetical():
    """A source that declares no stages still must not be sorted."""
    jobs = {"a": {"stage": "verify"}, "b": {"stage": "publish"}}
    payload = ci_local.merged_config(jobs)
    assert payload.index("- verify") < payload.index("- publish")


def test_a_declared_stage_no_job_uses_is_left_out():
    """GitLab accepts an unused stage, but carrying one hides a wiring mistake."""
    jobs = {"a": {"stage": "verify"}}
    assert "deploy" not in ci_local.merged_config(jobs, ["verify", "deploy"])
