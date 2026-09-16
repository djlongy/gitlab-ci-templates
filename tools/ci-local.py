#!/usr/bin/env python3
"""Run one component job's script locally, in the image the component pins.

WHY THIS EXISTS
---------------
The gate between "the YAML compiles" and "the job works" is a real pipeline on
a real runner, and that loop is minutes long. This closes it to seconds: it
resolves a component or composition the way `tests/pipelines/` already does,
assembles the environment a runner would set, and runs the job's own script in
the job's own image.

It resolves nothing GitLab resolves. It calls `tools/resolve/`, the same
package `tests/pipelines/` renders with before posting to the CI Lint API. The
substitution rules there are pinned against the live API by the probes in
`tests/pipelines/test_container_components.py`, so a local render is the
harness's own answer rather than a second opinion. A second implementation of
input interpolation would be a second thing to keep true.

WHY NOT THE CI LINT API FOR THE RESOLUTION
------------------------------------------
Because it cannot answer the question. Measured against a GitLab 18.9.1-ee
server on 2026/09/16, `POST /ci/lint` with `include_jobs: true` returns each
job's `name`, `stage`, `before_script`, `script`, `after_script`, `tag_list`,
`environment`, `when` and `allow_failure` -- and, without `dry_run`, `needs`,
`only` and `except`. It returns neither `image:` nor `variables:`, in either
mode. A runner needs both, so the local resolver is the source of the job and
the Lint API is a cross-check on the merged configuration (`--lint`), not a
substitute for it.

WHAT A GREEN RUN HERE PROVES, AND WHAT IT DOES NOT
--------------------------------------------------
Proves: the script is valid shell for that image's userland, the embedded
runtime writes the evidence it claims, the gate's exit code is what the
contract says, and the artifact paths are produced.

Does not prove: `services:`, `id_tokens:` and their JWTs, masked or protected
CI variables, `needs:`/`artifacts:` passing between jobs as the runner does it,
`rules:` evaluation, cache restore, or the runner's build-directory ownership
behaviour. A component still needs one real consumer pipeline run before it can
be called `released`. See docs/howto/local-testing.md.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.resolve import composition, render_component  # noqa: E402

# What a runner sets and a component job reads. Every predefined name any
# template in templates/ references is here; the values are local stand-ins, so
# a job that depends on one failing over to a real server value fails here
# loudly rather than silently reading an empty string.
#
# Regenerate the list with:
#   grep -rhoE '\$\{?CI_[A-Z0-9_]+' templates/*/template.yml | tr -d '${' \
#     | sort -u | grep -v '^CI_TPL'
PREDEFINED: dict[str, str] = {
    "CI": "true",
    "GITLAB_CI": "true",
    "CI_API_V4_URL": "https://gitlab.example.com/api/v4",
    "CI_AUTHOR_EMAIL": "ci-local@example.invalid",
    "CI_AUTHOR_NAME": "ci-local",
    "CI_COMMIT_BRANCH": "main",
    "CI_COMMIT_REF_NAME": "main",
    "CI_COMMIT_REF_PROTECTED": "false",
    "CI_COMMIT_SHA": "0" * 40,
    "CI_COMMIT_SHORT_SHA": "0" * 8,
    "CI_DEFAULT_BRANCH": "main",
    "CI_JOB_ID": "0",
    "CI_JOB_NAME": "",  # filled in from the selected job
    "CI_JOB_TOKEN": "",
    "CI_PIPELINE_ID": "0",
    "CI_PIPELINE_SOURCE": "push",
    "CI_PROJECT_DIR": "",  # filled in from the mount point
    "CI_PROJECT_ID": "0",
    "CI_PROJECT_NAME": "",  # filled in from --project-path
    "CI_PROJECT_NAMESPACE": "",
    "CI_PROJECT_PATH": "local/demo",
    "CI_SERVER_FQDN": "gitlab.example.com",
    "CI_SERVER_HOST": "gitlab.example.com",
    "CI_SERVER_URL": "https://gitlab.example.com",
}

# CI_COMMIT_TAG is deliberately absent: a runner leaves it unset on a branch
# pipeline, and a script testing `${CI_COMMIT_TAG:-}` must see unset, not empty.

# Top-level keys that configure the pipeline rather than declare a job.
# https://docs.gitlab.com/ci/yaml/#keywords
RESERVED_KEYWORDS = frozenset(
    {"default", "include", "stages", "variables", "workflow", "spec", "image",
     "services", "before_script", "after_script", "cache", "pages"}
)


class UsageError(RuntimeError):
    """The invocation cannot be resolved into a job to run."""


class Resolved(NamedTuple):
    """Jobs, and the stage order the source declared for them.

    The order is not decoration. GitLab resolves `needs:` against stage order,
    so sorting the stage list alphabetically puts `publish` before `verify` and
    a consumer's own configuration comes back rejected with "need ... is not
    defined in current or prior stages" -- a graph that is fine, reported as
    broken. `stages` is empty when the source declared none, and then first-seen
    order is used, which follows include order and so follows the graph.
    """

    jobs: dict
    stages: list


def _resolve_local_includes(body: dict, values: dict) -> Resolved:
    """Jobs, and stage order, from the `include:` list of a consumer body.

    A consumer of this library declares no `stages:` of its own -- the
    composition owns them (section 6.2) -- so the stage order arrives through
    the include or not at all. Losing it here is what made a valid
    container-mirror consumer lint as a broken graph.

    `local:` is resolved against this checkout. `project:` is resolved against
    this checkout too when it names this repository, which is the case worth
    testing: a consumer pinned to a released ref, run against the working tree
    to see what the next release would do to it. A `project:` include of any
    other repository is refused rather than guessed at.
    """
    jobs: dict[str, Any] = {}
    stages: list[str] = []
    for entry in body.get("include", []) or []:
        if not isinstance(entry, dict):
            raise UsageError(f"unsupported include entry: {entry!r}")
        if "rules" in entry and not composition._rule_is_active(entry["rules"], values):
            continue
        if "local" in entry:
            relative = entry["local"]
        elif "project" in entry:
            if entry["project"] not in ("platform/gitlab-ci-templates",):
                raise UsageError(
                    f"include of {entry['project']!r} cannot be resolved locally; "
                    "only this repository's own files are on disk"
                )
            relative = entry["file"]
        else:
            raise UsageError(f"unsupported include entry: {entry!r}")

        path = REPO_ROOT / relative.lstrip("/")
        inputs = render_component.substitute(entry.get("inputs", {}) or {}, values)
        if path.parent.parent.name == "pipelines" or path.parent == REPO_ROOT / "pipelines":
            produced = composition.resolve(path.stem, **inputs)
            for stage in produced.get("stages") or []:
                if stage not in stages:
                    stages.append(stage)
            produced = {k: v for k, v in produced.items() if k not in ("workflow", "stages")}
        else:
            produced = render_component.render(path, **inputs)
        clash = set(produced) & set(jobs)
        if clash:
            raise UsageError(f"two includes emit the same job name: {sorted(clash)}")
        jobs.update(produced)
    return Resolved(jobs, stages)


def resolve_config(path: Path, overrides: dict[str, str]) -> dict:
    """Jobs a consumer `.gitlab-ci.yml` would create, resolved locally."""
    documents = list(yaml.load_all(path.read_text(), Loader=composition.Loader))
    if len(documents) == 2:
        spec, body = documents
        declared = (spec or {}).get("spec", {}).get("inputs", {}) or {}
        values = render_component.resolve(declared, overrides)
    elif len(documents) == 1:
        body, values = documents[0], {}
        if overrides:
            raise UsageError(f"{path} declares no spec:inputs; --input has nothing to set")
    else:
        raise UsageError(f"{path} has {len(documents)} YAML documents; expected 1 or 2")

    # A top-level GitLab keyword is a mapping too, so a name check is not enough
    # to tell `default:` or `variables:` from a job that happens to be called
    # that. The reserved list is what separates them.
    jobs = {
        name: value
        for name, value in body.items()
        if isinstance(value, dict) and name not in RESERVED_KEYWORDS
    }
    included = _resolve_local_includes(body, values)
    jobs.update(included.jobs)
    # The consumer's own `stages:` wins when it has one; otherwise the order
    # comes from the composition it includes.
    return Resolved(jobs, list(body.get("stages") or []) or included.stages)


FIXTURES_DIR = REPO_ROOT / "tests" / "pipelines" / "fixtures"
FIXTURE_SUFFIX = ".consumer.gitlab-ci.yml"


def fixture_path(name: str) -> Path:
    """The consumer fixture `name`, the same file the CI Lint harness sends.

    One fixture set, two drivers: what tests/pipelines/ posts to the server and
    what this runs locally are the same bytes, so the local loop and the gate
    cannot drift into disagreeing about a consumer's shape.

    Only `*.consumer.gitlab-ci.yml` qualifies. The other files under fixtures/
    are data for a test to read -- security-image-components.yml is a component
    matrix, not a pipeline -- and pretending to run one would be a category
    error, not a resolver bug.
    """
    path = FIXTURES_DIR / f"{name}{FIXTURE_SUFFIX}"
    if not path.is_file():
        available = sorted(p.name[: -len(FIXTURE_SUFFIX)] for p in consumer_fixtures())
        raise UsageError(f"no fixture {name!r}; available: {', '.join(available)}")
    return path


def consumer_fixtures() -> list[Path]:
    return sorted(FIXTURES_DIR.glob(f"*{FIXTURE_SUFFIX}"))


def resolve_jobs(args: argparse.Namespace) -> dict:
    overrides = dict(pair.split("=", 1) for pair in args.input)
    if args.fixture:
        return resolve_config(fixture_path(args.fixture), overrides)
    if args.template:
        path = REPO_ROOT / "templates" / args.template / "template.yml"
        if not path.is_file():
            raise UsageError(f"no such component: templates/{args.template}/template.yml")
        return Resolved(render_component.render(path, **overrides), [])
    if args.composition:
        produced = composition.resolve(args.composition, **overrides)
        stages = list(produced.get("stages") or [])
        jobs = {k: v for k, v in produced.items() if k not in ("workflow", "stages")}
        return Resolved(jobs, stages)
    return resolve_config(Path(args.config), overrides)


def script_of(job: dict) -> str:
    """The job's shell, as one POSIX script.

    `before_script` and `script` run under `set -e` in a subshell; GitLab treats
    them as one failing unit. `after_script` runs afterwards whatever happened
    and cannot change the outcome, which is what the runner does, so a cleanup
    step that fails here does not turn a failed gate green.
    """
    def lines(key: str) -> list[str]:
        value = job.get(key) or []
        return [value] if isinstance(value, str) else list(value)

    body = ["(", "set -e"] + lines("before_script") + lines("script") + [")", "status=$?"]
    after = lines("after_script")
    if after:
        body += ["set +e"] + after
    return "\n".join(body + ["exit $status", ""])


def merged_config(jobs: dict, stages: list | None = None) -> str:
    """The resolved jobs as one configuration document, for the CI Lint API.

    Every job goes in, hidden ones included. A component that selects between
    two shapes -- docs-wiki-sync, for the docker and shell executors -- carries
    hidden parents that its public job `extends:`. Sending only the public jobs
    makes the API reject a configuration that is perfectly valid, with
    "unknown keys in `extends`".
    """
    # Only the real jobs declare a stage. A hidden parent may carry one the
    # pipeline never uses, and an undeclared stage fails the lint.
    used = []
    for name, job in jobs.items():
        if name.startswith("."):
            continue
        stage = job.get("stage", "test")
        if stage not in used:
            used.append(stage)
    # The declared list wins, narrowed to what these jobs use; otherwise
    # first-seen order. Never sorted: see Resolved.
    order = [s for s in (stages or [])if s in used] or used
    return composition.dump_yaml({"stages": order, **jobs})


def image_of(job: dict) -> str:
    image = job.get("image")
    if isinstance(image, dict):
        image = image.get("name")
    if not image:
        raise UsageError(
            "this job declares no image, so there is nothing to run it in. A "
            "shell-executor component (docs-wiki-sync with executor: shell) is "
            "tested by its runtime tests, not here."
        )
    return image


def environment(job: dict, args: argparse.Namespace, job_name: str, project_dir: str) -> dict[str, str]:
    """The variables the job sees, lowest precedence first.

    Predefined stand-ins, then the job's own `variables:`, then the developer's
    env file. The env file wins so a real token or registry credential can be
    supplied for a job that needs one without editing anything else.
    """
    env = dict(PREDEFINED)
    env["CI_PROJECT_DIR"] = project_dir
    env["CI_JOB_NAME"] = job_name
    env["CI_PROJECT_PATH"] = args.project_path
    namespace, _, name = args.project_path.rpartition("/")
    env["CI_PROJECT_NAMESPACE"], env["CI_PROJECT_NAME"] = namespace, name

    for key, value in (job.get("variables") or {}).items():
        env[str(key)] = "" if value is None else str(value)

    if args.env_file:
        for raw in Path(args.env_file).read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def run(job: dict, job_name: str, args: argparse.Namespace) -> int:
    checkout = Path(args.checkout).resolve()
    if not checkout.is_dir():
        raise UsageError(f"--checkout {checkout} is not a directory")
    # Colima's default VM only shares paths under $HOME. A checkout outside it
    # mounts as an empty directory and the job fails claiming the source is
    # missing, which reads as a bug in the component.
    if not str(checkout).startswith(str(Path.home())):
        print(
            f"ci-local: warning: {checkout} is outside $HOME; on Colima it will "
            "mount empty. Copy the checkout under $HOME first.",
            file=sys.stderr,
        )

    project_dir = f"/builds/{args.project_path}"
    env = environment(job, args, job_name, project_dir)

    if args.artifacts_from:
        source = Path(args.artifacts_from).resolve()
        if not source.is_dir():
            raise UsageError(f"--artifacts-from {source} is not a directory")
        shutil.copytree(source, checkout / ".ci-artifacts", dirs_exist_ok=True)
        print(f"ci-local: seeded {checkout / '.ci-artifacts'} from {source}")

    # The script goes in over stdin rather than a bind mount. A mount of a path
    # the Colima VM does not share -- which every system temporary directory on
    # macOS is, they live under /var/folders -- silently becomes an empty
    # DIRECTORY inside the container, and `sh` on an empty directory exits 0.
    # Measured 2026/09/16: a job that ran nothing, wrote nothing and reported
    # success. `--env-file` is a client-side read and is not affected.
    with tempfile.TemporaryDirectory() as scratch:
        env_file = Path(scratch) / "job.env"
        env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
        command = [
            "docker", "run", "--rm", "-i",
            "--entrypoint", "sh",
            "-v", f"{checkout}:{project_dir}",
            "-w", project_dir,
            "--env-file", str(env_file),
            image_of(job),
            "-s",
        ]
        print(f"ci-local: {job_name} in {image_of(job)}", flush=True)
        status = subprocess.run(
            command, input=script_of(job).encode(), check=False
        ).returncode

    print(f"ci-local: {job_name} exited {status}")
    artifacts = sorted(p for p in (checkout / ".ci-artifacts").rglob("*") if p.is_file())
    for path in artifacts:
        print(f"ci-local: artifact {path.relative_to(checkout)}")
    if not artifacts:
        print("ci-local: no artifacts under .ci-artifacts/")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--template", help="component under templates/")
    source.add_argument("--composition", help="composition under pipelines/, without .yml")
    source.add_argument("--config", help="a consumer .gitlab-ci.yml")
    source.add_argument(
        "--fixture",
        help="a consumer fixture under tests/pipelines/fixtures/, by name: the "
             "same file the CI Lint harness sends to the server",
    )
    parser.add_argument("--input", action="append", default=[], metavar="KEY=VALUE",
                        help="an input for the component or composition; repeatable")
    parser.add_argument("--job", help="which job to show or run")
    parser.add_argument("--checkout", default=".", help="the checkout to mount (default: cwd)")
    parser.add_argument("--env-file", help="KEY=VALUE lines, highest precedence")
    parser.add_argument("--project-path", default="local/demo",
                        help="CI_PROJECT_PATH, and the build directory inside the container")
    parser.add_argument("--artifacts-from", metavar="DIR",
                        help="a previous run's .ci-artifacts, for a job with a producer")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="list the jobs this config creates")
    mode.add_argument("--show", action="store_true", help="print the resolved script, run nothing")
    mode.add_argument("--lint", action="store_true",
                      help="cross-check the merged configuration through the CI Lint API")
    args = parser.parse_args(argv)

    try:
        jobs, declared_stages = resolve_jobs(args)
    except (UsageError, ValueError, composition.CompositionError) as error:
        print(f"ci-local: {error}", file=sys.stderr)
        return 2

    public = {name: job for name, job in jobs.items() if not name.startswith(".")}

    if args.list:
        for name in public:
            print(name)
        return 0

    if args.lint:
        # lint.py is the CI Lint harness and stays with the tests that are its
        # main caller; only this branch needs it, and only this branch needs a
        # token. Imported late so every other mode works without either.
        sys.path.insert(0, str(REPO_ROOT / "tests" / "pipelines"))
        from lint import LintError, lint  # noqa: PLC0415

        try:
            result = lint(merged_config(jobs, declared_stages))
        except LintError as error:
            print(f"ci-local: {error}", file=sys.stderr)
            return 2
        if not result.get("valid"):
            for message in result.get("errors", []):
                print(f"ci-local: lint error: {message}", file=sys.stderr)
            return 1
        print(f"ci-local: linted, {len(result.get('jobs', []))} job(s)")
        return 0

    if not args.job:
        print(
            "ci-local: --job is required to show or run. Jobs in this config:\n  "
            + "\n  ".join(public),
            file=sys.stderr,
        )
        return 2
    if args.job not in public:
        print(
            f"ci-local: no job {args.job!r}. Jobs in this config:\n  " + "\n  ".join(public),
            file=sys.stderr,
        )
        return 2

    if args.show:
        print(script_of(public[args.job]))
        return 0

    try:
        return run(public[args.job], args.job, args)
    except UsageError as error:
        print(f"ci-local: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
