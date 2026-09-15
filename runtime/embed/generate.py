#!/usr/bin/env python3
"""Embed runtime code into the component templates that execute it.

Section 12 of docs/gitlab-ci-agent-standard.md puts substantial logic in
`runtime/<domain>/` with its own tests, and also rules out reaching for it at
job time: `include:` imports YAML, not files, so the job runs against the
CONSUMER's checkout and this repository is not on disk. Cloning it back at job
time would mean a moving ref, which section 11.6 forbids for the same reason.

So the template carries the code, and this script is what makes the copy
trustworthy. Every embedded region is a marked span of shell inside a block
scalar:

    - |
      # BEGIN embed inline runtime/helm/validate.sh
      ...the file, verbatim...
      # END embed

Three delivery modes, because the components need three and no more:

    inline <source>              the block IS the script; the job runs it as a
                                 step, or sources the function library it
                                 defines
    file <dest> <source>         one file written to exactly <dest>, which the
                                 job then sources or invokes with arguments
    dir <dest> <source>...       a tree written under <dest>, each file keeping
                                 its path relative to the sources' common
                                 parent; this is how Python modules reach a job
                                 as importable modules, and how the multi-file
                                 runtimes (runtime/wiki, runtime/audit) travel

<dest> is a bare shell word, normally a `$CI_TPL_*` variable the template sets.
The generator adds the quoting; a dest carrying a quote, a space or a command
substitution is rejected rather than embedded, because the region is shell and
section 13.1 allows no eval.

`--write` refreshes every region from the files it names. `--check` fails when a
region and its sources have drifted, which is both a CI job and a test in
tests/runtime/embed/.

The upgrade path is section 12's own answer: an execution image that ships the
runtime, at which point the regions become an image build input instead of an
embedded copy.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"

# Quoted, so the shell expands nothing inside a payload. A source that contains
# this line would end its own heredoc early, which check_source refuses.
HEREDOC = "CI_TPL_EMBED_EOF"

BEGIN = "# BEGIN embed "
END = "# END embed"

# The marker pair and everything between it. The indentation of BEGIN sets the
# indentation of the regenerated region, so a template may nest it anywhere.
REGION = re.compile(
    rf"^(?P<indent>[ ]*){re.escape(BEGIN)}(?P<spec>[^\n]+)\n"
    r"(?P<body>.*?)"
    rf"^(?P=indent){re.escape(END)}\n",
    re.DOTALL | re.MULTILINE,
)

# A shell word with no quoting, whitespace, or substitution to get wrong.
DEST = re.compile(r"[A-Za-z0-9_./$-]+")


class EmbedError(Exception):
    """A region, a dest or a source this generator refuses to embed."""


def check_source(path: Path, text: str) -> None:
    """Refuse a source that cannot survive the trip into a block scalar."""
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip() == HEREDOC:
            raise EmbedError(f"{path}:{number} is the heredoc delimiter {HEREDOC}")
        if line.strip() == END:
            raise EmbedError(f"{path}:{number} would close the embedded region")
        if line != line.rstrip():
            raise EmbedError(
                f"{path}:{number} has trailing whitespace, which yamllint rejects "
                "once this file is embedded in a template's block scalar"
            )


def read_source(source: str) -> str:
    path = REPO_ROOT / source
    if not path.is_file():
        raise EmbedError(f"no such runtime file: {source}")
    text = path.read_text()
    check_source(path, text)
    return text


def check_dest(dest: str) -> str:
    if not DEST.fullmatch(dest):
        raise EmbedError(f"destination {dest!r} is not a plain shell word")
    return dest


def relative_paths(sources: list[str]) -> list[str]:
    """Where each source lands under a `dir` destination.

    Relative to the common parent of the region's sources, so a domain that
    pulls in the shared runtime/httpjson.py keeps `sign/cosign_attest.py` one
    level down and the module's `import httpjson` still resolves, while a
    region drawn entirely from one directory lands as bare filenames.
    """
    base = os.path.commonpath([str(Path(source).parent) for source in sources])
    return [str(Path(source).relative_to(base)) for source in sources]


def heredoc(indent: str, target: str, source: str) -> list[str]:
    lines = [f"{indent}cat > {target} <<'{HEREDOC}' || exit 1"]
    lines += [
        f"{indent}{line}" if line else ""
        for line in read_source(source).rstrip("\n").split("\n")
    ]
    lines.append(f"{indent}{HEREDOC}")
    if source.endswith(".sh"):
        lines.append(f"{indent}chmod +x {target} || exit 1")
    return lines


def render(indent: str, spec: str) -> str:
    """The region a marker asks for, markers included."""
    mode, *rest = spec.split()
    if mode == "inline":
        if len(rest) != 1:
            raise EmbedError(f"inline takes one source, got: {spec}")
        body = [
            f"{indent}{line}" if line else ""
            for line in read_source(rest[0]).rstrip("\n").split("\n")
        ]
    elif mode == "file":
        if len(rest) != 2:
            raise EmbedError(f"file takes a destination and one source, got: {spec}")
        body = heredoc(indent, f'"{check_dest(rest[0])}"', rest[1])
    elif mode == "dir":
        if len(rest) < 2:
            raise EmbedError(f"dir takes a destination and sources, got: {spec}")
        dest, sources = check_dest(rest[0]), rest[1:]
        relatives = relative_paths(sources)
        directories = sorted({str(Path(f'{dest}/{r}').parent) for r in relatives})
        body = [
            indent + "mkdir -p "
            + " ".join(f'"{directory}"' for directory in directories)
            + " || exit 1"
        ]
        for source, relative in zip(sources, relatives):
            body += heredoc(indent, f'"{dest}/{relative}"', source)
    else:
        raise EmbedError(f"unknown embed mode {mode!r}; use inline, file or dir")

    return (
        f"{indent}{BEGIN}{spec}\n" + "".join(f"{line}\n" for line in body)
        + f"{indent}{END}\n"
    )


def regions(text: str) -> list[re.Match[str]]:
    """Every marked region, refusing a BEGIN this module cannot pair with an END.

    Without the count an unterminated or mis-indented marker would simply not
    match, and the region would silently stop being regenerated.
    """
    found = list(REGION.finditer(text))
    declared = text.count(BEGIN)
    if len(found) != declared:
        raise EmbedError(
            f"{declared} '{BEGIN.strip()}' marker(s) but {len(found)} complete "
            f"region(s); an END is missing or indented differently"
        )
    return found


def embedded_blocks(path: Path) -> dict[str, str]:
    """The shell each `inline` region of `path` actually carries, de-indented.

    The tests run this, not the file under runtime/, because the embedded copy
    is what the job executes. Indentation is the difference that matters: a
    literal newline or a heredoc terminator behaves differently once every line
    has gained the block scalar's indent.
    """
    blocks: dict[str, str] = {}
    for match in regions(path.read_text()):
        mode, *rest = match["spec"].split()
        if mode != "inline":
            continue
        indent = match["indent"]
        blocks[rest[0]] = "".join(
            (line[len(indent):] if line.startswith(indent) else line) + "\n"
            for line in match["body"].rstrip("\n").split("\n")
        )
    return blocks


def rewrite(text: str) -> str:
    out, end = [], 0
    for match in regions(text):
        out.append(text[end:match.start()])
        out.append(render(match["indent"], match["spec"]))
        end = match.end()
    out.append(text[end:])
    return "".join(out)


def template_paths() -> list[Path]:
    if not TEMPLATES_DIR.is_dir():
        return []
    return sorted(TEMPLATES_DIR.glob("*/template.yml"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--write", action="store_true", help="refresh the embedded regions in place"
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when a template and its runtime sources have drifted",
    )
    args = parser.parse_args(argv)

    stale: list[str] = []
    for path in template_paths():
        current = path.read_text()
        try:
            wanted = rewrite(current)
        except EmbedError as error:
            print(f"{path.relative_to(REPO_ROOT)}: {error}", file=sys.stderr)
            return 1
        if current == wanted:
            continue
        stale.append(str(path.relative_to(REPO_ROOT)))
        if args.write:
            path.write_text(wanted)

    if not stale:
        return 0
    if args.write:
        print("refreshed: " + ", ".join(stale))
        return 0
    print(
        "embedded runtime is stale in: " + ", ".join(stale) + "\n"
        "run python3 runtime/embed/generate.py --write",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
