"""images/mirrored-images.yml: the schema, the naming rule, and drift.

A site whose runners cannot pull from the registry the component defaults name
has to copy every tool image into one they can. Consumers pin the digest and no
pipeline reads this file, which leaves two ways for it to go wrong: an entry
that is not a real reference, and a template pinning a digest that was never
copied. The second is the one that takes every consumer down at once, so it is
checked against templates/, pipelines/ and this repository's own .gitlab-ci.yml
rather than against the catalogue alone.

`tag` is the third. The mirror is pushed under it, because a registry that
garbage-collects untagged manifests reaps a mirror nothing tags, so an entry
with no usable tag is a mirror that deletes itself.

The mirror prefix comes from `.ci/estate.yml`. This repository ships
`docker.io/`, meaning the components pin upstream and nothing is mirrored, so
the catalogue is empty and the drift checks say so instead of failing. Point
the prefix at your own registry, add a row per tool image, and they enforce.

As in test_runner_images.py, the rejections are asserted against synthetic
documents. A test that only walked the shipped file would pass whatever the
schema said.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = REPO_ROOT / "images" / "mirrored-images.yml"
SCHEMA = Path(__file__).parent / "mirrored-images.schema.json"
CONSUMERS = REPO_ROOT / ".ci" / "consumers.yml"
ESTATE = REPO_ROOT / ".ci" / "estate.yml"

# The approved pull path for a tool image, as the estate profile records it.
# Every mirror entry lives under it, and it is what the drift check filters the
# pinned references by.
MIRROR_PREFIX = yaml.safe_load(ESTATE.read_text())["images"]["mirror_prefix"]
MIRRORING = MIRROR_PREFIX != "docker.io/"

# Every place a job image is named: the component defaults, the contract that
# restates each one, the composition defaults that restate them again, and the
# jobs this repository runs on itself.
PINNED_REFERENCE = re.compile(r"(?P<repository>[a-z0-9][a-z0-9.:/_-]*)@(?P<digest>sha256:[0-9a-f]{64})")

VALID_ENTRY = {
    "name": "anchore--syft",
    "source": "docker.io/anchore/syft",
    "repository": MIRROR_PREFIX + "anchore--syft",
    "digest": "sha256:" + "ab" * 32,
    "tag": "v1.20.0-debug",
    "verified_at": "2026/09/21",
}


def schema() -> dict:
    return json.loads(SCHEMA.read_text())


def catalogue() -> dict:
    return yaml.safe_load(CATALOGUE.read_text())


def consumer_projects() -> set[str]:
    document = yaml.safe_load(CONSUMERS.read_text())
    return {consumer["project"] for consumer in document["consumers"]}


def document(*images: dict) -> dict:
    return {"schema_version": 1, "images": [copy.deepcopy(image) for image in images]}


def errors(doc: dict) -> list[str]:
    return [error.message for error in Draft202012Validator(schema()).iter_errors(doc)]


def pinned_files() -> list[Path]:
    files = sorted((REPO_ROOT / "templates").rglob("*.yml"))
    files += sorted((REPO_ROOT / "pipelines").rglob("*.yml"))
    files.append(REPO_ROOT / ".gitlab-ci.yml")
    return files


# --- the shipped file --------------------------------------------------------


def test_the_schema_itself_is_a_valid_schema():
    Draft202012Validator.check_schema(schema())


def test_the_catalogue_matches_its_schema():
    assert errors(catalogue()) == []


def test_the_catalogue_says_no_pipeline_reads_it():
    """The header is the only thing telling a reader the file is inert, and the
    only place the naming rule is written down."""
    header = CATALOGUE.read_text()
    assert "NO PIPELINE READS THIS FILE" in header
    assert "tests/contracts/test_mirrored_images.py" in header
    # The copy has to preserve the digest or every pin in the library changes
    # meaning at the mirror, so the header states the flag rather than assuming
    # whoever fills the catalogue in knows it.
    assert "--preserve-digests" in header


def test_no_pipeline_reads_the_catalogue():
    """If that changes, the claim above has to change with it.

    A comment pointing at the file is fine and there are several; what must not
    appear is a job reading it, which is why only uncommented lines count.
    """
    readers = []
    for path in pinned_files() + sorted((REPO_ROOT / "runtime").rglob("*.py")):
        for line in path.read_text().splitlines():
            if "mirrored-images.yml" in line and not line.lstrip().startswith("#"):
                readers.append(f"{path.relative_to(REPO_ROOT)}: {line.strip()}")
    assert readers == []


def test_a_valid_entry_is_accepted():
    """Positive control: the rejections below mean nothing if a correct entry
    is refused too."""
    assert errors(document(VALID_ENTRY)) == []


# --- the naming rule ---------------------------------------------------------


def outside_prefix(doc: dict) -> list[str]:
    """Entries whose repository is not the name under the configured prefix.

    The schema cannot check this: the prefix is a fact about one estate and the
    schema is shipped for all of them. Two fields state one thing here, so they
    are asserted to agree.
    """
    return [
        image["repository"]
        for image in doc["images"]
        if image["repository"] != MIRROR_PREFIX + image["name"]
    ]


def test_the_repository_is_the_name_under_the_mirror_prefix():
    assert outside_prefix(catalogue()) == []


def test_a_repository_outside_the_mirror_prefix_is_caught():
    """A mirror the runners would not be allowed to pull is not a mirror."""
    assert outside_prefix(document(dict(VALID_ENTRY, repository="elsewhere.example.com/anchore--syft")))
    assert outside_prefix(document(dict(VALID_ENTRY, repository=MIRROR_PREFIX + "other/anchore--syft")))


def test_a_name_carrying_a_slash_is_refused():
    """The flattening exists because some registries take one level of name.
    A slash in `name` means the flattening was not applied."""
    assert errors(document(dict(VALID_ENTRY, name="anchore/syft")))


def test_a_flattened_name_recovers_its_upstream_path():
    """The double dash is what makes the flattening reversible, which is how a
    version bot gets from the mirror back to the upstream datasource."""
    for image in catalogue()["images"]:
        upstream = image["source"].split("/", 1)[1]
        assert image["name"] == upstream.replace("/", "--"), image
        assert image["name"].replace("--", "/") == upstream, image


# --- drift: a digest that is not one -----------------------------------------


@pytest.mark.parametrize(
    "digest",
    [
        "1.20.0",
        "latest",
        "sha256:" + "ab" * 31,
        "sha256:" + "ab" * 33,
        "sha256:" + "AB" * 32,
        "sha256:",
        "",
        "@sha256:" + "ab" * 32,
        "quay.example.com/ci/anchore--syft@sha256:" + "ab" * 32,
    ],
)
def test_a_digest_that_is_not_sha256_and_64_hex_is_refused(digest: str):
    assert errors(document(dict(VALID_ENTRY, digest=digest))), f"{digest!r} was accepted"


def test_a_source_carrying_a_tag_or_a_digest_is_refused():
    """The digest field is the identity; a source that also carries one is two
    claims that can disagree, and the script would copy the wrong manifest."""
    assert errors(document(dict(VALID_ENTRY, source="docker.io/anchore/syft:v1.20.0")))
    assert errors(
        document(dict(VALID_ENTRY, source="docker.io/anchore/syft@sha256:" + "ab" * 32))
    )


# --- drift: a tag the mirror cannot be kept alive by ------------------------
#
# The push is `skopeo copy ... docker://<repository>:<tag>`. An untagged
# manifest is unreferenced and Quay's garbage collector removes it, so the tag
# is not decoration and the schema refuses an entry that cannot supply one.


@pytest.mark.parametrize("tag", ["", " ", "-1.20.0", ".1.20.0", "v1.20.0 ", "a" * 128])
def test_a_tag_that_is_not_a_tag_is_refused(tag: str):
    assert errors(document(dict(VALID_ENTRY, tag=tag))), f"{tag!r} was accepted"


def test_an_entry_with_no_tag_is_refused():
    """Nothing would reference the manifest the copy pushed, and the collector
    takes it within minutes."""
    entry = dict(VALID_ENTRY)
    del entry["tag"]
    assert errors(document(entry))


@pytest.mark.parametrize("tag", ["3854e246-6829", "abcdef1-1", "0123456789ab-70"])
def test_a_tag_the_ci_org_auto_prunes_is_refused(tag: str):
    """The org's only auto-prune policy is creation_date 90d on tagPattern
    ^[0-9a-f]{7,12}-[0-9]+$. A mirror tagged that way is collected after 90
    days, which is the same outage as never tagging it, later."""
    assert errors(document(dict(VALID_ENTRY, tag=tag))), f"{tag!r} was accepted"


def test_the_shipped_tags_are_not_auto_pruned():
    """The catalogue as shipped, against the same policy."""
    pruned = re.compile(r"^[0-9a-f]{7,12}-[0-9]+$")
    assert [
        f"{image['name']}:{image['tag']}"
        for image in catalogue()["images"]
        if pruned.match(image["tag"])
    ] == []


def test_an_unknown_field_is_refused():
    assert errors(document(dict(VALID_ENTRY, mirrored_by="somebody")))


def test_a_missing_field_is_refused():
    for field in ("name", "source", "repository", "digest", "tag", "verified_at"):
        entry = dict(VALID_ENTRY)
        del entry[field]
        assert errors(document(entry)), field


# --- drift: the same image twice ---------------------------------------------
#
# JSON Schema cannot express "unique by a pair of properties", so this is a
# check of its own. The name alone is not unique: ci/alpine holds two digests.


def duplicates(doc: dict) -> list[str]:
    seen: dict[tuple[str, str], int] = {}
    for image in doc.get("images", []):
        key = (image.get("name"), image.get("digest"))
        seen[key] = seen.get(key, 0) + 1
    return sorted(f"{name}@{digest}" for (name, digest), count in seen.items() if count > 1)


def test_the_catalogue_records_each_image_and_digest_once():
    assert duplicates(catalogue()) == []


def test_a_repeated_name_and_digest_is_caught():
    assert duplicates(document(VALID_ENTRY, VALID_ENTRY)) == [
        "anchore--syft@sha256:" + "ab" * 32
    ]


def test_one_name_under_two_digests_is_not_drift():
    """Two components can pin two releases of one image, and they share a Quay
    repository. That is the shipped shape for alpine."""
    other = dict(VALID_ENTRY, digest="sha256:" + "cd" * 32, tag="v1.21.0-debug")
    assert duplicates(document(VALID_ENTRY, other)) == []
    assert errors(document(VALID_ENTRY, other)) == []


# --- drift: a pin with no mirror behind it -----------------------------------


def test_every_image_this_repository_pins_was_mirrored():
    """The failure this file exists to prevent. A digest bumped in a template
    but not here is a pull the runners refuse, on every consumer at once."""
    if not MIRRORING:
        pytest.skip("mirror_prefix is the upstream registry; nothing is mirrored")
    mirrored = {
        f"{image['repository']}@{image['digest']}" for image in catalogue()["images"]
    }
    missing = []
    for path in pinned_files():
        for match in PINNED_REFERENCE.finditer(path.read_text()):
            reference = match.group(0)
            if not reference.startswith(MIRROR_PREFIX):
                continue
            if reference not in mirrored:
                missing.append(f"{path.relative_to(REPO_ROOT)}: {reference}")
    assert not missing, "pinned but not in images/mirrored-images.yml:\n  " + "\n  ".join(missing)


def test_no_entry_is_unused():
    """An entry nothing pins is an image the mirror keeps warm for nobody.

    `pinned_by` is the exemption: that entry is pinned in a consumer's own
    pipeline, which this repository does not hold and cannot grep.
    """
    pinned = set()
    for path in pinned_files():
        pinned.update(match.group(0) for match in PINNED_REFERENCE.finditer(path.read_text()))
    unused = [
        f"{image['repository']}@{image['digest']}"
        for image in catalogue()["images"]
        if not image.get("pinned_by")
        and f"{image['repository']}@{image['digest']}" not in pinned
    ]
    assert not unused, "mirrored but pinned nowhere:\n  " + "\n  ".join(unused)


# --- drift: a consumer-pinned entry naming a project nobody tracks -----------
#
# `pinned_by` buys an entry out of the check above, so the claim it makes has
# to be checked itself. A project not in .ci/consumers.yml is a project no
# release bump reaches, and an entry pointing at one is an exemption granted to
# nobody.


def unlisted_projects(doc: dict, listed: set[str]) -> list[str]:
    return sorted(
        f"{image['name']}@{image['digest'][:14]}: {project}"
        for image in doc.get("images", [])
        for project in image.get("pinned_by", [])
        if project not in listed
    )


def test_every_pinned_by_project_is_a_known_consumer():
    assert unlisted_projects(catalogue(), consumer_projects()) == []


def test_a_pinned_by_project_that_is_not_a_consumer_is_caught():
    entry = dict(VALID_ENTRY, pinned_by=["platform/not-a-consumer"])
    assert unlisted_projects(document(entry), consumer_projects()) == [
        "anchore--syft@sha256:abababa: platform/not-a-consumer"
    ]


def test_a_pinned_by_project_that_is_a_consumer_is_accepted():
    """The other direction: the exemption has to work, or nothing can use it."""
    project = sorted(consumer_projects())[0]
    entry = dict(VALID_ENTRY, pinned_by=[project])
    assert errors(document(entry)) == []
    assert unlisted_projects(document(entry), consumer_projects()) == []


@pytest.mark.parametrize(
    "pinned_by",
    [
        [],                                       # present but empty
        ["platform/docs-site", "platform/docs-site"],  # the same project twice
        [""],                                     # an empty path
        ["docs-site"],                            # no namespace
        ["Platform/Docs-Site"],                   # a GitLab path is lower case
        "platform/docs-site",                     # a string, not a list
    ],
)
def test_a_pinned_by_that_is_not_a_list_of_project_paths_is_refused(pinned_by):
    assert errors(document(dict(VALID_ENTRY, pinned_by=pinned_by))), f"{pinned_by!r} was accepted"


def test_an_entry_without_pinned_by_is_still_accepted():
    """The field is optional: most mirrors are pinned by a template here."""
    assert errors(document(VALID_ENTRY)) == []
    assert "pinned_by" not in VALID_ENTRY
