#!/usr/bin/env python3
"""Read an RKE2 release manifest and print what the list job must fetch.

The manifest is plain YAML the consumer keeps in its own checkout, small enough
to parse without a YAML library: the execution image carries python3 but no
PyYAML, and installing one to read three keys would be a dependency nobody
audits. What is accepted is therefore a documented subset, not YAML:

    version: v1.31.5+rke2r1     scalar, required
    arch: amd64                 scalar, required
    lists:                      sequence of scalars, required, non-empty
      - core
      - canal

`#` starts a comment anywhere on a line and blank lines are ignored. A key this
parser does not know is ignored rather than rejected, so a consumer may keep
notes beside the three fields this reads.

Output is one line, `<version> <arch> <list> <list>...`, which the calling
shell splits with `read`. A malformed manifest exits 1 with a message naming
what is missing: the job must not resolve a partial release and report success.
"""

from __future__ import annotations

import argparse
import sys


def parse(text: str) -> tuple[str, str, list[str]]:
    """The version, architecture and list names a manifest declares."""
    fields: dict[str, str] = {}
    lists: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line == "-" or line.startswith("- "):
            # A bare `-` reaches here too, so the empty-entry check below sees
            # it. Left to the key branch it would read as a malformed key and
            # the message would name the wrong problem.
            lists.append(line[1:].strip())
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"line is neither a key nor a list entry: {raw.strip()!r}")
        if value.strip():
            fields[key.strip()] = value.strip()

    missing = [name for name in ("version", "arch") if not fields.get(name)]
    if not lists:
        missing.append("lists")
    if missing:
        raise ValueError("manifest declares no " + ", ".join(missing))
    if any(not name for name in lists):
        raise ValueError("manifest has an empty entry under lists")
    return fields["version"], fields["arch"], lists


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=argv[0], description=__doc__.strip().splitlines()[0])
    parser.add_argument("manifest", help="the release manifest in the consumer checkout")
    args = parser.parse_args(argv[1:])
    try:
        with open(args.manifest, encoding="utf-8") as handle:
            version, arch, lists = parse(handle.read())
    except (OSError, ValueError) as error:
        print(f"ERROR: cannot read release manifest {args.manifest}: {error}", file=sys.stderr)
        return 1
    print(version, arch, " ".join(lists))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
