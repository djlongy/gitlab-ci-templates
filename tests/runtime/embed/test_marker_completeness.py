"""A vendored Python file's own dependencies must be vendored with it.

An embed marker lists the files a template copies into the job. A Python file in
that list can reach for a sibling module — `import docfilter`, or the
`load("wiki-import")` helper that spec-loads a file by name — and nothing made
the marker list that sibling too. The embedding then succeeds, the drift check
passes, every unit test passes against runtime/ where the sibling is present,
and the job dies at run time on a file that was never copied.

That is RC2-2. templates/docs-wiki-sync/template.yml listed wiki-pull.py and not
the wiki-import.py it loads on line 52; platform/wiki-tpl-flat job 47477 raised
FileNotFoundError for /tmp/ci-tpl-runtime/docs-wiki-sync/wiki-import.py.

This walks each marker's Python sources transitively — a dependency of a
dependency is just as absent at run time — and resolves each name the two ways
the embedded layout makes importable: beside the importing file, and at the
region's root, which is where runtime/httpjson.py lands for the components that
vendor it alongside a module in a subdirectory.

Run: python3 -m pytest -q tests/runtime/embed/test_marker_completeness.py
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

_spec = importlib.util.spec_from_file_location(
    "embed_generate", REPO_ROOT / "runtime" / "embed" / "generate.py"
)
embed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(embed)

# `load("wiki-import")` / `load('wiki-import')`: the spec-loader helper the wiki
# tools use, which takes a file stem rather than a module name.
LOADED = re.compile(r"""\bload\(\s*["']([A-Za-z0-9_-]+)["']\s*\)""")
# A plain top-level import. Only names that resolve to a vendored file matter,
# so a false positive on a stdlib name costs nothing: it resolves to no path.
IMPORTED = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def markers() -> list[tuple[Path, str, list[str]]]:
    """(template path, dest, sources) for every embed region in the repository."""
    found = []
    for path in embed.template_paths():
        for match in embed.regions(path.read_text()):
            mode, dest, *sources = match["spec"].split()
            if mode == "inline":
                continue
            found.append((path, dest, sources))
    return found


MARKERS = markers()
PYTHON_MARKERS = [entry for entry in MARKERS if any(s.endswith(".py") for s in entry[2])]


def test_there_are_markers_with_python_sources():
    """Guard against an empty parametrisation. A regex that matched nothing, or a
    marker format this module stopped understanding, would otherwise read as a
    clean run."""
    assert MARKERS, "no embed regions found; the check below would assert nothing"
    assert PYTHON_MARKERS, "no embed region vendors a Python file"


def dependencies(source: str, base: str) -> set[str]:
    """Repo-relative paths of the vendored modules `source` reaches for."""
    text = (REPO_ROOT / source).read_text()
    here = Path(source).parent
    wanted = set()
    for name in LOADED.findall(text) + IMPORTED.findall(text):
        for candidate in (here / f"{name}.py", Path(base) / f"{name}.py"):
            if (REPO_ROOT / candidate).is_file():
                wanted.add(str(candidate))
    return wanted


@pytest.mark.parametrize(
    "template,dest,sources",
    PYTHON_MARKERS,
    ids=lambda value: value.parent.name if isinstance(value, Path) else "",
)
def test_every_module_a_vendored_file_loads_is_vendored_too(template, dest, sources):
    base = os.path.commonpath([str(Path(source).parent) for source in sources])
    listed = set(sources)
    seen: set[str] = set()
    pending = [source for source in sources if source.endswith(".py")]
    while pending:
        source = pending.pop()
        if source in seen:
            continue
        seen.add(source)
        for wanted in dependencies(source, base):
            assert wanted in listed, (
                f"{template.relative_to(REPO_ROOT)}: {source} loads {wanted}, "
                f"which the embed marker does not list. The job would copy "
                f"{source} into {dest} and die there on the missing file."
            )
            pending.append(wanted)
