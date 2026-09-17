#!/usr/bin/env python3
"""Render a component template's inputs so its jobs can be linted for real.

The CI Lint API resolves `include:` against files that exist on the server. A
component on an unmerged branch is not there, so a fixture cannot include it and
the merged configuration cannot be linted the way tests/pipelines/lint.py lints a
configuration whose includes the server already holds.

This module substitutes the inputs locally and hands GitLab the resulting job
configuration. What that buys is real: GitLab still compiles the pipeline,
evaluates `rules:`, resolves `needs:` against the jobs present, checks the stage
list and reports the job names it would create. What it does not buy is GitLab
performing the substitution, so the three substitution rules implemented here
are pinned against the live API by the probes in
tests/pipelines/test_container_components.py rather than assumed.

Substitution rules, as GitLab 18.9.1-ee performs them:

1. A scalar that is exactly `$[[ inputs.NAME ]]` becomes the typed input value:
   a string stays a string, an array stays an array, a boolean stays a boolean.
2. A placeholder inside a longer string is rendered as text. Arrays render as a
   JSON array literal, booleans as `true` or `false`.
3. A list item that is exactly `$[[ inputs.NAME ]]` and whose value is an array
   is flattened into the surrounding list. This is what lets `needs:` combine a
   named producer with an array of further producers.

Rule 1 applies to mapping keys as well as values, which is how a component's
job name carries its instance. It is also why an array input cannot be placed
alone in a `variables:` value: the substitution keeps the array, and GitLab
rejects a variable whose value is a list. Components that pass an array into the
shell embed the placeholder in a longer string so rule 2 renders it as a JSON
array literal.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

PLACEHOLDER = re.compile(r"\$\[\[\s*inputs\.([a-zA-Z0-9-]+)\s*\]\]")

REPO_ROOT = Path(__file__).resolve().parents[2]

# The only include-rule form written here: a literal compared against a literal,
# optionally several of them joined by `&&`. Both sides are literals because the
# inputs were substituted before the rule was read. Anything else raises rather
# than being guessed at: a rule silently treated as true would put needs in the
# rendered job that the real pipeline would not have.
TOGGLE_RULE = re.compile(
    r'^"(?P<left>[^"]*)"\s*(?P<operator>==|!=)\s*"(?P<right>[^"]*)"$'
)


class InputError(ValueError):
    """An input was missing, or did not satisfy its declared regex."""


def load(template_path: Path) -> tuple[dict, dict]:
    """Return (declared inputs, job configuration) from a component template."""
    documents = list(yaml.safe_load_all(template_path.read_text()))
    if len(documents) != 2:
        raise InputError(
            f"{template_path} has {len(documents)} YAML documents; a component "
            "is exactly two: the spec header and the jobs"
        )
    spec, jobs = documents
    return spec.get("spec", {}).get("inputs", {}) or {}, jobs


def resolve(declared: dict, supplied: dict) -> dict:
    """Apply defaults, reject unknown or missing inputs, check declared regexes."""
    unknown = set(supplied) - set(declared)
    if unknown:
        raise InputError(f"unknown inputs: {sorted(unknown)}")
    resolved: dict[str, Any] = {}
    for name, declaration in declared.items():
        declaration = declaration or {}
        if name in supplied:
            value = supplied[name]
        elif "default" in declaration:
            value = declaration["default"]
        else:
            raise InputError(f"input '{name}' is required and was not supplied")
        pattern = declaration.get("regex")
        if pattern and not re.search(pattern, str(value)):
            raise InputError(f"input '{name}' value {value!r} fails regex {pattern}")
        resolved[name] = value
    return resolved


def as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def substitute(node: Any, inputs: dict) -> Any:
    if isinstance(node, dict):
        # Keys are substituted too: a component's job name is a mapping key.
        return {
            substitute(key, inputs): substitute(value, inputs)
            for key, value in node.items()
        }
    if isinstance(node, list):
        out = []
        for item in node:
            rendered = substitute(item, inputs)
            # Rule 3: an array interpolated as a whole list item is flattened.
            if isinstance(item, str) and isinstance(rendered, list):
                out.extend(rendered)
            else:
                out.append(rendered)
        return out
    if not isinstance(node, str):
        return node
    whole = PLACEHOLDER.fullmatch(node.strip())
    if whole:
        # Rule 1: the value keeps its type.
        return lookup(whole.group(1), inputs)
    # Rule 2: rendered as text inside the surrounding string.
    return PLACEHOLDER.sub(lambda m: as_text(lookup(m.group(1), inputs)), node)


def lookup(name: str, inputs: dict) -> Any:
    if name not in inputs:
        raise InputError(f"template references undeclared input '{name}'")
    return inputs[name]


def rule_is_active(rules: list, inputs: dict) -> bool:
    """Whether an `include:` carrying these `rules:` is taken."""
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {"if", "when"}:
            raise InputError(f"unsupported include rule: {rule!r}")
        condition = substitute(rule["if"], inputs)
        clauses = [TOGGLE_RULE.match(clause.strip()) for clause in condition.split("&&")]
        if not all(clauses):
            raise InputError(
                f"include rule {condition!r} is not the literal toggle form this "
                "resolver understands; add support deliberately rather than "
                "assuming the include is active"
            )
        if all(
            (match.group("left") == match.group("right"))
            == (match.group("operator") == "==")
            for match in clauses
        ):
            return rule.get("when") != "never"
    return False


def render(template_path: Path, **supplied: Any) -> dict:
    """Return the job configuration a consumer would get from this component.

    A component may include `templates/_needs/needs.yml` to build a `needs:`
    list whose entries depend on which producers the consumer named. Those
    includes are resolved here the way GitLab resolves them: a rule that does
    not match drops its entry, and the including file wins any key it sets
    itself.
    """
    declared, body = load(template_path)
    inputs = resolve(declared, supplied)
    jobs = substitute(body, inputs)
    includes = jobs.pop("include", None)
    if not includes:
        return jobs

    merged: dict[str, Any] = {}
    for entry in includes:
        if set(entry) - {"local", "inputs", "rules"}:
            raise InputError(f"{template_path}: unsupported include entry {entry!r}")
        if "rules" in entry and not rule_is_active(entry["rules"], inputs):
            continue
        included = render(
            REPO_ROOT / entry["local"].lstrip("/"), **(entry.get("inputs") or {})
        )
        for name, job in included.items():
            merged[name] = {**merged.get(name, {}), **job}
    for name, job in jobs.items():
        merged[name] = {**merged.get(name, {}), **job}
    return merged


def compose(stages: list[str], *job_maps: dict, workflow: dict | None = None) -> str:
    """Merge rendered components into one pipeline document for the Lint API."""
    config: dict[str, Any] = {}
    if workflow:
        config["workflow"] = workflow
    config["stages"] = stages
    for jobs in job_maps:
        overlap = set(config) & set(jobs)
        if overlap - {"workflow", "stages"}:
            raise InputError(f"two components emit the same job name: {sorted(overlap)}")
        config.update(jobs)
    return yaml.safe_dump(config, sort_keys=False, default_flow_style=False, width=200)
