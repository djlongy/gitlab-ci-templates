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

The same two sources produce docs/bricks.md: one maintenance card per component,
so the files, inputs, producers, test command and egress for a brick are in one
place rather than spread over a template, a contract, a runtime directory and a
test tree. It is generated for the same reason the catalogue is: a hand-written
card is a claim about the template, and claims drift.

Usage:
    python3 runtime/catalog/generate.py --write    rewrite both generated files
    python3 runtime/catalog/generate.py --check    exit 1 if either is stale
    python3 runtime/catalog/generate.py            print the catalogue

With no templates/ directory the catalogue is empty. That is a valid state, not
an error: this repository's templates have not been migrated yet.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"
CATALOG_PATH = REPO_ROOT / ".ci" / "catalog.yml"
BRICKS_PATH = REPO_ROOT / "docs" / "bricks.md"
TESTS_DIR = REPO_ROOT / "tests"

# `# BEGIN embed inline runtime/x.sh` and
# `# BEGIN embed dir $CI_TPL_RUNTIME_DIR runtime/a.py runtime/b.py`: the paths
# are whatever tokens on that line start with `runtime/`.
EMBED_MARKER = re.compile(r"#\s*BEGIN embed\s+(?P<rest>.*)$")
ARTIFACT_PATH = re.compile(r"\.ci-artifacts/\S+")
INPUT_REFERENCE = re.compile(r"\$\[\[\s*inputs\.([a-z0-9-]+)\s*\]\]")

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


# --------------------------------------------------------------------------
# docs/bricks.md — one maintenance card per component
# --------------------------------------------------------------------------

BRICKS_HEADER = """\
<!-- GENERATED FILE — do not edit. Rebuild with:
         python3 runtime/catalog/generate.py --write
     Source of truth is templates/<name>/template.yml and
     templates/<name>/contract.yml. The drift check is in
     tests/contracts/test_bricks.py. -->

# Brick cards

One card per component: what it is for, what it must be told, what it reads from
another job, every file it is made of, the one command that tests it, and where
it talks to. A brick is picked out of this list and included on its own;
docs/howto/pick-your-bricks.md assembles three kits from it.

Each card is generated, so a field that is empty here is empty in the template
or the contract, not omitted.
"""


class BrickError(RuntimeError):
    """A template the card generator cannot describe without guessing."""


def purpose_of(template_path: Path) -> str:
    """The leading comment block of a template, as one paragraph.

    Some templates open with their own path and some with a `#### section ####`
    banner. Neither is a purpose, so both are skipped; a template with nothing
    left raises rather than getting an invented one.
    """
    own_path = str(template_path.relative_to(REPO_ROOT))
    lines = template_path.read_text().splitlines()
    index = 1 if lines and lines[0].strip() == "---" else 0
    paragraph: list[str] = []
    while index < len(lines) and lines[index].startswith("#"):
        text = lines[index].lstrip("#").strip()
        index += 1
        if text == own_path or text.endswith("####"):
            continue
        if not text:
            if paragraph:
                break
            continue
        paragraph.append(text)
    if not paragraph:
        raise BrickError(
            f"{own_path} opens with no purpose comment; write one line saying "
            "what the component is for, above its spec header"
        )
    return " ".join(paragraph)


def job_body(template_path: Path) -> dict:
    """The component's job configuration: the second YAML document.

    Loaded as text with the placeholders intact, which is what the card wants:
    `$[[ inputs.build-component ]]` names the input a maintainer would change,
    and a rendered value would not.
    """
    documents = list(yaml.safe_load_all(template_path.read_text()))
    body = documents[1] if len(documents) > 1 else {}
    return body if isinstance(body, dict) else {}


def readable(value: str) -> str:
    """`$[[ inputs.x ]]` as `{x}`, so a path reads as a path."""
    return INPUT_REFERENCE.sub(lambda m: "{" + m.group(1) + "}", value)


def producer_inputs(body: dict) -> list[tuple[str, str]]:
    """Every input this component splices into a `needs:`, with its edge kind."""
    found: list[tuple[str, str]] = []
    for job in body.values():
        if not isinstance(job, dict):
            continue
        for need in job.get("needs", []) or []:
            if isinstance(need, str):
                for name in INPUT_REFERENCE.findall(need):
                    found.append((name, "gate list"))
            elif isinstance(need, dict):
                kind = "artifacts" if need.get("artifacts") else "gate"
                for name in INPUT_REFERENCE.findall(str(need.get("job", ""))):
                    found.append((name, kind))
    return sorted(set(found))


def artefacts_read(name: str, body: dict) -> list[str]:
    """Artefact paths the job reads from another component's directory."""
    paths = set()
    for job in body.values():
        if not isinstance(job, dict):
            continue
        for value in (job.get("variables", {}) or {}).values():
            # Placeholders first: `$[[ inputs.instance ]]` holds spaces, so a
            # path matched before it is rewritten stops at the first one.
            for match in ARTIFACT_PATH.findall(readable(str(value))):
                own = f".ci-artifacts/{{instance}}/{name}"
                if not match.startswith(own):
                    paths.add(match)
    return sorted(paths)


