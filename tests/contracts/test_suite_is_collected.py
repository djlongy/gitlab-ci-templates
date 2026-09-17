"""No test directory may carry a name pytest walks past.

`tests/runtime/build/` was added with fifteen passing tests and the suite total
did not move: `build` matches pytest's default `norecursedirs`, so the directory
was skipped in every run, locally and in CI, with no message at all. A test that
is never collected is indistinguishable from a test that passes.

The patterns are read from the running pytest rather than copied here, so a
version that changes them changes this check with it.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]


def test_no_test_directory_is_silently_skipped(pytestconfig):
    patterns = pytestconfig.getini("norecursedirs")
    assert "build" in patterns, patterns
    offenders = sorted(
        str(d.relative_to(TESTS_DIR.parent))
        for d in TESTS_DIR.rglob("*")
        if d.is_dir()
        and d.name != "__pycache__"
        and any(fnmatch.fnmatch(d.name, p) for p in patterns)
    )
    assert offenders == [], (
        f"pytest walks past {offenders}; every test under them is never collected"
    )
