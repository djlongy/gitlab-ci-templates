"""The compositions must appear in the catalogue, and their contracts must hold.

Section 6.3 makes `.ci/catalog.yml` the authoritative inventory at a revision,
with each entry recording its `kind`, `component` or `pipeline`. The generator
only knew about `templates/` until this lane, so every composition was invisible
to anyone reading the catalogue to find out what exists.

Nothing here talks to the network. The drift check that keeps the committed
catalogue honest lives in tests/contracts/test_catalog.py and covers the
pipelines automatically, because it compares the whole generated document.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINES = REPO_ROOT / "pipelines"
CONTRACT_SCHEMA = REPO_ROOT / "tests" / "contracts" / "contract.schema.json"

# pipelines/ files that are deliberately not catalogued, each with the reason.
UNCATALOGUED = {
    # A compatibility entry point. Its jobs are the legacy bare names consumers
    # depend on (`gitleaks`, `buildkit-build`, ...), which the contract schema's
    # `{instance}:` pattern cannot express. Inventing a contract for it would put
    # a false claim in the catalogue, so it carries a deprecation header instead.
    "devsecops",
}


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "catalog_generate", REPO_ROOT / "runtime" / "catalog" / "generate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate = load_generator()

CONTRACTS = sorted(PIPELINES.glob("*.contract.yml"))
COMPOSITIONS = sorted(
    p.stem for p in PIPELINES.glob("*.yml") if not p.name.endswith(".contract.yml")
)


def test_every_composition_is_catalogued_or_listed_as_an_exception():
    catalogued = {e["name"] for e in generate.build_catalog().get("pipelines", [])}
    for name in COMPOSITIONS:
        if name in UNCATALOGUED:
            assert name not in catalogued, (
                f"{name} is listed as an exception but is catalogued anyway"
            )
            continue
        assert name in catalogued, (
            f"{name} has no contract, so nobody reading the catalogue can find it"
        )


def test_the_catalogue_records_the_compositions_as_pipelines():
    entries = generate.build_catalog().get("pipelines", [])
    assert entries, "the catalogue lists no compositions"
    for entry in entries:
        assert entry["kind"] == "pipeline", entry
        assert entry["path"].startswith("pipelines/"), entry
        assert entry["inputs"], f"{entry['name']} was catalogued with no inputs"
        assert "instance" in entry["inputs"] or any(
            key.endswith("instance") for key in entry["inputs"]
        ), f"{entry['name']} declares no instance input"


def test_components_and_pipelines_stay_in_separate_keys():
    catalog = generate.build_catalog()
    assert all(e["kind"] == "component" for e in catalog["components"])
    assert all(e["path"].startswith("templates/") for e in catalog["components"])


def test_a_pipeline_without_a_contract_is_not_catalogued(tmp_path, monkeypatch):
    """Positive control: the generator must be reading the contract, not guessing.

    It also pins the rule the exception list above depends on. If the generator
    started cataloguing every pipelines/*.yml, devsecops.yml would arrive in the
    catalogue carrying no contract at all.
    """
    (tmp_path / "templates").mkdir()
    pipelines = tmp_path / "pipelines"
    pipelines.mkdir()
    (pipelines / "with-contract.yml").write_text(
        "spec:\n  inputs:\n    instance:\n      default: demo\n---\nstages:\n  - verify\n"
    )
    (pipelines / "with-contract.contract.yml").write_text(
        "name: with-contract\nkind: pipeline\nstatus: experimental\n"
    )
    (pipelines / "no-contract.yml").write_text("stages:\n  - verify\n")
    monkeypatch.setattr(generate, "TEMPLATES_DIR", tmp_path / "templates")
    monkeypatch.setattr(generate, "REPO_ROOT", tmp_path)

    catalog = generate.build_catalog()
    names = [e["name"] for e in catalog["pipelines"]]
    assert names == ["with-contract"], names
    assert catalog["pipelines"][0]["inputs"]["instance"]["default"] == "demo"


def test_the_generator_still_reports_an_empty_tree_as_empty(tmp_path, monkeypatch):
    """The key appears only when there is something to put in it."""
    monkeypatch.setattr(generate, "TEMPLATES_DIR", tmp_path / "templates")
    assert generate.build_catalog() == {"schema_version": 1, "components": []}


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.name)
def test_a_composition_contract_matches_the_schema(path: Path):
    schema = json.loads(CONTRACT_SCHEMA.read_text())
    contract = yaml.safe_load(path.read_text())
    errors = sorted(
        Draft202012Validator(schema).iter_errors(contract),
        key=lambda e: list(e.absolute_path),
    )
    assert not errors, "\n".join(
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in errors
    )
    assert contract["kind"] == "pipeline"
    assert contract["name"] == path.name[: -len(".contract.yml")], (
        "a composition's contract name must equal its file name: that name is "
        "the public identifier consumers include"
    )


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.name)
def test_the_contract_lists_every_job_the_composition_can_emit(path: Path):
    """A contract that under-reports its jobs is worse than no contract.

    The optional jobs count: a consumer that turns the smoke test on gets
    `<instance>:container-smoke-test`, and it must be findable in the catalogue.
    """
    name = path.name[: -len(".contract.yml")]
    text = (PIPELINES / f"{name}.yml").read_text()
    declared = {job["pattern"].split(":", 1)[1] for job in yaml.safe_load(path.read_text())["emitted_jobs"]}
    included = {
        line.split("/templates/")[1].split("/")[0]
        for line in text.splitlines()
        if "- local: '/templates/" in line
    }
    assert declared == included, (
        f"{name}: contract lists {sorted(declared - included)} that the file does "
        f"not include, and omits {sorted(included - declared)}"
    )


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.name)
def test_the_contract_stages_match_the_composition(path: Path):
    name = path.name[: -len(".contract.yml")]
    documents = list(yaml.safe_load_all((PIPELINES / f"{name}.yml").read_text()))
    stages = set(documents[1]["stages"])
    for job in yaml.safe_load(path.read_text())["emitted_jobs"]:
        assert job["stage"] in stages, (
            f"{name}: contract puts {job['pattern']} in stage {job['stage']}, "
            f"which the composition does not declare"
        )


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.name)
def test_a_secret_value_never_reaches_the_contract(path: Path):
    """Section 6.1: secret NAMES only."""
    for secret in yaml.safe_load(path.read_text())["required_secrets"]:
        assert set(secret) <= {
            "name", "kind", "audience", "role", "scope", "protected_ref_required"
        }, secret
        assert secret["name"].isupper(), secret


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.name)
def test_evidence_claims_no_more_than_has_been_run(path: Path):
    """A composition has no runtime of its own, so a lint is its floor.

    integration-tested is the one claim a contract cannot make on its own word:
    it asserts that a pipeline ran somewhere, and the place that records which
    pipelines ran is .ci/compatibility.yml. A contract that claims it without a
    row there is a claim nobody can check, which was the state this repository
    started in.
    """
    contract = yaml.safe_load(path.read_text())
    evidence = contract["validation"]["evidence"]
    assert "gitlab-linted" in evidence
    if "integration-tested" in evidence:
        executed = yaml.safe_load(
            (REPO_ROOT / ".ci" / "compatibility.yml").read_text()
        )["evidence"].get("compositions-integration-tested", [])
        assert contract["name"] in executed, (
            f"{path.name} claims integration-tested, but .ci/compatibility.yml "
            "records no pipeline that ran it"
        )
