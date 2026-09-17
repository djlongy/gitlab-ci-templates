"""Checks on docs/bricks.md and the generator that writes it.

The drift check alone would pass on an empty file if the generator silently
produced nothing, so the synthetic component below is the positive control: it
builds a template, a contract and a runtime file on disk and asserts the card
names all three plus the test that covers the runtime file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"
BRICKS_PATH = REPO_ROOT / "docs" / "bricks.md"


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "catalog_generate", REPO_ROOT / "runtime" / "catalog" / "generate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate = load_generator()


def component_names() -> list[str]:
    if not TEMPLATES_DIR.is_dir():
        return []
    return sorted(p.parent.name for p in TEMPLATES_DIR.glob("*/template.yml"))


def test_bricks_is_not_stale():
    assert BRICKS_PATH.read_text() == generate.render_bricks(), (
        "docs/bricks.md does not match the templates on disk; "
        "run python3 runtime/catalog/generate.py --write"
    )


@pytest.mark.parametrize("name", component_names(), ids=lambda n: n)
def test_every_component_has_a_card(name: str):
    assert f"\n## {name}\n" in BRICKS_PATH.read_text()


def build_fixture(tmp_path: Path, monkeypatch, *, purpose: str = "Do one thing.") -> Path:
    component = tmp_path / "templates" / "demo-brick"
    component.mkdir(parents=True)
    (component / "template.yml").write_text(
        f"---\n# {purpose}\n\n"
        "#### Public interface ####\n"
        "spec:\n"
        "  inputs:\n"
        "    instance:\n"
        "      description: Component instance identifier.\n"
        "    build-job:\n"
        "      description: Producer of the image identity.\n"
        "    gate-jobs:\n"
        "      description: Extra gates.\n"
        "      default: []\n"
        "---\n"
        '"$[[ inputs.instance ]]:demo-brick":\n'
        "  stage: scan\n"
        "  variables:\n"
        "    CI_TPL_IDENTITY_FILE: "
        ".ci-artifacts/$[[ inputs.instance ]]/demo-producer/image.json\n"
        "  needs:\n"
        "    - job: $[[ inputs.build-job ]]\n"
        "      artifacts: true\n"
        "    - $[[ inputs.gate-jobs ]]\n"
        "  before_script:\n"
        "    - |\n"
        "      # BEGIN embed inline runtime/scan/demo.sh\n"
        "  script:\n"
        "    - demo\n"
    )
    (component / "contract.yml").write_text(
        "name: demo-brick\n"
        "kind: component\n"
        "status: experimental\n"
        "emitted_jobs:\n"
        "  - pattern: '{instance}:demo-brick'\n"
        "    stage: scan\n"
        "trust:\n"
        "  privileged: false\n"
        "  network_egress:\n"
        "    - registry.example\n"
    )
    tests_dir = tmp_path / "tests" / "runtime" / "scan"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_demo.py").write_text("def test_demo():\n    pass\n")
    monkeypatch.setattr(generate, "TEMPLATES_DIR", tmp_path / "templates")
    monkeypatch.setattr(generate, "TESTS_DIR", tmp_path / "tests")
    monkeypatch.setattr(generate, "REPO_ROOT", tmp_path)
    return component


def test_a_card_carries_every_field_a_maintainer_needs(tmp_path, monkeypatch):
    """Positive control for the drift check above."""
    build_fixture(tmp_path, monkeypatch)
    card = generate.render_bricks()

    assert "## demo-brick" in card
    assert "Do one thing." in card
    assert "`{instance}:demo-brick` in `scan`" in card
    # Required inputs are the ones with no default; gate-jobs has one.
    assert "- Required inputs: `instance`, `build-job`" in card
    assert "`build-job` (artifacts, required)" in card
    assert "`gate-jobs` (gate list, default `[]`)" in card
    assert ".ci-artifacts/{instance}/demo-producer/image.json" in card
    assert "`templates/demo-brick/template.yml`" in card
    assert "`templates/demo-brick/contract.yml`" in card
    assert "`runtime/scan/demo.sh`" in card
    assert "- Test: `python3 -m pytest tests/runtime/scan/test_demo.py`" in card
    assert "- Egress: `registry.example`" in card


def test_a_template_with_no_purpose_is_rejected(tmp_path, monkeypatch):
    """A card with an invented purpose would be worse than no card."""
    component = build_fixture(tmp_path, monkeypatch)
    template = component / "template.yml"
    template.write_text(template.read_text().replace("# Do one thing.\n", ""))
    with pytest.raises(generate.BrickError):
        generate.render_bricks()
