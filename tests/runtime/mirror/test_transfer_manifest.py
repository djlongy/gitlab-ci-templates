"""The transfer description in runtime/mirror/transfer_manifest.py.

manifest.json is the only thing that lets the receiving side prove a transfer
arrived complete: the archive leaves out every blob the far side already holds,
so the archive cannot answer the question about itself. Two properties therefore
matter more than the rest, and each has a test here.

  * it lists every layer of every image, including the ones left out;
  * it refuses to describe a layout that is missing what it was asked for,
    rather than describing less and reporting success.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime_helpers import load_runtime_module  # noqa: E402

transfer_manifest = load_runtime_module("mirror/transfer_manifest.py")

REF_ANNOTATION = "org.opencontainers.image.ref.name"


def digest_of(name: str) -> str:
    return "sha256:" + name.encode().hex().ljust(64, "0")[:64]


def layout(parent: Path, images: dict[str, list[str]]) -> Path:
    """An OCI layout carrying `images`: a ref mapped to its layer digests."""
    root = parent / "oci"
    blobs = root / "blobs" / "sha256"
    blobs.mkdir(parents=True)
    manifests = []
    for ref, layers in images.items():
        manifest = {
            "config": {"digest": digest_of(ref + "-config")},
            "layers": [{"digest": layer} for layer in layers],
        }
        manifest_digest = digest_of(ref)
        (blobs / manifest_digest.split(":", 1)[1]).write_text(json.dumps(manifest))
        manifests.append({"digest": manifest_digest, "annotations": {REF_ANNOTATION: ref}})
    (root / "index.json").write_text(json.dumps({"manifests": manifests}))
    return root


def test_every_layer_is_listed_even_the_shared_ones(tmp_path):
    """Two images sharing a base layer: the layer is stored once in the layout
    and named by both entries, because the far side reconstructs both images."""
    shared = digest_of("base")
    root = layout(
        tmp_path,
        {
            "mirror/library/nginx:1.27": [shared, digest_of("nginx")],
            "mirror/library/redis:7": [shared, digest_of("redis")],
        },
    )
    document = transfer_manifest.build(
        root, "20260916T000000Z", ["mirror/library/nginx:1.27", "mirror/library/redis:7"]
    )
    assert document["transfer"] == "20260916T000000Z"
    assert [image["ref"] for image in document["images"]] == [
        "mirror/library/nginx:1.27",
        "mirror/library/redis:7",
    ]
    assert all(shared in image["layers"] for image in document["images"])
    assert document["images"][0]["config"] == digest_of("mirror/library/nginx:1.27-config")


def test_the_order_of_the_reference_list_is_kept(tmp_path):
    root = layout(tmp_path, {"a:1": [digest_of("a")], "b:1": [digest_of("b")]})
    document = transfer_manifest.build(root, "stamp", ["b:1", "a:1"])
    assert [image["ref"] for image in document["images"]] == ["b:1", "a:1"]


def test_a_reference_the_layout_does_not_carry_fails(tmp_path):
    """skopeo copied less than it was asked to. Describing the rest and exiting
    0 would ship an archive whose manifest agrees with itself and is wrong."""
    root = layout(tmp_path, {"a:1": [digest_of("a")]})
    with pytest.raises(ValueError, match="no manifest for b:1"):
        transfer_manifest.build(root, "stamp", ["a:1", "b:1"])


def test_a_manifest_blob_that_is_not_on_disk_fails(tmp_path):
    root = layout(tmp_path, {"a:1": [digest_of("a")]})
    (root / "blobs" / "sha256" / digest_of("a:1").split(":", 1)[1]).unlink()
    with pytest.raises(ValueError, match="which is not in the layout"):
        transfer_manifest.build(root, "stamp", ["a:1"])


def test_an_index_entry_without_a_ref_annotation_is_skipped(tmp_path):
    """skopeo writes one, but a layout assembled another way may not, and a
    KeyError reading the index would be a traceback instead of a message."""
    root = layout(tmp_path, {"a:1": [digest_of("a")]})
    index = json.loads((root / "index.json").read_text())
    index["manifests"].append({"digest": digest_of("orphan"), "annotations": {}})
    (root / "index.json").write_text(json.dumps(index))
    document = transfer_manifest.build(root, "stamp", ["a:1"])
    assert len(document["images"]) == 1


def test_the_command_line_writes_json_to_stdout(tmp_path, capsys):
    layout(tmp_path, {"a:1": [digest_of("a")]})
    refs = tmp_path / "refs"
    refs.write_text("a:1\n\n")
    code = transfer_manifest.main(["transfer_manifest.py", str(tmp_path), "stamp", str(refs)])
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["images"][0]["ref"] == "a:1"


def test_an_empty_reference_list_fails(tmp_path, capsys):
    layout(tmp_path, {"a:1": [digest_of("a")]})
    refs = tmp_path / "refs"
    refs.write_text("\n\n")
    code = transfer_manifest.main(["transfer_manifest.py", str(tmp_path), "stamp", str(refs)])
    assert code == 1
    assert "names no reference" in capsys.readouterr().err


def test_a_missing_layout_fails_with_a_message(tmp_path, capsys):
    refs = tmp_path / "refs"
    refs.write_text("a:1\n")
    code = transfer_manifest.main(["transfer_manifest.py", str(tmp_path), "stamp", str(refs)])
    captured = capsys.readouterr()
    assert code == 1
    assert "cannot describe the transfer" in captured.err
    assert captured.out == ""


def test_the_wrong_number_of_arguments_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exit_code:
        transfer_manifest.main(["transfer_manifest.py"])
    assert exit_code.value.code == 2
    assert "usage:" in capsys.readouterr().err
