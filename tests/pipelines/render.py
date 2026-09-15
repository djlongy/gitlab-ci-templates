#!/usr/bin/env python3
"""Render a component template with chosen inputs, for the CI Lint harness.

Why this exists: `include: local:` resolves against the project on the server,
so a component that is not yet on the default branch cannot be included by a
lint fixture. Posting the template file itself does work - GitLab applies the
spec defaults and interpolates - but that only ever lints ONE instance with its
defaults, and section 8.10 asks for the repeat-inclusion case.

So the single-instance case is linted by GitLab doing its own interpolation,
and multi-instance fixtures are rendered here. `lint_template_defaults` and
`render` are then cross-checked against each other in the tests: if this
renderer and GitLab disagreed about the defaults, the job names would differ.

Interpolation implemented here is the documented `$[[ inputs.<name> ]]` form
only. It is not a reimplementation of GitLab's input system: no functions, no
validation, no type coercion beyond writing a value back as YAML.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def spec_and_body(path: Path) -> tuple[dict, str]:
    """The template's input spec, and its job document as raw text."""
    text = path.read_text()
    header, separator, body = text.partition("\n---\n")
    assert separator, f"{path} has no spec header"
    return yaml.safe_load(header)["spec"]["inputs"], body


def resolved_inputs(path: Path, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    spec, _ = spec_and_body(path)
    values: dict[str, Any] = {}
    for name, definition in spec.items():
        definition = definition or {}
        if name in (overrides or {}):
            values[name] = overrides[name]
        elif "default" in definition:
            values[name] = definition["default"]
        else:
            raise KeyError(f"{path.parent.name}: input '{name}' is required")
    return values


def render(component: str, overrides: dict[str, Any] | None = None) -> dict:
    """The job document a component produces for one set of inputs.

    Scalars are substituted textually, which is what GitLab documents. A
    multi-line value is refused rather than guessed at: fixtures keep list
    inputs to one entry, and the multi-line case is covered by the runtime
    tests, which run the shell against several lines directly.
    """
    path = REPO_ROOT / "templates" / component / "template.yml"
    _, body = spec_and_body(path)
    for name, value in resolved_inputs(path, overrides).items():
        placeholder = f"$[[ inputs.{name} ]]"
        if placeholder not in body:
            continue
        if isinstance(value, (list, dict)):
            # A structural input is always a whole YAML value in these templates.
            body = body.replace(f": {placeholder}", ": " + json.dumps(value))
            continue
        # Not a dict lookup keyed on True/False: Python hashes 1 and 0 equal to
        # them, so `max-retries: 1` rendered as `true` and GitLab answered
        # "retry max is not a number".
        if isinstance(value, bool):
            scalar = "true" if value else "false"
        else:
            scalar = str(value)
        assert "\n" not in scalar, f"render() cannot place a multi-line {name}"
        body = body.replace(placeholder, scalar)
    return yaml.safe_load(body)


def merged_pipeline(components: list[tuple[str, dict[str, Any]]], stages: list[str]) -> str:
    """A complete .gitlab-ci.yml with the given component instances rendered in."""
    pipeline: dict[str, Any] = {"stages": stages}
    for component, overrides in components:
        for job_name, job in render(component, overrides).items():
            assert job_name not in pipeline, f"two instances produced {job_name}"
            pipeline[job_name] = job
    return yaml.safe_dump(pipeline, sort_keys=False, width=10_000)
