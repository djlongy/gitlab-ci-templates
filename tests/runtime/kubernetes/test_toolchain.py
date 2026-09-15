"""The pinned-tool installer and the path guard in runtime/kubernetes/toolchain.sh.

Section 12 allows a runtime download only from a pinned immutable source with an
independently verified checksum, and requires a validation error to fail. Every
test here drives the copy embedded in templates/helm-validate/template.yml.

The hashing is real: sha256sum is a wrapper over the platform tool, not a stub.
Only the download is stubbed, because the point is what the wrapper does with
what it got, not whether GitHub serves the file.
"""

from __future__ import annotations

import hashlib
import subprocess
import tarfile
from pathlib import Path

TOOLCHAIN = "runtime/kubernetes/toolchain.sh"
TEMPLATE = "helm-validate"


def make_tool_archive(directory: Path, member: str = "faketool") -> Path:
    """A .tar.gz holding one executable, as a tool release publishes."""
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / member
    binary.write_text("#!/bin/sh\necho i-am-the-tool\n")
    binary.chmod(0o755)
    archive = directory / f"{member}.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(binary, arcname=member)
    return archive


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def serve(harness, files: dict[str, Path]) -> None:
    """A curl stub that copies a local file for each URL it is asked for.

    It also removes wget from PATH's reach by never defining it, so a test can
    tell which fetcher the script chose.
    """
    mapping = "\n".join(
        f'    {url}) cp "{source}" "$dest" ;;' for url, source in files.items()
    )
    harness.write_stub(
        "curl",
        "#!/bin/sh\n"
        'printf "curl %s\\n" "$*" >>"$CALL_LOG"\n'
        "dest=''\n"
        "url=''\n"
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        '    -o) dest=$2; shift 2 ;;\n'
        '    -*) shift ;;\n'
        '    *) url=$1; shift ;;\n'
        "  esac\n"
        "done\n"
        'case "$url" in\n'
        f"{mapping}\n"
        "    *) echo \"curl stub: no mapping for $url\" >&2; exit 22 ;;\n"
        "esac\n",
    )


def install_call(asset_url: str, checksums_url: str, checksums_sha: str,
                 asset_name: str, member: str, bin_dir: str) -> str:
    return (
        "ci_tpl_install_tool faketool "
        f"'{asset_url}' '{checksums_url}' '{checksums_sha}' "
        f"'{asset_name}' {member} '{bin_dir}'\n"
    )


def test_install_extracts_a_tool_whose_checksums_match(harness, tmp_path):
    archive = make_tool_archive(tmp_path / "release")
    checksums = tmp_path / "release" / "CHECKSUMS"
    checksums.write_text(f"{sha256(archive)}  faketool.tar.gz\n")
    serve(harness, {
        "https://example.invalid/faketool.tar.gz": archive,
        "https://example.invalid/CHECKSUMS": checksums,
    })
    bin_dir = tmp_path / "toolbin"

    result = harness.run(
        TEMPLATE, TOOLCHAIN,
        then=install_call(
            "https://example.invalid/faketool.tar.gz",
            "https://example.invalid/CHECKSUMS",
            sha256(checksums), "faketool.tar.gz", "faketool", str(bin_dir),
        ),
    )

    assert result.returncode == 0, result.output
    installed = bin_dir / "faketool"
    assert installed.is_file()
    assert subprocess.run([str(installed)], capture_output=True, text=True).stdout.strip() \
        == "i-am-the-tool"


def test_install_fails_when_the_checksum_file_was_swapped(harness, tmp_path):
    """The checksum file is pinned, so a substituted one is caught on its own.

    Without this pin an attacker who can serve both files just publishes a
    checksum matching the payload, and the asset check passes.
    """
    archive = make_tool_archive(tmp_path / "release")
    checksums = tmp_path / "release" / "CHECKSUMS"
    checksums.write_text(f"{sha256(archive)}  faketool.tar.gz\n")
    serve(harness, {
        "https://example.invalid/faketool.tar.gz": archive,
        "https://example.invalid/CHECKSUMS": checksums,
    })
    bin_dir = tmp_path / "toolbin"

    result = harness.run(
        TEMPLATE, TOOLCHAIN,
        then=install_call(
            "https://example.invalid/faketool.tar.gz",
            "https://example.invalid/CHECKSUMS",
            "0" * 64, "faketool.tar.gz", "faketool", str(bin_dir),
        ),
    )

    assert result.returncode != 0
    assert "checksum file does not match its pinned SHA-256" in result.output
    assert not bin_dir.exists()


