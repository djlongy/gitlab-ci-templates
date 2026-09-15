"""Behaviour tests for runtime/terraform/tfguard.sh.

Every failure path terraform-apply depends on is exercised here, because a
guard that cannot be shown to refuse is not a guard. The plan JSON fixtures were
produced by the pinned image (hashicorp/terraform 1.11.4) from real
`terraform plan` runs over `terraform_data` resources, not hand-written, so the
scanner is tested against terraform's actual serialisation.

External commands are stubbed with scripts on PATH rather than mocked, since
the subject is a shell script and its contract with terraform, wget and curl is
the thing under test.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TFGUARD = REPO_ROOT / "runtime" / "terraform" / "tfguard.sh"
FIXTURES = Path(__file__).parent / "fixtures"

TERRAFORM_VERSION = "1.11.4"

CI_ENV = {
    "CI_COMMIT_SHA": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4",
    "CI_PIPELINE_ID": "4242",
    "CI_JOB_ID": "9001",
}


@pytest.fixture
def stubs(tmp_path):
    """A PATH directory the tests fill with stand-ins for external commands."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()

    def add(name: str, body: str) -> None:
        script = stub_dir / name
        script.write_text("#!/bin/sh\n" + body)
        script.chmod(0o755)

    add("terraform", f'echo "Terraform v{TERRAFORM_VERSION}"\n')
    add.dir = stub_dir  # type: ignore[attr-defined]
    return add


def run(stubs, *args, env=None, cwd=None):
    environment = dict(os.environ)
    environment["PATH"] = f"{stubs.dir}:{environment['PATH']}"
    environment.update(CI_ENV)
    environment.update(env or {})
    return subprocess.run(
        ["sh", str(TFGUARD), *args],
        capture_output=True,
        text=True,
        env=environment,
        cwd=cwd,
    )


@pytest.fixture
def plan_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "tfplan").write_bytes(b"opaque terraform plan bytes")
    (root / ".terraform.lock.hcl").write_text('provider "registry/x" {\n  version = "1.0.0"\n}\n')
    return root


def write_metadata(stubs, plan_root, **overrides):
    arguments = {
        "--plan": str(plan_root / "tfplan"),
        "--out": str(plan_root / "plan-metadata.json"),
        "--instance": "demo",
        "--environment": "mgt",
        "--state-id": "demo-mgt",
        "--working-directory": "infrastructure/demo",
        "--backend-address": "https://gitlab.example.com/api/v4/projects/<project-id>/terraform/state/demo-mgt",
        "--lockfile": str(plan_root / ".terraform.lock.hcl"),
        "--max-age-seconds": "86400",
    }
    arguments.update(overrides)
    flat = [item for pair in arguments.items() for item in pair]
    return run(stubs, "plan-metadata", *flat)


def validate(stubs, plan_root, env=None, **overrides):
    arguments = {
        "--plan": str(plan_root / "tfplan"),
        "--metadata": str(plan_root / "plan-metadata.json"),
        "--instance": "demo",
        "--environment": "mgt",
        "--state-id": "demo-mgt",
        "--working-directory": "infrastructure/demo",
        "--lockfile": str(plan_root / ".terraform.lock.hcl"),
        "--max-age-seconds": "86400",
    }
    arguments.update(overrides)
    flat = [item for pair in arguments.items() for item in pair]
    return run(stubs, "validate-plan", *flat, env=env)


# --------------------------------------------------------------------------
# plan-metadata (section 9.3)
# --------------------------------------------------------------------------


def test_plan_metadata_records_every_required_field(stubs, plan_root):
    import json

    assert write_metadata(stubs, plan_root).returncode == 0
    record = json.loads((plan_root / "plan-metadata.json").read_text())

    assert record["schema_version"] == 1
    assert record["engine"] == "terraform"
    assert record["engine_version"] == TERRAFORM_VERSION
    assert record["instance"] == "demo"
    assert record["environment"] == "mgt"
    assert record["state_id"] == "demo-mgt"
    assert record["working_directory"] == "infrastructure/demo"
    assert record["source_commit"] == CI_ENV["CI_COMMIT_SHA"]
    # Serialised consistently as strings, per section 9.1's rule for IDs.
    assert record["pipeline_id"] == "4242"
    assert record["job_id"] == "9001"
    assert record["max_age_seconds"] == 86400
    assert record["created_at"].endswith("Z")
    assert len(record["plan_sha256"]) == 64
    assert len(record["lockfile_sha256"]) == 64
    # Backend identity without the credential.
    assert "example.com" in record["backend_address"]
    assert "password" not in record["backend_address"].lower()


