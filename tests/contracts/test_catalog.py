"""Checks on .ci/catalog.yml and the component contracts that feed it.

With no templates/ directory yet the real catalogue is empty, so the drift check
alone would pass without proving the generator can read anything. The synthetic
fixture below is the positive control: it builds a component on disk and asserts
the generator picks up its inputs and its contract. Without it, a generator that
silently returned nothing would look exactly like a correct one.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = Path(__file__).parent / "contract.schema.json"
TEMPLATES_DIR = REPO_ROOT / "templates"


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "catalog_generate", REPO_ROOT / "runtime" / "catalog" / "generate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate = load_generator()


def contract_paths() -> list[Path]:
    if not TEMPLATES_DIR.is_dir():
        return []
    return sorted(TEMPLATES_DIR.glob("*/contract.yml"))


def test_catalog_is_not_stale():
    assert generate.main(["--check"]) == 0, (
        ".ci/catalog.yml does not match the templates on disk; "
        "run python3 runtime/catalog/generate.py --write"
    )


def test_generator_tolerates_a_missing_templates_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(generate, "TEMPLATES_DIR", tmp_path / "templates")
    catalog = generate.build_catalog()
    assert catalog == {"schema_version": 1, "components": []}


def test_generator_reads_a_real_component(tmp_path, monkeypatch):
    """Positive control for the drift check above."""
    component = tmp_path / "templates" / "terraform-fmt"
    component.mkdir(parents=True)
    (component / "template.yml").write_text(
        "spec:\n"
        "  inputs:\n"
        "    instance:\n"
        "      description: Component instance identifier.\n"
        "    stage:\n"
        "      default: verify\n"
        "---\n"
        '"$[[ inputs.instance ]]:terraform-fmt":\n'
        "  stage: $[[ inputs.stage ]]\n"
        "  script:\n"
        "    - terraform fmt -check\n"
    )
    (component / "contract.yml").write_text(
        "name: terraform-fmt\n"
        "kind: component\n"
        "status: experimental\n"
        "runtime_image:\n"
        "  reference: hashicorp/terraform:1.11\n"
        "  pinning: tag\n"
    )
    monkeypatch.setattr(generate, "TEMPLATES_DIR", tmp_path / "templates")
    monkeypatch.setattr(generate, "REPO_ROOT", tmp_path)

    catalog = generate.build_catalog()
    assert len(catalog["components"]) == 1
    entry = catalog["components"][0]
    assert entry["name"] == "terraform-fmt"
    assert entry["kind"] == "component"
    assert entry["status"] == "experimental"
    assert entry["path"] == "templates/terraform-fmt/template.yml"
    assert set(entry["inputs"]) == {"instance", "stage"}
    assert entry["inputs"]["stage"]["default"] == "verify"
    # Contract fields the generator does not know about are carried through, so
    # extending contract.schema.json does not mean editing the generator.
    assert entry["runtime_image"]["pinning"] == "tag"


def test_contract_schema_is_valid():
    Draft202012Validator.check_schema(json.loads(CONTRACT_SCHEMA.read_text()))


@pytest.mark.parametrize(
    "path", contract_paths(), ids=lambda p: p.parent.name if contract_paths() else "none"
)
def test_contract_matches_its_schema(path: Path):
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
    assert contract["name"] == path.parent.name, (
        "a component's contract name must equal its directory name, "
        "because that name is the public API identifier"
    )


@pytest.mark.parametrize(
    "path", contract_paths(), ids=lambda p: p.parent.name if contract_paths() else "none"
)
def test_every_component_has_a_template(path: Path):
    assert (path.parent / "template.yml").exists()


def test_schema_rejects_a_malformed_contract():
    """Positive control for the contract schema.

    contract_paths() is empty today, so every contract test above is skipped and
    proves nothing. This one always runs.
    """
    schema = json.loads(CONTRACT_SCHEMA.read_text())
    validator = Draft202012Validator(schema)
    # Missing every required field.
    assert list(validator.iter_errors({}))
    # A deprecated component with no replacement recorded.
    assert list(validator.iter_errors({
        "name": "container-build-buildkit",
        "kind": "component",
        "status": "deprecated",
        "introduced_in": "1.0.0",
        "minimum_gitlab": "18.9",
        "emitted_jobs": [{"pattern": "{instance}:container-build-buildkit", "stage": "build"}],
        "outputs": [],
        "required_secrets": [],
        "runtime_image": {"reference": "moby/buildkit:v0.18.2", "pinning": "tag"},
        "trust": {"privileged": False, "network_egress": []},
        "validation": {"evidence": ["source-reviewed"], "verified_at": "2026/09/15"},
    }))
    # A job name outside the stage vocabulary.
    assert list(validator.iter_errors({
        "name": "container-build-buildkit",
        "kind": "component",
        "status": "released",
        "introduced_in": "1.0.0",
        "minimum_gitlab": "18.9",
        "emitted_jobs": [{"pattern": "{instance}:container-build-buildkit", "stage": "gate"}],
        "outputs": [],
        "required_secrets": [],
        "runtime_image": {"reference": "moby/buildkit:v0.18.2", "pinning": "tag"},
        "trust": {"privileged": False, "network_egress": []},
        "validation": {"evidence": ["source-reviewed"], "verified_at": "2026/09/15"},
    }))