def test_install_fails_when_the_asset_does_not_match_its_published_checksum(harness, tmp_path):
    archive = make_tool_archive(tmp_path / "release")
    tampered = tmp_path / "release" / "tampered.tar.gz"
    tampered.write_bytes(archive.read_bytes() + b"extra")
    checksums = tmp_path / "release" / "CHECKSUMS"
    checksums.write_text(f"{sha256(archive)}  faketool.tar.gz\n")
    serve(harness, {
        "https://example.invalid/faketool.tar.gz": tampered,
        "https://example.invalid/CHECKSUMS": checksums,
    })
    bin_dir = tmp_path / "toolbin"

    result = harness.run(
        TEMPLATE, TOOLCHAIN,
        then=install_call(
            "https://example.invalid/faketool.tar.gz",
            "https://example.invalid/CHECKSUMS",
            sha256(checksums), "faketool.tar.gz", "faketool", str(bin_dir),
        ),
    )

    assert result.returncode != 0
    assert "does not match its published checksum" in result.output


def test_install_fails_when_the_asset_is_absent_from_the_checksum_file(harness, tmp_path):
    archive = make_tool_archive(tmp_path / "release")
    checksums = tmp_path / "release" / "CHECKSUMS"
    checksums.write_text(f"{sha256(archive)}  some-other-asset.tar.gz\n")
    serve(harness, {
        "https://example.invalid/faketool.tar.gz": archive,
        "https://example.invalid/CHECKSUMS": checksums,
    })

    result = harness.run(
        TEMPLATE, TOOLCHAIN,
        then=install_call(
            "https://example.invalid/faketool.tar.gz",
            "https://example.invalid/CHECKSUMS",
            sha256(checksums), "faketool.tar.gz", "faketool",
            str(tmp_path / "toolbin"),
        ),
    )

    assert result.returncode != 0
    assert "is not listed in the faketool checksum file" in result.output


def test_install_fails_when_the_download_fails(harness, tmp_path):
    serve(harness, {})

    result = harness.run(
        TEMPLATE, TOOLCHAIN,
        then=install_call(
            "https://example.invalid/faketool.tar.gz",
            "https://example.invalid/CHECKSUMS",
            "0" * 64, "faketool.tar.gz", "faketool", str(tmp_path / "toolbin"),
        ),
    )

    assert result.returncode != 0


def test_the_newline_separator_is_only_a_newline(harness):
    """The embedded copy must not pick up the YAML block's indentation.

    Writing a literal newline in the source made CI_TPL_NEWLINE a newline plus
    six spaces once embedded, so every list-shaped input silently split on
    spaces too. This is the regression test for that.
    """
    result = harness.run(
        TEMPLATE, TOOLCHAIN,
        then='printf "[%s]" "$CI_TPL_NEWLINE" | od -c | head -2\n',
    )

    assert result.returncode == 0, result.output
    assert "[  \\n   ]" in result.stdout, result.stdout


def test_a_path_inside_the_checkout_is_accepted(harness):
    harness.file("charts/app/Chart.yaml", "name: app\n")

    result = harness.run(
        TEMPLATE, TOOLCHAIN, then="ci_tpl_require_inside_checkout charts/app\n"
    )

    assert result.returncode == 0, result.output


def test_a_path_escaping_the_checkout_is_rejected(harness):
    result = harness.run(
        TEMPLATE, TOOLCHAIN, then="ci_tpl_require_inside_checkout ../elsewhere\n"
    )

    assert result.returncode != 0
    assert "outside the checkout" in result.output


def test_a_symlink_out_of_the_checkout_is_rejected(harness, tmp_path):
    """A traversal check on the literal string would pass this."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (harness.project_dir / "sneaky").symlink_to(outside)

    result = harness.run(
        TEMPLATE, TOOLCHAIN, then="ci_tpl_require_inside_checkout sneaky/target\n"
    )

    assert result.returncode != 0
    assert "outside the checkout" in result.output