def test_plan_metadata_refuses_a_root_with_no_provider_lockfile(stubs, plan_root):
    (plan_root / ".terraform.lock.hcl").unlink()
    result = write_metadata(stubs, plan_root)
    assert result.returncode == 1
    assert "lockfile" in result.stderr


def test_plan_metadata_refuses_a_missing_plan(stubs, plan_root):
    (plan_root / "tfplan").unlink()
    result = write_metadata(stubs, plan_root)
    assert result.returncode == 1
    assert "no plan file" in result.stderr


def test_plan_metadata_refuses_an_empty_plan(stubs, plan_root):
    (plan_root / "tfplan").write_bytes(b"")
    result = write_metadata(stubs, plan_root)
    assert result.returncode == 1
    assert "empty" in result.stderr


def test_plan_metadata_refuses_to_record_an_unknown_source_commit(stubs, plan_root):
    result = write_metadata(stubs, plan_root, **{})
    assert result.returncode == 0
    result = run(
        stubs,
        "plan-metadata",
        "--plan", str(plan_root / "tfplan"),
        "--out", str(plan_root / "plan-metadata.json"),
        "--instance", "demo",
        "--environment", "mgt",
        "--state-id", "demo-mgt",
        "--working-directory", "infrastructure/demo",
        "--backend-address", "https://example.invalid",
        "--lockfile", str(plan_root / ".terraform.lock.hcl"),
        "--max-age-seconds", "86400",
        env={"CI_COMMIT_SHA": ""},
    )
    assert result.returncode == 1
    assert "source_commit" in result.stderr


# --------------------------------------------------------------------------
# validate-plan (section 11.2 item 7)
# --------------------------------------------------------------------------


def test_validate_plan_accepts_the_plan_it_was_written_for(stubs, plan_root):
    assert write_metadata(stubs, plan_root).returncode == 0
    result = validate(stubs, plan_root)
    assert result.returncode == 0, result.stderr
    assert "plan evidence accepted" in result.stderr


def test_validate_plan_rejects_a_tampered_plan_file(stubs, plan_root):
    assert write_metadata(stubs, plan_root).returncode == 0
    (plan_root / "tfplan").write_bytes(b"a different plan entirely")
    result = validate(stubs, plan_root)
    assert result.returncode == 1
    assert "plan file sha256 mismatch" in result.stderr


def test_validate_plan_rejects_a_changed_provider_lockfile(stubs, plan_root):
    assert write_metadata(stubs, plan_root).returncode == 0
    (plan_root / ".terraform.lock.hcl").write_text('provider "registry/x" {\n  version = "2.0.0"\n}\n')
    result = validate(stubs, plan_root)
    assert result.returncode == 1
    assert "provider lockfile sha256 mismatch" in result.stderr


def test_validate_plan_rejects_a_stale_plan(stubs, plan_root):
    assert write_metadata(stubs, plan_root).returncode == 0
    far_future = str(2 * 10 ** 9)
    result = validate(stubs, plan_root, **{"--now": far_future})
    assert result.returncode == 1
    assert "maximum deployable age" in result.stderr


def test_validate_plan_rejects_a_plan_from_the_future(stubs, plan_root):
    assert write_metadata(stubs, plan_root).returncode == 0
    result = validate(stubs, plan_root, **{"--now": "1000000000"})
    assert result.returncode == 1
    assert "in the future" in result.stderr


@pytest.mark.parametrize(
    ("flag", "value", "expected"),
    [
        ("--instance", "other", "instance mismatch"),
        ("--environment", "prod", "environment mismatch"),
        ("--state-id", "other-state", "state id mismatch"),
        ("--working-directory", "elsewhere", "working directory mismatch"),
        ("--max-age-seconds", "3600", "maximum deployable plan age mismatch"),
    ],
)
def test_validate_plan_rejects_a_plan_for_a_different_target(
    stubs, plan_root, flag, value, expected
):
    assert write_metadata(stubs, plan_root).returncode == 0
    result = validate(stubs, plan_root, **{flag: value})
    assert result.returncode == 1
    assert expected in result.stderr


