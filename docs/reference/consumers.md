# Consumer list

`.ci/consumers.yml` names every repository that pins this library, the
ref it pins, and the files that hold the include. Nothing reads the
file at pipeline time. The pin in the committed `.gitlab-ci.yml` is
what runs.

A release bump is one grep-and-replace merge request per row, and a row
that names two files needs both changed in the same one. A project that
pins the library in several places is the reason the `files` list is a
list rather than a string.

A digest catalogue bump in `images/runner-images.yml` is a separate
release: a consumer pinning this library by tag does not see a new image
digest until the tag it pins moves.

Add a row when a new project pins a tag. Remove a row when it stops. The
ref in this file must match the committed pipeline, not an intention.

`images/mirrored-images.yml` reads the project paths in this file. A mirror
entry carrying `pinned_by` names the consumers whose own pipelines pin it,
and every project it names has to be a row here. Remove a row and that check
fails until the mirror entry goes too.
