"""BuildKit build secrets: the pair format, and the argv the template builds.

C4 of the runner-images design: an entitlement certificate cannot travel as a
build argument, because `--opt build-arg:` is recorded in the image history and
the certificate then ships inside the image. `--secret` is mounted for one RUN
step and written into no layer, so the component takes NAME=VARIABLE pairs and
resolves the variable itself.

The second half of this file runs the template's OWN buildctl script entry
against a stub `buildctl-daemonless.sh`, so what is asserted is the argv the
component really builds rather than a copy of it that could drift.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "runtime" / "registry" / "image-json.sh"
TEMPLATE = REPO_ROOT / "templates" / "container-build-buildkit" / "template.yml"
JOB_NAME = "$[[ inputs.instance ]]:container-build-buildkit"
# What a leaked certificate would look like in the trace or in the argv.
SECRET_TEXT = "-----BEGIN CERTIFICATE-----\nnot-a-real-certificate\n-----END CERTIFICATE-----"


def run(script: str, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "CI_COMMIT_SHA": "0" * 39 + "1",
        "CI_PIPELINE_ID": "4242",
        "CI_JOB_ID": "9001",
    }
    full_env.update(env or {})
    return subprocess.run(
        ["/bin/sh", "-c", f". '{HELPER}'\n{script}"],
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
    )


# --- the pair format ---------------------------------------------------------


@pytest.mark.parametrize(
    "pair",
    [
        "no-equals-sign",
        "=ENTITLEMENT",
        "1BAD=ENTITLEMENT",
        "cert=lowercase_variable",
        "cert=-----BEGIN CERTIFICATE-----",
        "cert=/etc/pki/entitlement/key.pem",
    ],
)
def test_a_malformed_pair_is_refused_and_the_message_names_it(tmp_path, pair):
    result = run(f"ci_tpl_require_build_secret '{pair}'", tmp_path)
    assert result.returncode != 0
    assert pair in result.stderr
    assert "NAME=VARIABLE" in result.stderr


@pytest.mark.parametrize("pair", ["cert=ENTITLEMENT_PEM", "npmrc=NPM_AUTH_FILE", "a.b-c=X"])
def test_a_well_formed_pair_is_accepted(tmp_path, pair):
    assert run(f"ci_tpl_require_build_secret '{pair}'", tmp_path).returncode == 0


def test_a_variable_that_is_not_set_fails_naming_the_variable(tmp_path):
    result = run("ci_tpl_build_secret_argument 'cert=ENTITLEMENT_PEM'", tmp_path)
    assert result.returncode != 0
    assert "ENTITLEMENT_PEM" in result.stderr
    assert "empty or not defined" in result.stderr


def test_an_ordinary_variable_holding_the_pem_is_refused(tmp_path):
    """A file-type CI variable's value is a path. An ordinary one holds the PEM
    itself, and passing that to buildctl as `src=` would put the certificate in
    the argv and so in the job trace."""
    result = run(
        "ci_tpl_build_secret_argument 'cert=ENTITLEMENT_PEM'",
        tmp_path,
        env={"ENTITLEMENT_PEM": SECRET_TEXT},
    )
    assert result.returncode != 0
    assert "not a file-type CI variable" in result.stderr
    assert "BEGIN CERTIFICATE" not in result.stderr + result.stdout


def test_a_file_variable_resolves_to_a_src_path_and_never_the_contents(tmp_path):
    pem = tmp_path / "entitlement.pem"
    pem.write_text(SECRET_TEXT)
    result = run(
        "ci_tpl_build_secret_argument 'cert=ENTITLEMENT_PEM'",
        tmp_path,
        env={"ENTITLEMENT_PEM": str(pem)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"id=cert,src={pem}"
    assert "BEGIN CERTIFICATE" not in result.stdout


# --- the argv the template builds --------------------------------------------


def buildctl_entry() -> str:
    """The template's own script entry that invokes buildctl."""
    _, jobs = list(yaml.safe_load_all(TEMPLATE.read_text()))
    entries = [e for e in jobs[JOB_NAME]["script"] if "buildctl-daemonless.sh build" in e]
    assert len(entries) == 1
    return entries[0]


