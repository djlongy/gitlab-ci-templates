"""Every pinned dependency in this repository is visible to a Renovate manager.

Renovate fails silently in both directions: a dependency no manager extracts is
never updated, and the run that skipped it is green. `renovate-config-validator`
checks that the config parses, not that it sees anything, so this test does the
part that matters — it applies each custom manager's own regexes to the files
that manager claims, then asserts that every pin in the repository was matched
by one of them.

Adding the 21st digest pin, or a tool download in a shape the regexes do not
cover, fails here rather than becoming a dependency nobody updates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RENOVATE = REPO / "renovate.json"

# A digest pin whose registry no public datasource can resolve, with the reason.
# Renovate would log "found no results from datasource" at INFO and move on, so
# an entry here is a deliberate exclusion, not an oversight.
UNTRACKABLE = {
    # The example estate-built execution environment. An image your own estate
    # builds and publishes has no upstream to look a version up against, so no
    # datasource can resolve it and Renovate would log "found no results" at
    # INFO and move on. An entry here is a deliberate exclusion.
    "registry.example.com/shared/base/ansible-ee",
}

DIGEST_PIN = re.compile(r"(?P<ref>[A-Za-z0-9][^\s'\"@]*)@(?P<digest>sha256:[0-9a-f]{64})")
GITHUB_DOWNLOAD = re.compile(r"https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/releases/download/\S+")
VERSION_CONSTANT = re.compile(r"^[A-Z][A-Z0-9_]*_VERSION = \"(v?[0-9]+\.[0-9]+\.[0-9]+)\"", re.M)


def _to_python(pattern: str) -> str:
    """Renovate regexes are JavaScript; `re` spells a named group differently.

    JS writes `(?<name>...)`, Python writes `(?P<name>...)`. Lookbehind, `(?<=`
    and `(?<!`, is spelled the same in both and must not be rewritten.
    """
    return re.sub(r"\(\?<(?![=!])", "(?P<", pattern)


def _config() -> dict:
    return json.loads(RENOVATE.read_text())


def _managers() -> list[dict]:
    return _config()["customManagers"]


def _file_patterns(manager: dict) -> list[re.Pattern]:
    """Compile a manager's file patterns.

    Renovate accepts a glob or a `/regex/` string. Every pattern in this repo is
    the regex form; a glob would need different handling and this test says so
    rather than pretending to interpret one.
    """
    patterns = manager.get("managerFilePatterns") or manager.get("fileMatch") or []
    compiled = []
    for pattern in patterns:
        assert pattern.startswith("/") and pattern.endswith("/"), (
            f"non-regex managerFilePattern {pattern!r}; this test only reads the /regex/ form"
        )
        compiled.append(re.compile(_to_python(pattern[1:-1])))
    assert compiled, "a custom manager with no file patterns matches nothing"
    return compiled


def _tracked_files() -> list[Path]:
    """Every file a manager could plausibly claim, as repo-relative paths."""
    roots = ("templates", "runtime", "pipelines")
    files = []
    for root in roots:
        for path in sorted((REPO / root).rglob("*")):
            if path.is_file() and path.suffix in {".yml", ".yaml", ".py", ".sh"}:
                files.append(path)
    return files


def _matches_for(manager: dict) -> dict[Path, list[re.Match]]:
    """Run one manager's matchStrings over the files its patterns claim."""
    patterns = _file_patterns(manager)
    strings = [re.compile(_to_python(s)) for s in manager["matchStrings"]]
    found: dict[Path, list[re.Match]] = {}
    for path in _tracked_files():
        rel = path.relative_to(REPO).as_posix()
        if not any(p.search(rel) for p in patterns):
            continue
        text = path.read_text()
        hits = [m for s in strings for m in s.finditer(text)]
        if hits:
            found[path] = hits
    return found


def _covered_spans() -> dict[Path, list[tuple[int, int]]]:
    """Every (start, end) span any manager matched, per file."""
    spans: dict[Path, list[tuple[int, int]]] = {}
    for manager in _managers():
        for path, hits in _matches_for(manager).items():
            spans.setdefault(path, []).extend((m.start(), m.end()) for m in hits)
    return spans


