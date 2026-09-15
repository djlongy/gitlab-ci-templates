#!/usr/bin/env python3
"""Build lintable root configuration out of component templates.

WHY THIS EXISTS
`include: local:` resolves against the project's default branch on the server.
A component that is not on `main` yet cannot be reached that way, and nothing in
this lane is pushed, so an include-based fixture would lint the OLD files under
terraform/ and say nothing about the new ones.

The CI Lint API does accept a root file that carries its own `spec:` header, and
it applies that header for real: input defaults, `regex` validation and
`$[[ inputs.* ]]` substitution in the body are all GitLab's own, not a local
re-implementation. Probed on gitlab.example.com 18.9.1-ee, 2026/09/15:

    content 'spec:\\n  inputs:\\n    instance:\\n      default: demo\\n---\\n...'
    -> valid, jobs ['demo:terraform-fmt']
    a default violating its own regex -> 'default value does not match required
    RegEx pattern'
    a required input with no default  -> 'required value has not been provided'

So this module supplies values by writing them into the spec header as defaults
and leaves the body untouched. Its only rewrite of the body is renaming input
references when two components are placed in one file, which is the sole way to
give them different values for the same input name.

The limitation to report honestly: this proves the RENDERED configuration
compiles and which jobs it creates. It does not prove that `include: local:`
with `inputs:` resolves, because that needs the branch on the server.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"


class ComponentError(RuntimeError):
    """The template is not the two-document shape a component must have."""


def split_template(name: str) -> tuple[str, str]:
    """Return the component's spec header text and its job body text."""
    path = TEMPLATES_DIR / name / "template.yml"
    text = path.read_text()
    documents = text.split("\n---\n")
    if len(documents) != 2:
        raise ComponentError(
            f"{path} must be exactly two YAML documents separated by a line ---, "
            f"found {len(documents)}"
        )
    return documents[0], documents[1]


def declared_inputs(name: str) -> dict[str, Any]:
    header, _ = split_template(name)
    spec = yaml.safe_load(header)
    if not isinstance(spec, dict) or "spec" not in spec:
        raise ComponentError(f"{name} has no spec: header")
    return spec["spec"].get("inputs") or {}


def referenced_inputs(body: str) -> set[str]:
    return set(re.findall(r"\$\[\[\s*inputs\.([a-z0-9-]+)[^\]]*\]\]", body))


def _rename(body: str, old: str, new: str) -> str:
    return re.sub(
        r"\$\[\[\s*inputs\." + re.escape(old) + r"(\s*(?:\|[^\]]*)?)\]\]",
        lambda m: "$[[ inputs." + new + m.group(1) + "]]",
        body,
    )


def compose(
    stages: list[str],
    components: list[tuple[str, dict[str, Any]]],
) -> str:
    """One root configuration containing every named component.

    `components` is a list of (component-name, inputs). Each entry gets its own
    namespaced copy of the component's inputs, so two entries of one component
    can carry different values. The supplied values become the spec defaults;
    GitLab does the interpolation.
    """
    merged_inputs: dict[str, Any] = {}
    bodies: list[str] = []

    for index, (name, values) in enumerate(components, start=1):
        header, body = split_template(name)
        inputs = declared_inputs(name)

        unknown = referenced_inputs(body) - set(inputs)
        if unknown:
            raise ComponentError(
                f"{name} interpolates inputs it does not declare: {sorted(unknown)}"
            )
        unexpected = set(values) - set(inputs)
        if unexpected:
            raise ComponentError(
                f"{name} has no such input(s): {sorted(unexpected)}"
            )

        prefix = f"c{index}-"
        for input_name, declaration in inputs.items():
            declaration = dict(declaration or {})
            if input_name in values:
                declaration["default"] = values[input_name]
            merged_inputs[prefix + input_name] = declaration
            body = _rename(body, input_name, prefix + input_name)
        bodies.append(body)

    header_text = yaml.safe_dump(
        {"spec": {"inputs": merged_inputs}}, sort_keys=False, default_flow_style=False
    )
    stages_text = yaml.safe_dump({"stages": stages}, sort_keys=False)
    return header_text + "---\n" + stages_text + "\n".join(bodies)