def test_validate_plan_rejects_a_plan_from_a_different_commit(stubs, plan_root):
    assert write_metadata(stubs, plan_root).returncode == 0
    result = validate(stubs, plan_root, env={"CI_COMMIT_SHA": "0" * 40})
    assert result.returncode == 1
    assert "source commit mismatch" in result.stderr


def test_validate_plan_rejects_a_plan_from_a_different_engine_version(stubs, plan_root):
    assert write_metadata(stubs, plan_root).returncode == 0
    (stubs.dir / "terraform").write_text("#!/bin/sh\necho 'Terraform v1.13.3'\n")
    result = validate(stubs, plan_root)
    assert result.returncode == 1
    assert "engine version mismatch" in result.stderr


def test_validate_plan_rejects_a_plan_with_no_metadata(stubs, plan_root):
    result = validate(stubs, plan_root)
    assert result.returncode == 1
    assert "no plan metadata" in result.stderr


def test_validate_plan_rejects_metadata_that_is_not_ours(stubs, plan_root):
    (plan_root / "plan-metadata.json").write_text('{"schema_version": 1}\n')
    result = validate(stubs, plan_root)
    assert result.returncode == 1
    assert "is not a plan-metadata.json this version wrote" in result.stderr


def test_validate_plan_rejects_an_unparseable_creation_time(stubs, plan_root):
    assert write_metadata(stubs, plan_root).returncode == 0
    path = plan_root / "plan-metadata.json"
    text = path.read_text()
    broken = "\n".join(
        '  "created_at": "yesterday",' if line.strip().startswith('"created_at"') else line
        for line in text.splitlines()
    )
    path.write_text(broken + "\n")
    result = validate(stubs, plan_root)
    assert result.returncode == 1
    assert "RFC 3339" in result.stderr


# --------------------------------------------------------------------------
# destroy-guard (section 11.2 item 10)
# --------------------------------------------------------------------------


def test_destroy_guard_passes_a_plan_that_removes_nothing(stubs):
    result = run(stubs, "destroy-guard", "--plan-json", str(FIXTURES / "plan-noop.json"))
    assert result.returncode == 0
    assert "destroys and replaces nothing" in result.stderr


def test_destroy_guard_blocks_a_plan_that_destroys_or_replaces(stubs):
    result = run(
        stubs, "destroy-guard", "--plan-json", str(FIXTURES / "plan-destroy-replace.json")
    )
    assert result.returncode == 3
    assert "destroying: 1, replacing: 1" in result.stderr
    assert "allow-destroy" in result.stderr


def test_destroy_guard_allows_removal_when_explicitly_permitted(stubs):
    result = run(
        stubs,
        "destroy-guard",
        "--plan-json", str(FIXTURES / "plan-destroy-replace.json"),
        "--allow-destroy", "true",
    )
    assert result.returncode == 0
    assert "allow-destroy is true" in result.stderr


def test_destroy_guard_does_not_mistake_drift_for_a_planned_deletion(stubs):
    """resource_drift is observed reality; terraform serialises it before
    resource_changes and it removes nothing."""
    result = run(stubs, "destroy-guard", "--plan-json", str(FIXTURES / "plan-drift-only.json"))
    assert result.returncode == 0, result.stderr


def test_destroy_guard_fails_on_an_empty_plan_json(stubs, tmp_path):
    empty = tmp_path / "plan.json"
    empty.write_text("")
    result = run(stubs, "destroy-guard", "--plan-json", str(empty))
    assert result.returncode == 1
    assert "empty" in result.stderr


def test_destroy_guard_fails_on_a_missing_plan_json(stubs, tmp_path):
    result = run(stubs, "destroy-guard", "--plan-json", str(tmp_path / "absent.json"))
    assert result.returncode == 1
    assert "no plan JSON" in result.stderr


