#!/usr/bin/env python3
"""Build .ci/catalog.yml from the template headers and component contracts.

The catalogue is generated, never hand-edited: section 6.3 of
docs/gitlab-ci-agent-standard.md makes it the authoritative inventory at a given
revision, and a hand-edited inventory is a claim rather than a fact. Each entry
is the union of two sources:

  templates/<name>/template.yml   the spec:inputs header, read as declared
  templates/<name>/contract.yml   everything spec:inputs cannot express

Compositions are catalogued the same way, under a `pipelines:` key, from

  pipelines/<name>.yml            its spec:inputs header
  pipelines/<name>.contract.yml   its contract

Section 6.3 gives every entry a `kind`, `component` or `pipeline`. They are
separate keys here rather than one list because the key is what a reader indexes
by, and an entry under `components:` whose kind says `pipeline` reads as a
mistake.

A pipeline with no contract file is not catalogued. Writing a contract is how a
composition enters the catalogue, and a composition whose emitted job names the
contract schema's `{instance}:` pattern cannot express does not get one invented
for it: that would put a false claim in the catalogue.

Usage:
    python3 runtime/catalog/generate.py --write    rewrite .ci/catalog.yml
    python3 runtime/catalog/generate.py --check    exit 1 if it is out of date
    python3 runtime/catalog/generate.py            print it

With no templates/ directory the catalogue is empty. That is a valid state, not
an error: this repository's templates have not been migrated yet.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"
CATALOG_PATH = REPO_ROOT / ".ci" / "catalog.yml"

HEADER = """\
---
# GENERATED FILE — do not edit.
#
# Rebuild with:
#     python3 runtime/catalog/generate.py --write
#
# Source of truth is templates/<name>/template.yml (its spec:inputs header) plus
# templates/<name>/contract.yml, and pipelines/<name>.yml plus
# pipelines/<name>.contract.yml for the compositions. Editing this file by hand
# makes the drift check in tests/contracts/test_catalog.py fail, which is the
# point.
"""


def read_spec_inputs(template_path: Path) -> dict:
    """Return the spec:inputs mapping declared in a component header.

    A component template is two YAML documents: the spec header, then the job
    configuration. Only the header is read here.
    """
    documents = list(yaml.safe_load_all(template_path.read_text()))
    if not documents or not isinstance(documents[0], dict):
        return {}
    spec = documents[0].get("spec")
    if not isinstance(spec, dict):
        return {}
    inputs = spec.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def build_entry(template_dir: Path) -> dict:
    name = template_dir.name
    template_path = template_dir / "template.yml"
    contract_path = template_dir / "contract.yml"

    contract = {}
    if contract_path.exists():
        loaded = yaml.safe_load(contract_path.read_text())
        if isinstance(loaded, dict):
            contract = loaded

    entry = {
        "name": contract.get("name", name),
        "kind": contract.get("kind", "component"),
        "path": str(template_path.relative_to(REPO_ROOT)),
        "status": contract.get("status", "proposed"),
        "inputs": read_spec_inputs(template_path),
    }
    # Everything else is passed through from the contract verbatim, so a field
    # added to contract.schema.json reaches the catalogue without touching this
    # generator.
    for key, value in contract.items():
        if key not in ("name", "kind", "status"):
            entry[key] = value
    return entry


def build_pipeline_entry(pipeline_path: Path, contract_path: Path) -> dict:
    """One catalogue entry for a composition in pipelines/."""
    contract = yaml.safe_load(contract_path.read_text())
    if not isinstance(contract, dict):
        contract = {}

    entry = {
        "name": contract.get("name", pipeline_path.stem),
        "kind": contract.get("kind", "pipeline"),
        "path": str(pipeline_path.relative_to(REPO_ROOT)),
        "status": contract.get("status", "proposed"),
        "inputs": read_spec_inputs(pipeline_path),
    }
    for key, value in contract.items():
        if key not in ("name", "kind", "status"):
            entry[key] = value
    return entry


def build_catalog() -> dict:
    entries = []
    if TEMPLATES_DIR.is_dir():
        for template_dir in sorted(p for p in TEMPLATES_DIR.iterdir() if p.is_dir()):
            if (template_dir / "template.yml").exists():
                entries.append(build_entry(template_dir))

    # Derived from TEMPLATES_DIR rather than read from a global, so a caller that
    # points the generator at another tree gets that tree's pipelines too.
    pipelines_dir = TEMPLATES_DIR.parent / "pipelines"
    pipelines = []
    if pipelines_dir.is_dir():
        for pipeline_path in sorted(pipelines_dir.glob("*.yml")):
            if pipeline_path.name.endswith(".contract.yml"):
                continue
            contract_path = pipeline_path.with_suffix(".contract.yml")
            if contract_path.exists():
                pipelines.append(build_pipeline_entry(pipeline_path, contract_path))

    catalog = {"schema_version": 1, "components": entries}
    # Only when there are any: the empty catalogue is a documented valid state
    # and a key that is always there but always empty says nothing.
    if pipelines:
        catalog["pipelines"] = pipelines
    return catalog


class IndentedDumper(yaml.SafeDumper):
    """Indent sequences under their parent key.

    PyYAML writes a block sequence at the same column as the key that owns it.
    That is valid YAML and yamllint's default indentation rule rejects it, so
    the generated catalogue failed the repository's own lint command while every
    hand-written file passed. Forcing indentless off puts the dashes one level
    in, which is what the rest of the repository looks like.
    """

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow=flow, indentless=False)


def render(catalog: dict) -> str:
    body = yaml.dump(
        catalog,
        Dumper=IndentedDumper,
        sort_keys=False,
        default_flow_style=False,
        width=88,
    )
    return HEADER + body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="rewrite .ci/catalog.yml")
    group.add_argument("--check", action="store_true", help="fail if it is out of date")
    args = parser.parse_args(argv)

    rendered = render(build_catalog())

    if args.write:
        CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CATALOG_PATH.write_text(rendered)
        print(f"wrote {CATALOG_PATH.relative_to(REPO_ROOT)}")
        return 0

    if args.check:
        current = CATALOG_PATH.read_text() if CATALOG_PATH.exists() else ""
        if current != rendered:
            print(
                f"{CATALOG_PATH.relative_to(REPO_ROOT)} is out of date; "
                "run: python3 runtime/catalog/generate.py --write",
                file=sys.stderr,
            )
            return 1
        return 0

    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
