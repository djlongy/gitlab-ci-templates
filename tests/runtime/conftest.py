"""Run a component's embedded shell under a stub toolchain.

These suites execute the script the TEMPLATE carries, not the file under
runtime/. The embedding is where shell changes meaning - a literal newline or a
heredoc terminator gains the YAML block scalar's indentation - so a source file
that behaves correctly proves nothing about the job. Both bugs were real here
and were found this way.

Stubs, not the real tools: the point is the wrapper's decisions - which exit
code stands, which failure is hidden, what is written - and a stub lets a test
name the tool's outcome instead of arranging for it. The real tools were
exercised separately in their pinned images; that evidence is in each
contract.yml. `sha256sum` is a thin wrapper over macOS `shasum -a 256` rather
than a stub, so the checksum verification under test is the real one.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_embedder():
    spec = importlib.util.spec_from_file_location(
        "embed_generate", REPO_ROOT / "runtime" / "embed" / "generate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


embed = _load_embedder()

# A stub records how it was called and returns what the test told it to.
STUB = """#!/bin/sh
printf '%s' "$0" >>"$CALL_LOG"
for arg in "$@"; do printf ' %s' "$arg" >>"$CALL_LOG"; done
printf '\\n' >>"$CALL_LOG"
_name=$(basename "$0")
if [ -f "$STUB_DIR/$_name.stdout" ]; then cat "$STUB_DIR/$_name.stdout"; fi
if [ -f "$STUB_DIR/$_name.stderr" ]; then cat "$STUB_DIR/$_name.stderr" >&2; fi
if [ -f "$STUB_DIR/$_name.$1.exit" ]; then exit "$(cat "$STUB_DIR/$_name.$1.exit")"; fi
if [ -f "$STUB_DIR/$_name.exit" ]; then exit "$(cat "$STUB_DIR/$_name.exit")"; fi
exit 0
"""

# macOS has shasum, not sha256sum. A wrapper keeps the real hashing, so the
# checksum verification in toolchain.sh is genuinely under test.
SHA256SUM = """#!/bin/sh
if command -v /usr/bin/shasum >/dev/null 2>&1; then exec /usr/bin/shasum -a 256 "$@"; fi
exec /usr/bin/sha256sum "$@"
"""


@dataclass
class Run:
    returncode: int
    stdout: str
    stderr: str
    calls: list[str]

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def called(self, needle: str) -> bool:
        return any(needle in call for call in self.calls)


class Harness:
    """A checkout, a stub PATH, and the ability to run one embedded block."""

    def __init__(self, tmp_path: Path):
        self.project_dir = tmp_path / "checkout"
        self.project_dir.mkdir()
        self.stub_dir = tmp_path / "stubs"
        self.stub_dir.mkdir()
        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        self.call_log = tmp_path / "calls.log"
        self.call_log.touch()
        self.env: dict[str, str] = {}
        self.write_stub("sha256sum", SHA256SUM)

    def write_stub(self, name: str, body: str = STUB) -> None:
        path = self.bin_dir / name
        path.write_text(body)
        path.chmod(0o755)

    def stub(self, name: str, *, exit_code: int = 0, stdout: str = "", stderr: str = "",
             subcommand_exits: dict[str, int] | None = None) -> None:
        self.write_stub(name)
        if exit_code:
            (self.stub_dir / f"{name}.exit").write_text(str(exit_code))
        if stdout:
            (self.stub_dir / f"{name}.stdout").write_text(stdout)
        if stderr:
            (self.stub_dir / f"{name}.stderr").write_text(stderr)
        for subcommand, code in (subcommand_exits or {}).items():
            (self.stub_dir / f"{name}.{subcommand}.exit").write_text(str(code))

    def file(self, relative: str, content: str = "") -> Path:
        path = self.project_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def run(self, template: str, *sources: str, then: str = "") -> Run:
        """Concatenate the named embedded blocks of `template` and run them.

        `then` appends a caller-supplied driver, which is how the function
        library in toolchain.sh is exercised on its own.
        """
        blocks = embed.embedded_blocks(REPO_ROOT / "templates" / template / "template.yml")
        missing = [source for source in sources if source not in blocks]
        assert not missing, f"{template} has no embedded block for {missing}"
        script = "\n".join(blocks[source] for source in sources) + "\n" + then

        environment = {
            "PATH": f"{self.bin_dir}:/usr/bin:/bin",
            "HOME": str(self.project_dir),
            "CI_PROJECT_DIR": str(self.project_dir),
            "STUB_DIR": str(self.stub_dir),
            "CALL_LOG": str(self.call_log),
            **self.env,
        }
        completed = subprocess.run(
            ["/bin/sh", "-c", script],
            cwd=self.project_dir,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return Run(
            completed.returncode,
            completed.stdout,
            completed.stderr,
            self.call_log.read_text().splitlines(),
        )


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


@pytest.fixture
def chart(harness: Harness):
    """Create a minimal chart directory; content is irrelevant to a stubbed helm."""

    def _chart(path: str, *, name: str = "demo", version: str = "1.0.0",
               dependencies: bool = False, lock: bool = False) -> str:
        body = f"apiVersion: v2\nname: {name}\nversion: {version}\n"
        if dependencies:
            body += "dependencies:\n  - name: other\n    version: 1.0.0\n"
        harness.file(f"{path}/Chart.yaml", body)
        if lock:
            harness.file(f"{path}/Chart.lock", "dependencies: []\n")
        return path

    return _chart
