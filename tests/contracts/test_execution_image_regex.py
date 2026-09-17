"""Every component's execution-image input takes a mirror-prefix variable.

Every default names one registry, so a site that mirrors these images
somewhere else would otherwise override 37 inputs to say one thing. GitLab
expands a CI variable in `image:`, so one group variable can carry the prefix
and each input keeps its digest.

The old `^.+@sha256:...` admitted the variable form by accident, because `.+`
admits everything. These cases say it on purpose, and the rejections are the
part that has to survive the change: a component that quietly started accepting
a bare tag would be pinning nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted(p.parent.name for p in REPO_ROOT.glob("templates/*/template.yml"))
HEX = "c" * 64
# docs-wiki-sync runs on a shell executor with no image at all, and BuildKit
# accepts a tag for a site whose mirror cannot serve a digest. Both are
# documented exceptions in their own contracts, so the tag cases are asserted
# against the component rather than against every component.
TAG_ACCEPTED = {"container-build-buildkit", "docs-wiki-sync"}
EMPTY_ACCEPTED = {"docs-wiki-sync"}


def execution_image(component: str) -> dict:
    path = REPO_ROOT / "templates" / component / "template.yml"
    return next(yaml.safe_load_all(path.read_text()))["spec"]["inputs"]["execution-image"]


@pytest.mark.parametrize("component", TEMPLATES)
def test_a_mirror_prefix_variable_is_accepted(component: str):
    spec = execution_image(component)
    # Only the registry host is replaced. A default may be host/image or
    # host/namespace/image, and taking two segments off the second form would
    # build a reference to a different image.
    reference = re.sub(r"^[^/]+/", "$CI_TPL_MIRROR/", spec["default"])
    assert reference.startswith("$CI_TPL_MIRROR/"), spec["default"]
    assert re.match(spec["regex"], reference), reference


@pytest.mark.parametrize("component", TEMPLATES)
def test_the_shipped_default_is_still_accepted_and_digest_pinned(component: str):
    spec = execution_image(component)
    assert re.match(spec["regex"], spec["default"])
    assert re.search(r"@sha256:[0-9a-f]{64}$", spec["default"]), (
        "section 1 forbids calling a tag a digest, whatever the input admits"
    )


@pytest.mark.parametrize("component", TEMPLATES)
def test_a_bare_name_with_no_registry_and_no_digest_is_refused(component: str):
    """The form the variable prefix must not open the door to."""
    spec = execution_image(component)
    if component in TAG_ACCEPTED:
        pytest.skip(f"{component} documents a name:tag form in its contract")
    for reference in (f"anchore/syft:v1.33.0", "$CI_TPL_MIRROR/anchore/syft:v1.33.0"):
        assert not re.match(spec["regex"], reference), reference


@pytest.mark.parametrize("component", TEMPLATES)
def test_a_variable_that_is_not_upper_snake_is_refused(component: str):
    """`$path/x@sha256:...` is a path that happens to start with a dollar, not a
    variable reference, and the runner would pass it to the daemon verbatim."""
    spec = execution_image(component)
    for reference in (f"$mirror/anchore/syft@sha256:{HEX}", f"$ /anchore/syft@sha256:{HEX}"):
        assert not re.match(spec["regex"], reference), reference


@pytest.mark.parametrize("component", TEMPLATES)
def test_an_empty_value_is_refused_unless_the_contract_allows_one(component: str):
    spec = execution_image(component)
    accepted = bool(re.match(spec["regex"], ""))
    assert accepted == (component in EMPTY_ACCEPTED)


@pytest.mark.parametrize("component", TEMPLATES)
def test_the_description_says_the_variable_form_is_supported(component: str):
    """An input nobody is told about is not an interface."""
    assert "$UPPER_SNAKE/" in execution_image(component)["description"]
