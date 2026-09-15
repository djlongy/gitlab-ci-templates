"""`released` is a claim about evidence, not about how finished something looks.

Every component in 1.0.0-rc.1 was `experimental` because nothing had run.
Consumer projects then ran 1.0.0-rc.2 for real, and the statuses moved. The rule
that moved them is the thing worth pinning, because it is the one a future
release will be tempted to bend: a component that looks complete, is fully
tested locally and lints cleanly is still `experimental` until a consumer
pipeline executed its job and that job went green.

Three things a run is NOT: a job GitLab created and nobody started (a manual
apply), a job a rule skipped, and a lint. Each of those exists in this release
and none of them promoted anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = sorted(
    list((REPO_ROOT / "templates").glob("*/contract.yml"))
    + list((REPO_ROOT / "pipelines").glob("*.contract.yml"))
)


def name_of(path: Path) -> str:
    return path.parent.name if path.name == "contract.yml" else path.name[: -len(".contract.yml")]


def contract(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_there_are_contracts_to_check():
    assert len(CONTRACTS) >= 45, [str(p) for p in CONTRACTS]


@pytest.mark.parametrize("path", CONTRACTS, ids=name_of)
def test_released_means_a_consumer_ran_it(path):
    data = contract(path)
    evidence = data["validation"]["evidence"]
    if data["status"] == "released":
        assert "integration-tested" in evidence, (
            f"{name_of(path)} is released without an integration-tested claim"
        )
        environment = data["validation"].get("environment", "")
        assert "pipeline" in environment or "5" in environment, (
            f"{name_of(path)} is released but its environment names no consumer run"
        )
        assert data["validation"].get("revision"), (
            f"{name_of(path)} is released without naming the shared ref it ran on"
        )


@pytest.mark.parametrize("path", CONTRACTS, ids=name_of)
def test_experimental_means_no_green_consumer_run(path):
    """The other direction, which is the one that rots.

    A contract that gains integration-tested evidence and keeps `experimental`
    is a status nobody updated; one that keeps `experimental` with a green run
    behind it is a claim that understates its own evidence. Either way the
    catalogue stops meaning anything.
    """
    data = contract(path)
    if data["status"] == "experimental":
        assert "integration-tested" not in data["validation"]["evidence"], (
            f"{name_of(path)} holds integration-tested evidence and is still experimental"
        )


@pytest.mark.parametrize("path", CONTRACTS, ids=name_of)
def test_experimental_says_why(path):
    """Section 6.3: `experimental` without a reason is indistinguishable from
    nobody having looked."""
    data = contract(path)
    if data["status"] != "experimental":
        return
    prose = " ".join(
        str(data["validation"].get(field, "")) for field in ("notes", "environment")
    ).lower()
    admissions = (
        "status: experimental",
        "has not been executed",
        "have not been executed",
        "nothing in this release has been executed",
        "no pipeline has been run",
        "no pipeline has executed",
        "not been run",
        "evidence is gitlab-linted only",
        "nothing here has been executed",
    )
    assert any(phrase in prose for phrase in admissions), (
        f"{name_of(path)} is experimental and its contract does not say why"
    )


def test_the_release_promoted_something():
    """A positive control: a rule that promoted nothing would pass every test above."""
    released = [name_of(p) for p in CONTRACTS if contract(p)["status"] == "released"]
    assert len(released) >= 12, released
    assert "docs-wiki-sync" in released
    assert "terraform-apply" not in released, "a manual job nobody started is not a run"
    assert "ansible-lint" not in released, "a skipped job is not a run"


def test_the_readme_table_agrees_with_the_contracts():
    """The table is the first thing a consumer reads, and it is hand-written.

    A status that is right in `.ci/catalog.yml` and stale in README.md is worse
    than one that is wrong in both: the reader has no reason to doubt it.
    """
    import re

    readme = (REPO_ROOT / "README.md").read_text()
    row = re.compile(
        r"\| \[`(?P<name>[a-z0-9-]+)`\]\(pipelines/[a-z0-9-]+\.yml\)"
        r" \|[^|]*\|[^|]*\| (?P<status>[a-z]+) \|"
    )
    table = {m.group("name"): m.group("status") for m in row.finditer(readme)}
    assert len(table) >= 11, table

    for name, status in table.items():
        declared = contract(REPO_ROOT / "pipelines" / f"{name}.contract.yml")["status"]
        assert status == declared, f"README says {name} is {status}, its contract says {declared}"
