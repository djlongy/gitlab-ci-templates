#!/usr/bin/env python3
"""Resolve a composition in `pipelines/` into the configuration it would create.

Why this exists
---------------
`include: local:` resolves against what the SERVER holds on the project's
default branch. A composition on an unmerged branch includes components that are
also on that branch, so a lint fixture cannot reach either of them by
`include:`. Until the branch is released, the way to lint a composition is to
perform the two substitutions GitLab performs — the composition's own inputs
into its `include:` blocks, then each component's inputs into its jobs — and
send GitLab the resulting jobs.

The component half of that is `render_component.render`, which this module
calls rather than reimplementing. What is added here is the composition half:
reading the `spec:` header of a `pipelines/*.yml`, interpolating its inputs into
each include's `inputs:` mapping, and honouring the `rules:` on an include.

What it proves and what it does not
-----------------------------------
Sending the result to the CI Lint API proves the merged configuration compiles,
that every `needs:` resolves against a job that exists, that the stages are
declared, and which job names are created. It does NOT prove that GitLab accepts
the composition's own `spec:` header, or that a consumer's `include: project:`
with `inputs:` resolves. Those are release-time checks against a pushed ref and
no test here claims them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import render_component

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINES_DIR = REPO_ROOT / "pipelines"

PLACEHOLDER = render_component.PLACEHOLDER


class Reference(list):
    """GitLab's `!reference` tag, kept intact through a load and dump cycle.

    A consumer reaches into a shared hidden job with
    `!reference [.not_for_wiki_sync, rules]`. PyYAML's SafeLoader refuses the
    unknown tag, and a loader that silently dropped it would hand the Lint API a
    job whose rules the real consumer does not have.
    """


class Loader(yaml.SafeLoader):
    pass


class Dumper(yaml.SafeDumper):
    pass


Loader.add_constructor(
    "!reference", lambda loader, node: Reference(loader.construct_sequence(node))
)
Dumper.add_representer(
    Reference,
    lambda dumper, data: dumper.represent_sequence("!reference", data, flow_style=True),
)


def load_yaml(text: str):
    return yaml.load(text, Loader=Loader)


def dump_yaml(data) -> str:
    return yaml.dump(
        data, Dumper=Dumper, sort_keys=False, default_flow_style=False, width=400
    )


class CompositionError(ValueError):
    """The composition is not the shape a root composition must have."""


def load(name: str) -> tuple[dict, dict]:
    """Return (declared inputs, body) for pipelines/<name>.yml."""
    path = PIPELINES_DIR / f"{name}.yml"
    documents = list(yaml.load_all(path.read_text(), Loader=Loader))
    if len(documents) != 2:
        raise CompositionError(
            f"{path} has {len(documents)} YAML documents; a composition is "
            "exactly two: the spec header, then workflow/stages/include"
        )
    spec, body = documents
    return (spec or {}).get("spec", {}).get("inputs", {}) or {}, body


def _substitute(node: Any, values: dict) -> Any:
    """Interpolate the composition's inputs, with GitLab's typing rules."""
    return render_component.substitute(node, values)


def _rule_is_active(rules: list, values: dict) -> bool:
    """A toggle input compared against a literal, as the compositions write it.

    The form and its refusals belong to `render_component`, which applies the
    same rule to the `include:` a component makes; only the error type differs.
    """
    try:
        return render_component.rule_is_active(rules, values)
    except render_component.InputError as error:
        raise CompositionError(str(error)) from error


def resolve(name: str, **supplied: Any) -> dict:
    """The whole configuration this composition creates for these inputs."""
    declared, body = load(name)
    values = render_component.resolve(declared, supplied)

    for reference in set(PLACEHOLDER.findall(dump_yaml(body))):
        if reference not in values:
            raise CompositionError(
                f"{name} interpolates an input it does not declare: {reference}"
            )

    config: dict[str, Any] = {}
    if "workflow" in body:
        # Substituted like any other part of the body: the workflow carries
        # `release-tag-pattern`, and leaving the placeholder in would make a
        # linted pipeline pass for a tag the real one rejects.
        config["workflow"] = _substitute(body["workflow"], values)
    if "stages" not in body:
        raise CompositionError(f"{name} declares no stages")
    config["stages"] = body["stages"]

    for entry in body.get("include", []):
        if set(entry) - {"local", "inputs", "rules"}:
            raise CompositionError(f"{name}: unsupported include entry {entry!r}")
        if "rules" in entry and not _rule_is_active(entry["rules"], values):
            continue
        component = Path(entry["local"]).parent.name
        inputs = _substitute(entry.get("inputs", {}), values)
        jobs = render_component.render(
            REPO_ROOT / "templates" / component / "template.yml", **inputs
        )
        clash = set(jobs) & set(config)
        if clash:
            raise CompositionError(
                f"{name}: two active includes emit the same job name: {sorted(clash)}"
            )
        config.update(jobs)
    return config


def render(name: str, **supplied: Any) -> str:
    """The configuration as a YAML document, ready for the CI Lint API."""
    return dump_yaml(resolve(name, **supplied))


def jobs_only(config: dict) -> dict:
    """Real jobs. A key starting with `.` is a hidden parent, not a job."""
    return {
        k: v
        for k, v in config.items()
        if k not in ("workflow", "stages") and not k.startswith(".")
    }


def needs_of(job: dict) -> list[str]:
    """Job names this job declares in `needs:`, whatever entry form is used."""
    names = []
    for need in job.get("needs", []) or []:
        names.append(need["job"] if isinstance(need, dict) else need)
    return names


def rule_conditions(job: dict) -> set[str]:
    """The `if:` strings that make this job eligible, as written."""
    return {
        rule["if"]
        for rule in job.get("rules", []) or []
        if isinstance(rule, dict) and "if" in rule and rule.get("when") != "never"
    }