def _is_covered(spans: list[tuple[int, int]], match: re.Match) -> bool:
    """Did any manager match overlap this pin?

    Overlap, not containment: the release-download managers deliberately stop at
    the tag, because the filename after it is not part of the dependency.
    """
    return any(start < match.end() and match.start() < end for start, end in spans)


def test_the_config_parses_and_declares_custom_managers():
    config = _config()
    assert config["customManagers"], "no custom managers: nothing in this repo is tracked"
    for manager in config["customManagers"]:
        assert manager["customType"] == "regex"
        assert manager.get("description"), "a manager without a description is unreviewable"
        for string in manager["matchStrings"]:
            compiled = re.compile(_to_python(string))
            groups = set(compiled.groupindex)
            assert groups & {"currentValue", "currentDigest"}, (
                f"matchString {string!r} captures no currentValue or currentDigest, "
                "so Renovate has nothing to replace"
            )
            assert "depName" in groups or manager.get("depNameTemplate"), (
                f"matchString {string!r} has no depName and its manager sets no depNameTemplate"
            )


def test_no_manager_is_dead_config():
    """A manager that matches nothing is config that looks like coverage."""
    for manager in _managers():
        assert _matches_for(manager), (
            f"custom manager {manager['description'][:60]!r} matches no file in the repository"
        )


def test_every_digest_pin_is_matched_by_a_manager():
    spans = _covered_spans()
    unseen = []
    for path in _tracked_files():
        text = path.read_text()
        for match in DIGEST_PIN.finditer(text):
            if match.group("ref") in UNTRACKABLE:
                continue
            if not _is_covered(spans.get(path, []), match):
                unseen.append(f"{path.relative_to(REPO)}: {match.group('ref')}")
    assert not unseen, (
        "digest pins no Renovate manager extracts:\n  " + "\n  ".join(unseen)
    )


def test_every_pinned_release_download_is_matched_by_a_manager():
    spans = _covered_spans()
    unseen = []
    for path in _tracked_files():
        text = path.read_text()
        for match in GITHUB_DOWNLOAD.finditer(text):
            if not _is_covered(spans.get(path, []), match):
                unseen.append(f"{path.relative_to(REPO)}: {match.group(0)[:90]}")
    assert not unseen, (
        "pinned release downloads no Renovate manager extracts:\n  " + "\n  ".join(unseen)
    )


def test_every_version_constant_is_matched_by_a_manager():
    spans = _covered_spans()
    unseen = []
    for path in _tracked_files():
        text = path.read_text()
        for match in VERSION_CONSTANT.finditer(text):
            if not _is_covered(spans.get(path, []), match):
                unseen.append(f"{path.relative_to(REPO)}: {match.group(0).strip()}")
    assert not unseen, (
        "pinned tool versions no Renovate manager extracts:\n  " + "\n  ".join(unseen)
    )


def test_the_untrackable_list_is_not_stale():
    """An exclusion that no longer appears in the repo is a lie about coverage."""
    corpus = "\n".join(path.read_text() for path in _tracked_files())
    for ref in UNTRACKABLE:
        assert ref in corpus, f"{ref} is excluded from tracking but no longer pinned anywhere"


@pytest.mark.parametrize(
    "expected",
    [
        "aquasec/trivy",
        "anchore/syft",
        "zricethezav/gitleaks",
        "hashicorp/terraform",
        "cgr.dev/chainguard/static",
    ],
)
def test_known_images_resolve_to_a_lookup_name_without_a_private_registry_prefix(expected):
    """A private registry prefix must not reach the datasource.

    The lookup name is what Renovate asks the datasource about. A private
    registry the bot cannot authenticate to answers nothing, and the run that
    found nothing is green — the dependency simply stops being updated. Capture
    the upstream name, rewrite the pin wherever you pull it from.
    """
    names = set()
    for manager in _managers():
        if manager.get("datasourceTemplate") != "docker":
            continue
        for hits in _matches_for(manager).values():
            names.update(m.group("depName") for m in hits)
    assert expected in names, f"{expected} is not among the docker lookup names: {sorted(names)}"
    private = sorted(n for n in names if n.startswith("registry."))
    assert not private, f"a docker lookup name still carries a registry host: {private}"
