#!/usr/bin/env python3
"""Describe an OCI layout so the receiving side can prove the transfer arrived.

`<layout-parent>/oci` is the layout skopeo copied every image into, `<stamp>`
names the transfer, and `<refs-file>` holds one destination reference per line
in the order they were copied. The document written to stdout is:

    {"transfer": "<stamp>",
     "images": [{"ref": ..., "digest": ..., "config": ..., "layers": [...]}]}

It lists EVERY layer, including the ones the archive leaves out because the
receiving side already holds them. That is the point of the file: the archive
alone cannot say whether it is complete, and a manifest that listed only what
travelled would agree with itself no matter how much was missing.

A reference the layout does not carry, or a manifest blob that is not there,
exits 1. Both mean skopeo copied less than it was asked to, and a transfer
described as complete when it is not is the failure this file exists to make
loud.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REF_ANNOTATION = "org.opencontainers.image.ref.name"


def build(layout: Path, stamp: str, refs: list[str]) -> dict:
    """The transfer document for `refs`, read out of the layout on disk."""
    index = json.loads((layout / "index.json").read_text())
    by_ref = {
        entry["annotations"][REF_ANNOTATION]: entry["digest"]
        for entry in index["manifests"]
        if REF_ANNOTATION in entry.get("annotations", {})
    }

    images = []
    for ref in refs:
        if ref not in by_ref:
            raise ValueError(f"the layout carries no manifest for {ref}")
        digest = by_ref[ref]
        blob = layout / "blobs" / "sha256" / digest.split(":", 1)[1]
        if not blob.is_file():
            raise ValueError(f"{ref} names manifest {digest}, which is not in the layout")
        manifest = json.loads(blob.read_text())
        images.append(
            {
                "ref": ref,
                "digest": digest,
                "config": manifest["config"]["digest"],
                "layers": [layer["digest"] for layer in manifest["layers"]],
            }
        )
    return {"transfer": stamp, "images": images}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=argv[0], description=__doc__.strip().splitlines()[0])
    parser.add_argument("layout_parent", help="directory holding the oci/ layout")
    parser.add_argument("stamp", help="name of this transfer")
    parser.add_argument("refs_file", help="one destination reference per line, in copy order")
    args = parser.parse_args(argv[1:])
    parent, stamp, refs_file = args.layout_parent, args.stamp, args.refs_file
    try:
        refs = [line.strip() for line in Path(refs_file).read_text().splitlines() if line.strip()]
        if not refs:
            raise ValueError(f"{refs_file} names no reference")
        document = build(Path(parent) / "oci", stamp, refs)
    except (OSError, KeyError, ValueError) as error:
        print(f"ERROR: cannot describe the transfer: {error}", file=sys.stderr)
        return 1
    json.dump(document, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
