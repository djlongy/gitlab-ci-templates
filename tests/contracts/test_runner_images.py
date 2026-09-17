"""images/runner-images.yml: the schema, and the drift the schema cannot see.

D10 of the runner-images design makes promoting a runner image a reviewed digest
change in this repository, because Quay has no promote API and
`container-promote-harbor` speaks Harbor's. Nothing here reads the file: no
component, no composition, no generator. These tests are the whole of its
enforcement, so they are written to fail against the two ways a hand-edited
digest catalogue goes wrong rather than only to accept the shipped one.

The shipped catalogue is empty, because no runner-images project exists yet, so
every check that matters is also run against synthetic documents. A test that
only walked an empty list would pass whatever the schema said.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = REPO_ROOT / "images" / "runner-images.yml"
SCHEMA = Path(__file__).parent / "runner-images.schema.json"

VALID_ENTRY = {
    "name": "runner-python",
    "repository": "quay.example.com/ci/runner-python",
    "digest": "sha256:" + "ab" * 32,
    "tag": "0f1e2d3c-6210",
    "built_from": {
        "project": "platform/runner-images",
        "pipeline_id": 6210,
        "commit": "0" * 39 + "1",
    },
    "verified_at": "2026/09/17",
}


def schema() -> dict:
    return json.loads(SCHEMA.read_text())


def validator() -> Draft202012Validator:
    return Draft202012Validator(schema())


def catalogue() -> dict:
    return yaml.safe_load(CATALOGUE.read_text())


def document(*images: dict) -> dict:
    return {"schema_version": 1, "images": [copy.deepcopy(image) for image in images]}


def errors(doc: dict) -> list[str]:
    return [error.message for error in validator().iter_errors(doc)]


# --- the shipped file --------------------------------------------------------


def test_the_schema_itself_is_a_valid_schema():
    Draft202012Validator.check_schema(schema())


def test_the_catalogue_matches_its_schema():
    assert errors(catalogue()) == []


def test_the_catalogue_says_nothing_consumes_it():
    """The header is the only thing telling a reader the file is inert. Losing
    it is how a decorative file becomes one somebody believes in."""
    header = CATALOGUE.read_text()
    assert "NOTHING IN THIS REPOSITORY READS THIS FILE" in header
    assert "tests/contracts/test_runner_images.py" in header


def test_nothing_in_the_repository_reads_the_catalogue():
    """If that changes, the claim above has to change with it."""
    readers = []
    for path in list((REPO_ROOT / "templates").rglob("*.yml")) + list(
        (REPO_ROOT / "pipelines").rglob("*.yml")
    ) + list((REPO_ROOT / "runtime").rglob("*.py")):
        if "runner-images.yml" in path.read_text():
            readers.append(str(path.relative_to(REPO_ROOT)))
    assert readers == []


def test_a_valid_entry_is_accepted():
    """Positive control: the negative cases below mean nothing if the schema
    rejects a correct entry too."""
    assert errors(document(VALID_ENTRY)) == []


# --- drift: a digest that is not one -----------------------------------------


@pytest.mark.parametrize(
    "digest",
    [
        "0.2.0",
        "latest",
        "sha256:" + "ab" * 31,
        "sha256:" + "ab" * 33,
        "sha256:" + "AB" * 32,
        "sha256:",
        "",
        "@sha256:" + "ab" * 32,
        "quay.example.com/ci/runner-python@sha256:" + "ab" * 32,
    ],
)
def test_a_digest_that_is_not_sha256_and_64_hex_is_refused(digest: str):
    entry = dict(VALID_ENTRY, digest=digest)
    assert errors(document(entry)), f"{digest!r} was accepted as a digest"


def test_a_repository_carrying_a_tag_or_a_digest_is_refused():
    """The digest field is the identity; a repository that also carries one is
    two claims that can disagree."""
    assert errors(document(dict(VALID_ENTRY, repository="quay.example.com/ci/runner-python:0.2.0")))
    assert errors(
        document(
            dict(
                VALID_ENTRY,
                repository="quay.example.com/ci/runner-python@sha256:" + "ab" * 32,
            )
        )
    )


def test_an_entry_missing_its_provenance_is_refused():
    for field in ("project", "pipeline_id", "commit"):
        built_from = dict(VALID_ENTRY["built_from"])
        del built_from[field]
        assert errors(document(dict(VALID_ENTRY, built_from=built_from))), field


def test_a_short_commit_is_refused():
    """A short sha is ambiguous across a repository's lifetime, and this record
    is meant to still resolve years later."""
    built_from = dict(VALID_ENTRY["built_from"], commit="0f1e2d3")
    assert errors(document(dict(VALID_ENTRY, built_from=built_from)))


def test_an_unknown_field_is_refused():
    assert errors(document(dict(VALID_ENTRY, promoted_by="somebody")))


# --- drift: a name that appears twice ----------------------------------------
#
# JSON Schema cannot express "unique by one property", so this is a check of its
# own rather than a schema keyword.


def duplicate_names(doc: dict) -> list[str]:
    seen: dict[str, int] = {}
    for image in doc.get("images", []):
        name = image.get("name")
        seen[name] = seen.get(name, 0) + 1
    return sorted(name for name, count in seen.items() if count > 1)


def test_the_catalogue_names_each_image_once():
    assert duplicate_names(catalogue()) == []


def test_a_repeated_name_is_caught():
    """Two entries for one image is the drift that matters: a consumer reading
    the file gets whichever the parser kept, and the other digest is a promotion
    that silently did not happen."""
    other = dict(VALID_ENTRY, digest="sha256:" + "cd" * 32, tag="9a8b7c6d-6211")
    assert duplicate_names(document(VALID_ENTRY, other)) == ["runner-python"]


def test_two_different_images_are_not_drift():
    other = dict(VALID_ENTRY, name="runner-node", repository="quay.example.com/ci/runner-node")
    assert duplicate_names(document(VALID_ENTRY, other)) == []


def test_one_digest_under_two_names_is_allowed():
    """Two images can legitimately resolve to one digest when a rebuild changed
    nothing, so the uniqueness rule is on the name and not on the digest."""
    other = dict(VALID_ENTRY, name="runner-node", repository="quay.example.com/ci/runner-node")
    assert errors(document(VALID_ENTRY, other)) == []
