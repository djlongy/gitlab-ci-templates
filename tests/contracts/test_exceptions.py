"""Checks on .ci/exceptions.yml, the record of reviewed deviations.

The file enforces nothing at pipeline time, which is exactly why it needs a test:
an exception that is never read is a note, and a note drifts. What is proved here
is that every entry carries what a reviewer needs — scope, the rule it excepts,
an owner, an expiry and what limits the risk meanwhile — that no id is used
twice, and that an id a consumer project claims resolves to an entry that exists.

The id pattern is the one tests/contracts/project.schema.json already demands of
a project record's `exceptions:` list. The two files agree on it deliberately:
an id written in one must resolve in the other.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
EXCEPTIONS = REPO_ROOT / ".ci" / "exceptions.yml"
SCHEMA_PATH = Path(__file__).parent / "exceptions.schema.json"
PROJECT_SCHEMA_PATH = Path(__file__).parent / "project.schema.json"
EXAMPLES = REPO_ROOT / "examples"

SCHEMA = json.loads(SCHEMA_PATH.read_text())
RECORD = yaml.safe_load(EXCEPTIONS.read_text())
ENTRIES = RECORD["exceptions"]


def test_the_schema_is_a_valid_json_schema():
    Draft202012Validator.check_schema(SCHEMA)


def test_the_record_matches_its_schema():
    errors = sorted(
        Draft202012Validator(SCHEMA).iter_errors(RECORD),
        key=lambda error: list(error.absolute_path),
    )
    assert not errors, "\n".join(
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in errors
    )


def test_ids_are_unique():
    """An id is how a project record points at an entry. Two entries sharing one
    means a project claims an exception that is ambiguous."""
    ids = [entry["id"] for entry in ENTRIES]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    assert not duplicates, f"reused exception ids: {duplicates}"


def test_the_id_pattern_is_the_one_project_records_must_satisfy():
    """Positive control for the pattern above.

    Both schemas could agree on a pattern that neither id satisfies, and every
    other test here would still pass. This one takes the pattern from the
    project-record schema — the consumer-facing side — and applies it to the ids
    actually written.
    """
    project_schema = json.loads(PROJECT_SCHEMA_PATH.read_text())
    pattern = project_schema["properties"]["exceptions"]["items"]["pattern"]
    assert pattern == SCHEMA["properties"]["exceptions"]["items"]["properties"]["id"]["pattern"], (
        "the two schemas disagree on the exception id pattern"
    )
    validator = Draft202012Validator({"type": "string", "pattern": pattern})
    assert ENTRIES, "no exceptions recorded; the checks below would assert nothing"
    for entry in ENTRIES:
        assert not list(validator.iter_errors(entry["id"])), (
            f"{entry['id']} does not satisfy {pattern}, so a project record "
            "cannot reference it"
        )


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry["id"])
def test_an_exception_expires_after_it_was_granted(entry):
    granted = date.fromisoformat(entry["granted_at"].replace("/", "-"))
    expires = date.fromisoformat(entry["expires_at"].replace("/", "-"))
    assert expires > granted, f"{entry['id']} expires on or before it was granted"


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda entry: entry["id"])
def test_the_rule_is_quoted_from_the_standard(entry):
    """The rule field names a section and quotes it. A paraphrase cannot be
    checked against the document, and a section number alone does not say what
    was excepted."""
    standard = (REPO_ROOT / "docs" / "gitlab-ci-agent-standard.md").read_text()
    quoted = entry["rule"].split(" ", 1)[1]
    _, _, text = quoted.partition(": ")
    assert (text or quoted) in standard, (
        f"{entry['id']} quotes {text or quoted!r}, which is not in the standard"
    )


def test_every_id_a_project_record_claims_resolves():
    known = {entry["id"] for entry in ENTRIES}
    for record in sorted(EXAMPLES.glob("*/.ci/project.yml")):
        claimed = yaml.safe_load(record.read_text()).get("exceptions", [])
        unknown = sorted(set(claimed) - known)
        assert not unknown, f"{record} claims exceptions with no entry: {unknown}"