def embedded_runtime(template_path: Path) -> list[str]:
    paths = []
    for line in template_path.read_text().splitlines():
        match = EMBED_MARKER.search(line)
        if match:
            paths += [t for t in match.group("rest").split() if t.startswith("runtime/")]
    return sorted(set(paths))


def test_files(name: str, runtime_paths: list[str]) -> list[str]:
    """Every test that covers this brick: its runtime's tests, and any test
    file that names the component.

    Two sources because they catch different things. A runtime helper's tests
    never mention the component that embeds them, and a component with no
    embedded runtime is covered only by tests that name it.
    """
    found = set()
    for runtime_path in runtime_paths:
        candidate = Path(runtime_path)
        directory = TESTS_DIR / "runtime" / candidate.parent.relative_to("runtime")
        # image-json.sh is tested by test_image_json.py: the runtime files are
        # kebab-case and the test modules are importable, so try both spellings.
        for stem in {candidate.stem, candidate.stem.replace("-", "_")}:
            test = directory / f"test_{stem}.py"
            if test.exists():
                found.add(str(test.relative_to(REPO_ROOT)))
    if TESTS_DIR.is_dir():
        for test in TESTS_DIR.rglob("test_*.py"):
            if name in test.read_text():
                found.add(str(test.relative_to(REPO_ROOT)))
    return sorted(found)


def as_list(values: list[str]) -> str:
    return ", ".join(f"`{v}`" for v in values) if values else "none"


def build_card(template_dir: Path) -> str:
    name = template_dir.name
    template_path = template_dir / "template.yml"
    contract_path = template_dir / "contract.yml"
    contract = {}
    if contract_path.exists():
        loaded = yaml.safe_load(contract_path.read_text())
        if isinstance(loaded, dict):
            contract = loaded

    inputs = read_spec_inputs(template_path)
    body = job_body(template_path)
    required = [key for key, spec in inputs.items() if "default" not in (spec or {})]
    producers = producer_inputs(body)
    runtime_paths = embedded_runtime(template_path)

    files = [str(template_path.relative_to(REPO_ROOT))]
    if contract_path.exists():
        files.append(str(contract_path.relative_to(REPO_ROOT)))
    files += runtime_paths

    tests = test_files(name, runtime_paths)
    jobs = [
        f"`{job['pattern']}` in `{job['stage']}`"
        for job in contract.get("emitted_jobs", [])
        if isinstance(job, dict) and "pattern" in job and "stage" in job
    ]
    egress = (contract.get("trust", {}) or {}).get("network_egress", []) or []

    producer_text = "none"
    if producers:
        producer_text = ", ".join(
            f"`{input_name}` ({kind}" + (
                f", default `{inputs[input_name]['default']}`)"
                if input_name in inputs and "default" in (inputs[input_name] or {})
                else ", required)"
            )
            for input_name, kind in producers
        )

    test_command = (
        "`python3 -m pytest " + " ".join(tests) + "`" if tests else "none"
    )

    lines = [
        f"## {name}",
        "",
        purpose_of(template_path),
        "",
        f"- Status: {contract.get('status', 'proposed')}",
        f"- Jobs: {', '.join(jobs) if jobs else 'none declared in the contract'}",
        f"- Required inputs: {as_list(required)}",
        f"- Producer inputs: {producer_text}",
        f"- Reads: {as_list(artefacts_read(name, body))}",
        f"- Files: {as_list(files)}",
        f"- Test: {test_command}",
        f"- Egress: {as_list([str(e) for e in egress])}",
    ]
    return "\n".join(lines)


def render_bricks() -> str:
    cards = []
    if TEMPLATES_DIR.is_dir():
        for template_dir in sorted(p for p in TEMPLATES_DIR.iterdir() if p.is_dir()):
            if (template_dir / "template.yml").exists():
                cards.append(build_card(template_dir))
    return BRICKS_HEADER + "\n" + "\n\n".join(cards) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="rewrite .ci/catalog.yml")
    group.add_argument("--check", action="store_true", help="fail if it is out of date")
    args = parser.parse_args(argv)

    generated = [
        (CATALOG_PATH, render(build_catalog())),
        (BRICKS_PATH, render_bricks()),
    ]

    if args.write:
        for path, rendered in generated:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered)
            print(f"wrote {path.relative_to(REPO_ROOT)}")
        return 0

    if args.check:
        stale = [
            path
            for path, rendered in generated
            if (path.read_text() if path.exists() else "") != rendered
        ]
        for path in stale:
            print(
                f"{path.relative_to(REPO_ROOT)} is out of date; "
                "run: python3 runtime/catalog/generate.py --write",
                file=sys.stderr,
            )
        return 1 if stale else 0

    rendered = generated[0][1]

    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
