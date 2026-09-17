"""What the two grype components do to their environment and their argv.

O4 of the runner-images design: `security-image-grype` ran `/grype db update`
unconditionally and exposed no database input, so an air-gapped estate could not
express where the database comes from, and the component was kept off the path
for that reason alone. The trivy components got `db-repository` in 1.2.0.

Grype's database is not an OCI artifact, so the counterpart is a URL. Read from
grype v0.118.0, the version both components pin:

  cmd/grype/cli/options/database.go   `update-url` -> GRYPE_DB_UPDATE_URL,
                                      `auto-update` -> GRYPE_DB_AUTO_UPDATE,
                                      `require-update-check`
  grype/db/v6/distribution/config.go  LatestURL defaults to
                                      https://grype.anchore.io/databases
  cmd/grype/cli/commands/db_import.go `import FILE | URL`, with a `checksum`
                                      query parameter verified against the
                                      archive

Both templates' own script entries are executed here against a stub `/grype`, so
what is asserted is what the component does rather than a copy of it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPONENTS = {
    "security-image-grype": REPO_ROOT / "templates" / "security-image-grype" / "template.yml",
    "security-filesystem-grype": REPO_ROOT
    / "templates"
    / "security-filesystem-grype"
    / "template.yml",
}


def entries(component: str, key: str) -> list[str]:
    _, jobs = list(yaml.safe_load_all(COMPONENTS[component].read_text()))
    return jobs[f"$[[ inputs.instance ]]:{component}"][key]


def db_env_entry(component: str) -> str:
    found = [e for e in entries(component, "before_script") if "GRYPE_DB_UPDATE_URL" in e]
    assert len(found) == 1, component
    return found[0]


def db_hydrate_entry(component: str) -> str:
    """The part of the script that hydrates the database.

    security-filesystem-grype keeps that in a script entry of its own.
    security-image-grype deliberately runs one shell block, so the region is cut
    out of it between two anchors from that file. A rewrite that moves either
    anchor fails here rather than quietly measuring nothing.
    """
    found = [e for e in entries(component, "script") if "grype db " in e]
    assert len(found) == 1, component
    entry = found[0]
    start = entry.find("# Hydrate the database loudly")
    if start != -1:
        entry = entry[start:]
    end = entry.find("CI_TPL_UNFIXED_FLAG=")
    if end != -1:
        entry = entry[:end]
    assert "grype db " in entry, component
    return entry


def run(component: str, tmp_path: Path, **inputs: str) -> subprocess.CompletedProcess:
    """Run the component's database entries with /grype stubbed.

    The stub records its argv and prints the two lines the templates parse out
    of `grype db status`, so the entry runs to the end instead of stopping on a
    database it cannot see.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "argv"
    stub = bin_dir / "grype"
    stub.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{argv_file}"\n'
        'if [ "$1" = db ] && [ "$2" = status ]; then\n'
        '  echo "Built: 2026-09-16T00:00:00Z"\n'
        '  echo "Status: valid"\n'
        "fi\n"
        "exit 0\n"
    )
    stub.chmod(0o755)

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "CI_TPL_DB_UPDATE_URL": inputs.get("db-update-url", ""),
        "CI_TPL_DB_ARCHIVE": inputs.get("db-archive", ""),
    }
    # The components invoke /grype, the absolute path the anchore images put it
    # at. That one token is repointed at the stub; nothing else in the entry is
    # rewritten.
    script = "\n".join(
        [
            db_env_entry(component),
            db_hydrate_entry(component).replace("/grype ", f"{stub} "),
            'echo "GRYPE_DB_UPDATE_URL=[${GRYPE_DB_UPDATE_URL-unset}]"',
            'echo "GRYPE_DB_AUTO_UPDATE=[${GRYPE_DB_AUTO_UPDATE-unset}]"',
            'echo "GRYPE_DB_REQUIRE_UPDATE_CHECK=[${GRYPE_DB_REQUIRE_UPDATE_CHECK-unset}]"',
        ]
    )
    result = subprocess.run(
        ["/bin/sh", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    result.argv = argv_file.read_text().splitlines() if argv_file.exists() else []  # type: ignore[attr-defined]
    return result


@pytest.mark.parametrize("component", sorted(COMPONENTS))
def test_unset_keeps_todays_behaviour(component: str, tmp_path):
    """No input set: `grype db update`, and not one GRYPE_DB_ variable exported.
    An empty GRYPE_DB_UPDATE_URL would be a URL of "", not an absent setting."""
    result = run(component, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "db update" in result.argv
    assert not any(a.startswith("db import") for a in result.argv)
    assert "GRYPE_DB_UPDATE_URL=[unset]" in result.stdout
    assert "GRYPE_DB_AUTO_UPDATE=[unset]" in result.stdout
    assert "GRYPE_DB_REQUIRE_UPDATE_CHECK=[unset]" in result.stdout


@pytest.mark.parametrize("component", sorted(COMPONENTS))
def test_a_mirror_url_is_exported_and_the_update_still_runs(component: str, tmp_path):
    mirror = "https://artifactory.example.com/grype/databases"
    result = run(component, tmp_path, **{"db-update-url": mirror})
    assert result.returncode == 0, result.stderr
    assert f"GRYPE_DB_UPDATE_URL=[{mirror}]" in result.stdout
    assert "db update" in result.argv
    assert mirror in result.stdout


@pytest.mark.parametrize("component", sorted(COMPONENTS))
def test_an_archive_is_imported_and_the_scan_calls_nothing(component: str, tmp_path):
    """The whole point of O4: with an archive, grype must not reach for the
    listing at scan time either, which is what auto-update would do."""
    archive = "/opt/grype-db/vulnerability-db.tar.zst"
    result = run(component, tmp_path, **{"db-archive": archive})
    assert result.returncode == 0, result.stderr
    assert f"db import {archive}" in result.argv
    assert "db update" not in result.argv
    assert "GRYPE_DB_AUTO_UPDATE=[false]" in result.stdout
    assert "GRYPE_DB_REQUIRE_UPDATE_CHECK=[false]" in result.stdout


@pytest.mark.parametrize("component", sorted(COMPONENTS))
def test_an_archive_beside_a_mirror_url_imports_and_does_not_update(component: str, tmp_path):
    result = run(
        component,
        tmp_path,
        **{
            "db-update-url": "https://artifactory.example.com/grype/databases",
            "db-archive": "https://artifactory.example.com/grype/db.tar.zst?checksum=sha256:"
            + "ab" * 32,
        },
    )
    assert result.returncode == 0, result.stderr
    assert any(a.startswith("db import ") for a in result.argv)
    assert "db update" not in result.argv


@pytest.mark.parametrize("component", sorted(COMPONENTS))
def test_the_inputs_default_to_empty(component: str):
    spec, _ = list(yaml.safe_load_all(COMPONENTS[component].read_text()))
    declared = spec["spec"]["inputs"]
    assert declared["db-update-url"]["default"] == ""
    assert declared["db-archive"]["default"] == ""


@pytest.mark.parametrize("component", sorted(COMPONENTS))
def test_a_failed_import_stops_the_job(component: str, tmp_path):
    """A database that did not load must not become an empty report a gate
    reads as clean, which is the same rule the update path already follows."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "grype"
    stub.write_text('#!/bin/sh\n[ "$1" = db ] && [ "$2" = import ] && exit 1\nexit 0\n')
    stub.chmod(0o755)
    result = subprocess.run(
        [
            "/bin/sh",
            "-c",
            db_env_entry(component)
            + "\n"
            + db_hydrate_entry(component).replace("/grype ", f"{stub} "),
        ],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "CI_TPL_DB_UPDATE_URL": "",
            "CI_TPL_DB_ARCHIVE": "/opt/grype-db/db.tar.zst",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