def test_destroy_guard_fails_rather_than_vouch_for_a_shape_it_cannot_read(stubs, tmp_path):
    """The positive control.

    A scanner that finds nothing looks exactly like a clean plan. If terraform
    ever changes how it serialises a resource change, this must stop the apply
    instead of waving it through.
    """
    mangled = (FIXTURES / "plan-destroy-replace.json").read_text().replace(
        '"change":{"actions":', '"change":{"acts":'
    )
    path = tmp_path / "plan.json"
    path.write_text(mangled)
    result = run(stubs, "destroy-guard", "--plan-json", str(path))
    assert result.returncode == 1
    assert "cannot vouch" in result.stderr


# --------------------------------------------------------------------------
# vault-login (section 10.2)
# --------------------------------------------------------------------------


VAULT_ARGS = (
    "vault-login",
    "--address", "https://vault.example.invalid:8200",
    "--jwt-path", "jwt",
    "--role", "gitlab-ci-terraform",
)


def test_vault_login_returns_only_the_client_token(stubs):
    stubs("wget", 'echo \'{"auth":{"client_token":"s.thetoken","lease_duration":3600}}\'\n')
    result = run(stubs, *VAULT_ARGS, env={"VAULT_ID_TOKEN": "a.jwt.value"})
    assert result.returncode == 0
    assert result.stdout.strip() == "s.thetoken"


def test_vault_login_fails_when_no_id_token_was_minted(stubs):
    stubs("wget", 'echo \'{"auth":{"client_token":"s.thetoken"}}\'\n')
    result = run(stubs, *VAULT_ARGS, env={"VAULT_ID_TOKEN": ""})
    assert result.returncode == 1
    assert "VAULT_ID_TOKEN is empty" in result.stderr


def test_vault_login_fails_when_the_request_fails(stubs):
    stubs("wget", "exit 8\n")
    result = run(stubs, *VAULT_ARGS, env={"VAULT_ID_TOKEN": "a.jwt.value"})
    assert result.returncode == 1
    assert "Vault login" in result.stderr


def test_vault_login_fails_on_a_response_with_no_client_token(stubs):
    """The estate's consumers sed the token out and use whatever comes back.

    A permission-denied body has no client_token, the sed matches nothing, and
    Terraform then fails much later with an unrelated provider error.
    """
    stubs("wget", 'echo \'{"errors":["permission denied"]}\'\n')
    result = run(stubs, *VAULT_ARGS, env={"VAULT_ID_TOKEN": "a.jwt.value"})
    assert result.returncode == 1
    assert "no client_token" in result.stderr
    # The body is never echoed: on a success it would be the token itself.
    assert "permission denied" not in result.stderr


# --------------------------------------------------------------------------
# module-version (section 7.2)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("v1.2.3", "1.2.3"), ("1.2.3", "1.2.3"), ("v0.1.0-rc.1", "0.1.0-rc.1")],
)
def test_module_version_normalises_an_optional_v_prefix(stubs, tag, expected):
    result = run(stubs, "module-version", "--tag", tag)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("tag", ["1.2", "release-1", "v1.2.3.4", "latest"])
def test_module_version_rejects_anything_that_is_not_semantic(stubs, tag):
    result = run(stubs, "module-version", "--tag", tag)
    assert result.returncode == 1
    assert "MAJOR.MINOR.PATCH" in result.stderr


# --------------------------------------------------------------------------
# module-package (section 11.5)
# --------------------------------------------------------------------------


@pytest.fixture
def module_source(tmp_path):
    source = tmp_path / "module"
    (source / ".terraform" / "providers").mkdir(parents=True)
    (source / "examples").mkdir()
    (source / "main.tf").write_text('resource "terraform_data" "x" {}\n')
    (source / "variables.tf").write_text("variable \"name\" {}\n")
    (source / "README.md").write_text("# module\n")
    (source / ".terraform" / "providers" / "cached").write_text("binary\n")
    (source / "terraform.tfstate").write_text('{"serial": 1}\n')
    (source / "terraform.tfstate.backup").write_text('{"serial": 0}\n')
    (source / "secrets.tfvars").write_text('password = "hunter2"\n')
    (source / "tfplan").write_bytes(b"saved plan")
    (source / "service.key").write_text("PRIVATE KEY\n")
    (source / ".terraform.lock.hcl").write_text("lock\n")
    return source


