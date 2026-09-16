#!/usr/bin/env python3
"""Resolve a local component template into merged configuration for CI Lint.

Why this is needed
------------------
`tests/pipelines/lint.py` sends configuration to GitLab's CI Lint API, which is
the only check here that resolves `include:` for real. It resolves them against
what the SERVER holds, so a component that exists only on a local branch cannot
be reached by an `include:` in the linted content. Until the branch is released,
the way to lint a new component is to substitute its inputs the way GitLab does
and send the resulting jobs.

This is a deliberate approximation of one documented behaviour: interpolation of
`$[[ inputs.name ]]`. A string that is exactly one interpolation takes the
input's own type; an interpolation inside a longer string is substituted as
text. Everything the API itself checks — job keys, rules, stage membership,
needs, artifacts, the generated job names — is still checked by the API on the
result, which is the point of sending it.

What it does NOT prove: that GitLab accepts the `spec:inputs` header itself, or
that a consumer's `include:` resolves. Those are release-time checks against a
pushed ref, and no test here claims them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"

WHOLE = re.compile(r"^\$\[\[\s*inputs\.([a-zA-Z0-9_-]+)\s*\]\]$")
EMBEDDED = re.compile(r"\$\[\[\s*inputs\.([a-zA-Z0-9_-]+)\s*\]\]")


class InputError(ValueError):
    """An input was not supplied and has no default, or does not exist."""


def load_template(component: str) -> tuple[dict, Any]:
    """Return (declared inputs, job configuration) for templates/<component>."""
    documents = list(
        yaml.safe_load_all((TEMPLATES_DIR / component / "template.yml").read_text())
    )
    if len(documents) != 2:
        raise InputError(
            f"{component}/template.yml must be exactly two YAML documents "
            f"(spec header, then jobs); found {len(documents)}"
        )
    spec, body = documents
    return (spec or {}).get("spec", {}).get("inputs", {}) or {}, body


def resolve_values(declared: dict, supplied: dict) -> dict:
    values = {}
    for name, definition in declared.items():
        definition = definition or {}
        if name in supplied:
            values[name] = supplied[name]
        elif "default" in definition:
            values[name] = definition["default"]
        else:
            raise InputError(f"input '{name}' is required and was not supplied")
    unknown = set(supplied) - set(declared)
    if unknown:
        raise InputError(f"unknown input(s): {', '.join(sorted(unknown))}")
    # GitLab applies `regex:` and `options:` when it resolves an include, and it
    # refuses the pipeline when a value fails. This harness hands GitLab the
    # already-resolved jobs, so without the same checks a fixture can pass a
    # value a real consumer's include would be rejected for -- which is how a
    # job-path regex that refused a leading underscore linted green here and
    # failed pipeline creation in platform/infrastructure.
    for name, definition in declared.items():
        definition = definition or {}
        pattern = definition.get("regex")
        if pattern and not re.search(pattern, str(values[name])):
            raise InputError(
                f"input '{name}' value {values[name]!r} fails its declared "
                f"regex {pattern}"
            )
        options = definition.get("options")
        if options is not None and values[name] not in options:
            raise InputError(
                f"input '{name}' value {values[name]!r} is not one of {options}"
            )
    return values


def as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def interpolate(node: Any, values: dict) -> Any:
    if isinstance(node, str):
        whole = WHOLE.match(node)
        if whole:
            if whole.group(1) not in values:
                raise InputError(f"undeclared input '{whole.group(1)}'")
            return values[whole.group(1)]

        def replace(match: re.Match) -> str:
            if match.group(1) not in values:
                raise InputError(f"undeclared input '{match.group(1)}'")
            return as_text(values[match.group(1)])

        return EMBEDDED.sub(replace, node)
    if isinstance(node, dict):
        return {
            interpolate(key, values): interpolate(value, values)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [interpolate(item, values) for item in node]
    return node


def resolve(component: str, **supplied: Any) -> dict:
    """Jobs a single include of this component with these inputs would create."""
    declared, body = load_template(component)
    return interpolate(body, resolve_values(declared, supplied))


def public(jobs: dict) -> dict:
    """The jobs a consumer can see and depend on.

    A name beginning with `.` is hidden: GitLab never creates it, and a
    consumer must not reference it (section 5). A component that selects
    between two shapes, as docs-wiki-sync does for the docker and shell
    executors, carries hidden parents beside its one public job.
    """
    return {name: job for name, job in jobs.items() if not name.startswith(".")}




def merged_config(includes: list[tuple[str, dict]], *, stages: list[str], workflow=None) -> str:
    """Render the configuration a composition of these includes would produce."""
    config: dict[str, Any] = {}
    if workflow is not None:
        config["workflow"] = workflow
    config["stages"] = stages
    for component, inputs in includes:
        jobs = resolve(component, **inputs)
        overlap = set(jobs) & set(config)
        if overlap:
            raise InputError(f"two includes emit the same job name: {sorted(overlap)}")
        config.update(jobs)
    return yaml.safe_dump(config, sort_keys=False, default_flow_style=False, width=200)
