"""The RKE2 release manifest parser in runtime/mirror/release_list.py.

The manifest is consumer-supplied data read without a YAML library, so the tests
that matter are about what it refuses. A manifest missing a field used to reach
the shell as a Python traceback and a half-resolved release; now it names the
field and exits 1, and the list job writes nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_helpers import load_runtime_module  # noqa: E402

release_list = load_runtime_module("mirror/release_list.py")

COMPLETE = """
# RKE2 release this project mirrors
version: v1.31.5+rke2r1
arch: amd64
lists:
  - core
  - canal
"""


def run(tmp_path: Path, text: str, capsys) -> tuple[int, str, str]:
    manifest = tmp_path / "rke2.yaml"
    manifest.write_text(text)
    code = release_list.main(["release_list.py", str(manifest)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_complete_manifest_prints_one_splittable_line(tmp_path, capsys):
    """The calling shell reads it with `read -r version arch lists`."""
    code, out, _ = run(tmp_path, COMPLETE, capsys)
    assert code == 0
    assert out.split() == ["v1.31.5+rke2r1", "amd64", "core", "canal"]


def test_comments_and_blank_lines_are_ignored(tmp_path, capsys):
    code, out, _ = run(
        tmp_path,
        "version: v1.0.0   # the release\n\narch: arm64\nlists:\n  - core  # only this one\n",
        capsys,
    )
    assert code == 0
    assert out.split() == ["v1.0.0", "arm64", "core"]


def test_an_unknown_key_is_kept_rather_than_rejected(tmp_path, capsys):
    """A consumer may keep notes beside the three fields this reads."""
    code, out, _ = run(tmp_path, COMPLETE + "note: reviewed 2026/09/01\n", capsys)
    assert code == 0
    assert out.split()[0] == "v1.31.5+rke2r1"


@pytest.mark.parametrize(
    "missing, text",
    [
        ("version", "arch: amd64\nlists:\n  - core\n"),
        ("arch", "version: v1.0.0\nlists:\n  - core\n"),
        ("lists", "version: v1.0.0\narch: amd64\n"),
    ],
)
def test_a_missing_field_names_itself_and_fails(tmp_path, capsys, missing, text):
    code, out, err = run(tmp_path, text, capsys)
    assert code == 1
    assert missing in err
    assert out == "", "nothing may be printed for a manifest that did not parse"


def test_a_key_with_an_empty_value_counts_as_missing(tmp_path, capsys):
    """`version:` with nothing after it is the shape a half-finished edit leaves."""
    code, _, err = run(tmp_path, "version:\narch: amd64\nlists:\n  - core\n", capsys)
    assert code == 1
    assert "version" in err


def test_an_empty_list_entry_fails(tmp_path, capsys):
    """A bare `-` would build the URL rke2-images-.amd64.txt and fetch a 404."""
    code, _, err = run(tmp_path, "version: v1.0.0\narch: amd64\nlists:\n  - core\n  - \n", capsys)
    assert code == 1
    assert "empty entry" in err


def test_a_line_that_is_neither_key_nor_entry_fails(tmp_path, capsys):
    code, _, err = run(tmp_path, "version: v1.0.0\narch: amd64\nlists:\n  - core\nnonsense\n", capsys)
    assert code == 1
    assert "neither a key nor a list entry" in err


def test_a_manifest_that_is_not_there_fails(tmp_path, capsys):
    code = release_list.main(["release_list.py", str(tmp_path / "absent.yaml")])
    captured = capsys.readouterr()
    assert code == 1
    assert "cannot read release manifest" in captured.err


def test_the_wrong_number_of_arguments_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exit_code:
        release_list.main(["release_list.py"])
    assert exit_code.value.code == 2
    assert "usage:" in capsys.readouterr().err