def build_argv(tmp_path: Path, *, build_args: str, build_secrets: str, env: dict) -> list[str]:
    """Run that entry with buildctl stubbed, and return the argv it was given."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    argv_file = tmp_path / "argv"
    stub = stub_dir / "buildctl-daemonless.sh"
    stub.write_text(f'#!/bin/sh\nfor a in "$@"; do printf \'%s\\n\' "$a"; done > "{argv_file}"\n')
    stub.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    full_env = {
        "PATH": f"{stub_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "CI_TPL_WORK": str(work),
        "CI_TPL_ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "CI_TPL_CONTEXT": ".",
        "CI_TPL_DOCKERFILE": "Dockerfile",
        "CI_TPL_PLATFORMS": "linux/amd64",
        "CI_TPL_PUSH": "false",
        "CI_TPL_BUILD_ARGS": build_args,
        "CI_TPL_BUILD_SECRETS": build_secrets,
    }
    full_env.update(env)
    (tmp_path / "artifacts").mkdir()
    result = subprocess.run(
        ["/bin/sh", "-c", f". '{HELPER}'\nnames=x\n{buildctl_entry()}"],
        cwd=tmp_path,
        env=full_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return argv_file.read_text().splitlines()


def test_the_argv_carries_the_secret_as_a_mount_and_never_as_a_build_arg(tmp_path):
    pem = tmp_path / "entitlement.pem"
    pem.write_text(SECRET_TEXT)
    argv = build_argv(
        tmp_path,
        build_args='json ["BASE_REF=registry.example/base:1"]',
        build_secrets='json ["cert=ENTITLEMENT_PEM"]',
        env={"ENTITLEMENT_PEM": str(pem)},
    )
    assert "--secret" in argv
    assert f"id=cert,src={pem}" in argv
    assert "build-arg:BASE_REF=registry.example/base:1" in argv
    joined = "\n".join(argv)
    assert "BEGIN CERTIFICATE" not in joined
    assert "build-arg:cert" not in joined
    assert not any(a.startswith("build-arg:") and "ENTITLEMENT" in a for a in argv)


def test_no_secrets_means_no_secret_flag(tmp_path):
    argv = build_argv(
        tmp_path,
        build_args="json []",
        build_secrets="json []",
        env={},
    )
    assert "--secret" not in argv


def test_a_malformed_pair_stops_the_build(tmp_path):
    """The loop exits before buildctl runs, so a typo never becomes a build that
    silently lacks its secret."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "buildctl-daemonless.sh"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    result = subprocess.run(
        ["/bin/sh", "-c", f". '{HELPER}'\nnames=x\n{buildctl_entry()}"],
        cwd=tmp_path,
        env={
            "PATH": f"{stub_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
            "CI_TPL_WORK": str(work),
            "CI_TPL_ARTIFACT_DIR": str(tmp_path),
            "CI_TPL_CONTEXT": ".",
            "CI_TPL_DOCKERFILE": "Dockerfile",
            "CI_TPL_PLATFORMS": "linux/amd64",
            "CI_TPL_PUSH": "false",
            "CI_TPL_BUILD_ARGS": "json []",
            "CI_TPL_BUILD_SECRETS": 'json ["cert=/etc/pki/entitlement/key.pem"]',
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "cert=/etc/pki/entitlement/key.pem" in result.stderr


def test_the_template_never_routes_a_secret_through_a_build_argument():
    """The whole point of C4. A future edit that reads CI_TPL_BUILD_SECRETS into
    the build-arg loop fails here.

    The region is the secrets loop itself, from the variable to the `done` that
    closes it, and not everything up to buildctl: the job legitimately builds
    other build arguments in between."""
    _, jobs = list(yaml.safe_load_all(TEMPLATE.read_text()))
    script = "\n".join(jobs[JOB_NAME]["script"])
    start = script.index("CI_TPL_BUILD_SECRETS")
    end = script.index('done < "$CI_TPL_WORK/build-secrets"', start)
    secrets_loop = script[start:end]
    assert "build-arg" not in secrets_loop
    assert "--secret" in secrets_loop
    assert "--secret" in script