def test_module_package_ships_module_content_only(stubs, module_source, tmp_path):
    import tarfile

    archive = tmp_path / "module.tgz"
    result = run(
        stubs, "module-package", "--source", str(module_source), "--out", str(archive)
    )
    assert result.returncode == 0, result.stderr

    with tarfile.open(archive) as tar:
        members = {name.lstrip("./") for name in tar.getnames()}

    assert "main.tf" in members
    assert "variables.tf" in members
    assert "README.md" in members
    for excluded in (
        ".terraform/providers/cached",
        "terraform.tfstate",
        "terraform.tfstate.backup",
        "secrets.tfvars",
        "tfplan",
        "service.key",
        ".terraform.lock.hcl",
    ):
        assert excluded not in members, f"{excluded} reached the published archive"


def test_module_package_refuses_a_directory_with_no_terraform_in_it(
    stubs, tmp_path
):
    source = tmp_path / "notamodule"
    source.mkdir()
    (source / "README.md").write_text("# nothing here\n")
    result = run(
        stubs, "module-package", "--source", str(source), "--out", str(tmp_path / "x.tgz")
    )
    assert result.returncode == 1
    assert "no .tf file" in result.stderr


# --------------------------------------------------------------------------
# module-registry-assert (section 11.5)
# --------------------------------------------------------------------------


PUBLISHED = (
    '[{"id":7,"name":"hypervisor-vm/hypervisor","version":"1.2.3",'
    '"package_type":"terraform_module","_links":{"web_path":"/x"}}]'
)

REGISTRY_ARGS = (
    "module-registry-assert",
    "--api-url", "https://git.example.invalid/api/v4",
    "--project-id", "15",
    "--name", "hypervisor-vm",
    "--system", "hypervisor",
    "--version", "1.2.3",
)


def registry(stubs, expect, body=None, exit_code=0, **env):
    if body is None:
        body = "[]"
    stubs("curl", f"cat <<'BODY'\n{body}\nBODY\nexit {exit_code}\n")
    environment = {"CI_JOB_TOKEN": "job-token-value"}
    environment.update(env)
    return run(stubs, *REGISTRY_ARGS, "--expect", expect, env=environment)


def test_registry_assert_absent_passes_when_the_version_is_new(stubs):
    result = registry(stubs, "absent")
    assert result.returncode == 0, result.stderr
    assert "not published yet" in result.stderr


def test_registry_assert_absent_refuses_to_overwrite_a_release(stubs):
    result = registry(stubs, "absent", body=PUBLISHED)
    assert result.returncode == 1
    assert "immutable" in result.stderr


def test_registry_assert_present_confirms_the_upload_produced_a_package(stubs):
    result = registry(stubs, "present", body=PUBLISHED)
    assert result.returncode == 0, result.stderr
    assert "is published" in result.stderr


def test_registry_assert_present_fails_when_the_accepted_upload_produced_nothing(stubs):
    """Section 10.3's rule applied to a registry: acceptance is not publication."""
    result = registry(stubs, "present")
    assert result.returncode == 1
    assert "produced nothing" in result.stderr


def test_registry_assert_does_not_read_a_failed_request_as_an_empty_registry(stubs):
    result = registry(stubs, "absent", body="502 Bad Gateway", exit_code=22)
    assert result.returncode == 1
    assert "could not list existing packages" in result.stderr


def test_registry_assert_rejects_a_response_that_is_not_a_json_array(stubs):
    result = registry(stubs, "absent", body='{"message":"404 Project Not Found"}')
    assert result.returncode == 1
    assert "refusing to treat that as" in result.stderr


def test_registry_assert_ignores_a_matching_version_of_another_module(stubs):
    other = (
        '[{"id":9,"name":"cloudflare-dns/cloudflare","version":"1.2.3",'
        '"package_type":"terraform_module"}]'
    )
    result = registry(stubs, "absent", body=other)
    assert result.returncode == 0, result.stderr


def test_registry_assert_fails_without_a_job_token(stubs):
    result = registry(stubs, "absent", CI_JOB_TOKEN="")
    assert result.returncode == 1
    assert "CI_JOB_TOKEN" in result.stderr


# --------------------------------------------------------------------------


def test_unknown_subcommand_is_refused(stubs):
    result = run(stubs, "definitely-not-a-command")
    assert result.returncode == 2
    assert "usage:" in result.stderr
