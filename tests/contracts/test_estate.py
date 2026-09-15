"""Checks on .ci/estate.yml and the rest of the .ci metadata.

The profile is what every later agent reads instead of rediscovering the estate,
so a wrong value here is worse than a missing one. These tests prove three
things: the profile matches its schema, no illustrative endpoint from the
standard was copied into it, and no secret value was written where only a name
belongs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_DIR = REPO_ROOT / ".ci"
ESTATE = CI_DIR / "estate.yml"
SCHEMA = Path(__file__).parent / "estate.schema.json"

# Hosts that must never appear in a profile describing a REAL estate. This
# repository is the public reference, so its own .ci/estate.yml is deliberately
# illustrative and uses them throughout; what the test below protects is the
# opposite direction — that a fork which has filled the profile in has not left
# an illustration behind. `EXAMPLE_PROFILE` is the opt-out, and it is the first
# line a fork deletes.
PLACEHOLDER_HOSTS = ("example.com", "example.org", "example.net")

# Set to False in a fork once .ci/estate.yml describes the estate you run.
EXAMPLE_PROFILE = True

# Shapes that mean somebody pasted a value where a name belongs. Each entry is
# (label, compiled pattern).
SECRET_PATTERNS = [
    ("GitLab personal access token", re.compile(r"glpat-[A-Za-z0-9_-]{16,}")),
    ("GitLab token of any prefix", re.compile(r"\bgl[a-z]{2,8}-[A-Za-z0-9_-]{20,}")),
    ("Vault service token", re.compile(r"\bhvs\.[A-Za-z0-9._-]{20,}")),
    ("Vault legacy token", re.compile(r"\bs\.[A-Za-z0-9]{24,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("PEM private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")),
]


def ci_files() -> list[Path]:
    return sorted(CI_DIR.glob("*.yml"))


def scan_for_secrets(text: str) -> list[str]:
    """Return the label of every secret shape found in text."""
    return [label for label, pattern in SECRET_PATTERNS if pattern.search(text)]


def test_estate_matches_its_schema():
    schema = json.loads(SCHEMA.read_text())
    Draft202012Validator.check_schema(schema)
    profile = yaml.safe_load(ESTATE.read_text())
    errors = sorted(
        Draft202012Validator(schema).iter_errors(profile),
        key=lambda e: list(e.absolute_path),
    )
    assert not errors, "\n".join(
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in errors
    )


@pytest.mark.parametrize("path", ci_files(), ids=lambda p: p.name)
def test_no_placeholder_endpoints(path: Path):
    """Section 4: a profile describing a real estate may not carry illustrations.

    Skipped while EXAMPLE_PROFILE is set, which is the state this repository
    ships in. A fork clears the flag and this becomes the check that its profile
    was actually filled in rather than half-edited.
    """
    if EXAMPLE_PROFILE:
        pytest.skip("this repository ships the illustrative example profile")
    text = path.read_text()
    found = [host for host in PLACEHOLDER_HOSTS if host in text]
    assert not found, f"{path.name} carries illustrative endpoints: {found}"


def test_the_example_profile_really_is_illustrative():
    """Positive control for the flag above.

    EXAMPLE_PROFILE left set on a profile that has been filled in would silence
    the check above for good. While it is set, the profile must actually name an
    illustrative endpoint somewhere.
    """
    if not EXAMPLE_PROFILE:
        pytest.skip("this fork declares a real estate profile")
    text = ESTATE.read_text()
    assert any(host in text for host in PLACEHOLDER_HOSTS), (
        "EXAMPLE_PROFILE is set but .ci/estate.yml names no illustrative "
        "endpoint; clear the flag so the placeholder check runs"
    )


@pytest.mark.parametrize("path", ci_files(), ids=lambda p: p.name)
def test_no_secret_values(path: Path):
    found = scan_for_secrets(path.read_text())
    assert not found, f"{path.name} looks like it carries {found}"


def test_secret_scanner_catches_a_known_token():
    """Positive control.

    A detector that never fires reads exactly like a clean repository. This
    feeds the scanner one synthetic example of every shape it claims to catch
    and fails if any of them slips through. The strings below are invented, not
    issued.
    """
    samples = {
        "GitLab personal access token": "glpat-" + "A" * 20,
        "GitLab token of any prefix": "glrt-" + "B" * 24,
        "Vault service token": "hvs." + "C" * 24,
        "Vault legacy token": "s." + "D" * 24,
        "AWS access key id": "AKIA" + "E" * 16,
        "PEM private key": "-----BEGIN RSA PRIVATE KEY-----",
        "JSON web token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig",
    }
    missed = [label for label, sample in samples.items() if label not in scan_for_secrets(sample)]
    assert not missed, f"the scanner is blind to {missed}"


def test_every_unresolved_field_carries_a_reason():
    """`unresolved` without a reason is indistinguishable from laziness.

    Each line holding the literal `unresolved` must have a comment within the
    three lines above it, or a trailing comment of its own.
    """
    lines = ESTATE.read_text().splitlines()
    bare = []
    for index, line in enumerate(lines):
        if "unresolved" not in line or line.lstrip().startswith("#"):
            continue
        context = lines[max(0, index - 3):index] + [line]
        if not any("#" in candidate for candidate in context):
            bare.append(f"line {index + 1}: {line.strip()}")
    assert not bare, "unresolved values with no stated reason:\n" + "\n".join(bare)


def test_environments_are_named_and_distinct():
    """The list is the estate's own vocabulary, so the names are not asserted.

    What is asserted is that there IS one, that no name repeats, and that each
    is a name a component input can carry. A profile with an empty or duplicated
    environment list resolves an environment input to nothing in particular.
    """
    profile = yaml.safe_load(ESTATE.read_text())
    names = [env["name"] for env in profile["environments"]]
    assert names, "the profile records no environments"
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, f"repeated environment names: {duplicates}"


# --------------------------------------------------------------------------
# Approved execution images
# --------------------------------------------------------------------------

CATALOG = CI_DIR / "catalog.yml"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def catalogue_runtime_images() -> list[tuple[str, str]]:
    """(unit name, reference) for every catalogued unit that names one image.

    A composition's runtime_image is prose, because it forwards a digest-pinned
    image per component rather than running one itself; those are excluded by
    their own `pinning` field rather than by guessing from the string.
    """
    catalog = yaml.safe_load(CATALOG.read_text())
    units = catalog.get("components", []) + catalog.get("pipelines", [])
    return [
        (unit["name"], unit["runtime_image"]["reference"])
        for unit in units
        if unit.get("runtime_image", {}).get("pinning") == "digest"
    ]


def test_the_estate_points_at_the_catalogue_for_approved_images():
    """Section 4 asks for approved images. This says where they are, once.

    A second hand-written list here would drift from the contracts; the
    catalogue is generated from them and cannot.
    """
    images = yaml.safe_load(ESTATE.read_text())["images"]
    assert images != "unresolved"
    assert images["source_of_truth"].startswith(".ci/catalog.yml")
    assert images["mirror_prefix"] == "docker.io/"
    estate_built = images["estate_built"]["ansible-ee"]
    assert estate_built["pinning"] == "digest"
    assert DIGEST.search(estate_built["reference"])
    assert estate_built["reference"].startswith("registry.example.com/shared/base/ansible-ee@")


def test_the_estate_built_image_matches_the_contract_that_runs_it():
    """One digest, recorded twice, so they are asserted to be the same one."""
    contract = yaml.safe_load(
        (REPO_ROOT / "templates" / "ansible-syntax" / "contract.yml").read_text()
    )
    estate = yaml.safe_load(ESTATE.read_text())["images"]["estate_built"]["ansible-ee"]
    assert contract["runtime_image"]["reference"].strip() == estate["reference"]


def test_every_catalogued_runtime_image_is_pinned_by_digest():
    """Section 1: a tag is not a digest, and this is the estate-wide check of it."""
    images = catalogue_runtime_images()
    assert len(images) >= 20, f"only {len(images)} units name an image; the catalogue looks empty"
    unpinned = [(name, reference) for name, reference in images if not DIGEST.search(reference)]
    assert not unpinned, f"not pinned by digest: {unpinned}"


def test_every_third_party_runtime_image_is_pulled_through_the_approved_prefix():
    """An approved image is one your estate has agreed to serve.

    This repository ships `docker.io/`, the upstream. An estate that fronts
    Docker Hub with a pull-through cache sets `mirror_prefix` to that cache and
    rewrites the references; the digests do not change, because a pull-through
    cache serves the upstream manifest unchanged.
    """
    prefix = yaml.safe_load(ESTATE.read_text())["images"]["mirror_prefix"]
    estate_built = "registry.example.com/shared/"
    outside = [
        (name, reference)
        for name, reference in catalogue_runtime_images()
        if not reference.startswith(prefix) and not reference.startswith(estate_built)
    ]
    assert not outside, f"outside the approved prefix: {outside}"


def scanner_entries() -> list[dict]:
    """Every scanner recorded under security.defaults, required and optional."""
    defaults = yaml.safe_load(ESTATE.read_text())["security"]["defaults"]
    return defaults["required_scanners"] + defaults["optional_scanners"]


def test_every_recorded_scanner_is_a_component_in_this_repository():
    """The profile may not name a template that was deleted or never existed.

    The flat per-tool files this block used to name were removed in 1.0.0, and
    a profile that still pointed at them read as an inventory of live paths.
    Every entry now names a component directory, and the file has to be there.
    """
    for entry in scanner_entries():
        template = entry["template"]
        assert template.startswith("templates/"), (
            f"{entry['name']}: template {template!r} points outside templates/"
        )
        assert template.endswith("/template.yml"), f"{entry['name']}: {template!r}"
        assert (REPO_ROOT / template).is_file(), f"{entry['name']}: {template} does not exist"
        assert template == f"templates/{entry['name']}/template.yml", (
            f"{entry['name']}: recorded template {template!r} is a different component"
        )


def test_recorded_scanner_defaults_match_the_components():
    """The recorded behaviour is read back from the component, not asserted.

    This block is the estate's answer to "what gates today". It is only worth
    reading if it cannot drift from the components it describes, so each value
    is compared against the input default it was taken from.
    """
    for entry in scanner_entries():
        spec = next(yaml.safe_load_all((REPO_ROOT / entry["template"]).read_text()))
        inputs = spec["spec"]["inputs"]
        name = entry["name"]

        assert entry["stage"] == inputs["stage"]["default"], f"{name}: stage"
        assert entry["allow_failure"] is False, f"{name}: no component ships allow_failure"

        # policy-mode is the gate. A component without one has no advisory mode
        # and always fails on a finding, so `blocking: true` is the only honest
        # record for it.
        declared = inputs.get("policy-mode", {}).get("default")
        recorded = entry.get("component_default_policy_mode", "blocking" if entry["blocking"] else "advisory")
        if declared is None:
            assert entry["blocking"] is True, f"{name}: no policy-mode input, so it always gates"
        else:
            assert recorded == declared, f"{name}: policy-mode recorded {recorded!r}, component says {declared!r}"

        if "severity-threshold" in inputs:
            assert entry["blocking_severity"] == inputs["severity-threshold"]["default"], (
                f"{name}: blocking_severity"
            )
        if "scanners" in inputs:
            assert entry["scanners"] == inputs["scanners"]["default"], f"{name}: scanners"


def test_recorded_unfixed_handling_matches_the_components():
    """`ignore-unfixed` is a boolean in some components and a string in others."""
    recorded = yaml.safe_load(ESTATE.read_text())["security"]["defaults"]["unfixed_vulnerabilities"]
    by_name = {e["name"]: e for e in scanner_entries()}
    for name, handling in recorded.items():
        assert name in by_name, f"{name} is not a recorded scanner"
        inputs = next(yaml.safe_load_all((REPO_ROOT / by_name[name]["template"]).read_text()))
        default = inputs["spec"]["inputs"]["ignore-unfixed"]["default"]
        ignored = default is True or str(default).lower() == "true"
        assert handling == ("ignored" if ignored else "gated"), (
            f"{name}: recorded {handling!r}, component default is {default!r}"
        )
