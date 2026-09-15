"""Loader and HTTP double for the runtime helper tests.

The helpers are scripts a job materialises, not an installed package, so tests
import them from their path rather than by name. Loading the real file is
deliberate: these suites exercise the bytes runtime/<domain>/materialise.yml
ships.

Not a conftest: a conftest is imported by pytest for its fixtures, and importing
one directly as a module gives two copies of everything in it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = REPO_ROOT / "runtime"


def load_runtime_module(relative: str):
    """Import runtime/<relative> under a name derived from its path."""
    path = RUNTIME / relative
    name = "ci_runtime_" + relative.replace("/", "_").removesuffix(".py")
    if str(RUNTIME) not in sys.path:
        sys.path.insert(0, str(RUNTIME))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    """Stands in for httpjson.Response without an HTTP layer."""

    def __init__(self, status: int, payload=None, body: bytes | None = None):
        self.status = status
        if body is not None:
            self.body = body
        else:
            self.body = json.dumps(payload if payload is not None else {}).encode()

    @property
    def text(self) -> str:
        return self.body.decode()

    def json(self):
        try:
            return json.loads(self.body)
        except ValueError as error:
            raise RuntimeError(f"not JSON: {error}") from error

    def ok(self) -> bool:
        return 200 <= self.status < 300
