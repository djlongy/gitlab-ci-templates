"""Checks on the consumer project records under examples/*/.ci/project.yml.

Two things are proved here. The records match tests/contracts/project.schema.json,
and they match the example pipeline beside them: same composition files, same
instances, same inputs. Section 4 of docs/gitlab-ci-agent-standard.md asks for
that consistency to be enforced in tests where both files are maintained, and it
is the only thing that stops a project record drifting into a description of a
pipeline nobody has.

The examples are the fixtures because they are the shape every consumer copies.
A record that no test reads is a record nobody has checked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "examples"
SCHEMA_PATH = Path(__file__).parent / "project.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())

EXAMPLE_DIRS = sorted(p.parent for p in EXAMPLES.glob("*/.gitlab-ci.yml"))
RECORDS = sorted(EXAMPLES.glob("*/.ci/project.yml"))


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_the_schema_is_a_valid_json_schema():
    Draft202012Validator.check_schema(SCHEMA)


def test_every_example_has_a_project_record():
    """Guard against an empty parametrisation, and against a forgotten example."""
    assert EXAMPLE_DIRS, "no examples found; the tests below would assert nothing"
    assert [p.parent.parent for p in RECORDS] == EXAMPLE_DIRS


@pytest.mark.parametrize("path", RECORDS, ids=lambda p: p.parent.parent.name)
def test_a_record_matches_the_schema(path: Path):
    errors = sorted(
        Draft202012Validator(SCHEMA).iter_errors(load(path)),
        key=lambda error: list(error.path),
    )
    assert not errors, "\n".join(
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in errors
    )


@pytest.mark.parametrize("path", RECORDS, ids=lambda p: p.parent.parent.name)
def test_a_record_describes_the_pipeline_beside_it(path: Path):
    """The record is not an intention. Every field it shares with the include matches."""
    record = load(path)
    pipeline = load(path.parent.parent / ".gitlab-ci.yml")
    includes = pipeline["include"]

    assert len(record["compositions"]) == len(includes), (
        "the record lists a different number of includes than the pipeline has"
    )
    for declared, entry in zip(record["compositions"], includes):
        assert declared["file"] == entry["file"]
        assert declared["inputs"] == entry["inputs"], (
            f"{declared['file']}: the record and the include disagree on inputs"
        )
        named = [
            value
            for name, value in entry["inputs"].items()
            if name == "instance" or name.endswith("-instance")
        ]
        assert declared["instances"] == named, (
            f"{declared['file']}: the record names {declared['instances']}, "
            f"the include names {named}"
        )

    assert record["shared_ci"]["project"] == includes[0]["project"]
    assert record["shared_ci"]["ref"] == includes[0]["ref"]


@pytest.mark.parametrize("path", RECORDS, ids=lambda p: p.parent.parent.name)
def test_an_adopted_workload_names_a_composition_that_exists(path: Path):
    record = load(path)
    if record["workload"]["adoption"] != "composition":
        assert record["workload"].get("reason"), "the schema should have caught this"
        return
    composition = REPO_ROOT / "pipelines" / f"{record['workload']['name']}.yml"
    assert composition.is_file(), f"no composition named {composition.name}"
    for declared in record["compositions"]:
        assert (REPO_ROOT / declared["file"].lstrip("/")).is_file(), declared["file"]


def test_the_schema_rejects_the_records_it_is_there_to_reject():
    """Positive control. Every assertion above passes against a schema that allows
    anything, so the refusals are exercised here rather than assumed."""
    valid = load(RECORDS[0])
    validator = Draft202012Validator(SCHEMA)

    def rejected(mutate) -> bool:
        candidate = json.loads(json.dumps(valid))
        mutate(candidate)
        return bool(list(validator.iter_errors(candidate)))

    assert rejected(lambda r: r.pop("shared_ci"))
    assert rejected(lambda r: r["shared_ci"].__setitem__("ref", "main"))
    assert rejected(lambda r: r["compositions"][0].__setitem__("instances", ["Bad_Name"]))
    assert rejected(lambda r: r["compositions"][0].__setitem__("file", "pipelines/x.yml"))
    assert rejected(lambda r: r.__setitem__("workload", {"name": "custom", "adoption": "bespoke"}))
    assert rejected(lambda r: r.__setitem__("unknown_field", True))
