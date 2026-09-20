# Mirror the tool images into a registry your runners can reach

Every component here runs in a container, and each one pins that container by
digest in its `execution-image` input. This repository ships those defaults
pointing at `docker.io`. If the machines that run your jobs cannot pull from
there, or your estate refuses any registry but its own, every job fails at pull
time before its script runs.

The fix is to copy each tool image into a registry your runners accept, and to
record what you copied in
[`images/mirrored-images.yml`](../../images/mirrored-images.yml) so the
catalogue and the templates can be checked against each other.

Two settings make it work, and they are the whole design:

1. `mirror_prefix` in [`.ci/estate.yml`](../../.ci/estate.yml) names the
   approved pull path. It is what `tests/contracts/test_mirrored_images.py`
   validates entries against.
2. Every `execution-image` input accepts a leading `$UPPER_SNAKE/`, so one
   group CI variable carries the prefix and no consumer overrides 37 inputs.
   The digest stays in the input, where a reviewer can see it.

## The naming rule

Some registries take only one level of repository name, so the upstream path is
flattened and has to be reversible:

| Upstream | Mirror name |
| --- | --- |
| `alpine` | `alpine` |
| `anchore/syft` | `anchore--syft` |
| `aquasec/trivy` | `aquasec--trivy` |

A double dash stands in for the slash, so `anchore--syft` recovers to
`anchore/syft` by replacing `--` with `/`. That matters beyond tidiness: a
version bot needs the upstream name to look the image up, and the mirror is the
only name it can see. If your registry takes nested names, keep the upstream
path instead and the same checks still hold.

`name` and `repository` state one fact twice, and the test asserts they agree:
`repository` must be `mirror_prefix` plus `name`.

## The tag is not optional

A registry that garbage-collects unreferenced manifests will delete a mirror
that no tag points at, often within minutes of the copy. Push the tag as well
as the digest:

```bash
skopeo copy --all --preserve-digests \
  docker://<source>@<digest> \
  docker://<repository>:<tag>
```

`--preserve-digests` is the flag that matters. Without it the copy may
re-encode the manifest, the digest changes, and every pin in this library now
names an image your mirror does not hold. With it, one manifest exists under
two references and a consumer keeps pinning the digest exactly as before.

The tag is never what a pipeline resolves. It exists to keep the manifest
referenced and to let a human find the release the digest came from.

Registries commonly auto-prune build tags by age. The catalogue schema refuses
a tag shaped like `<commit>-<pipeline>`, so a tool image cannot be tagged into
a prune policy meant for build output.

## An image a consumer pins, not this library

`tests/contracts/test_mirrored_images.py` fails an entry that nothing pins: a
mirror kept warm for nobody is one you will keep updating for no reason. But a
consumer that overrides an `execution-image`, or runs a job of its own, pins a
mirror no file here names, and that check would read it as unused.

`pinned_by` is that entry's answer. It names the projects whose own
`.gitlab-ci.yml` holds the pin:

```yaml
  - name: koalaman--shellcheck-alpine
    source: docker.io/koalaman/shellcheck-alpine
    repository: registry.example.com/ci/koalaman--shellcheck-alpine
    digest: sha256:0000000000000000000000000000000000000000000000000000000000000000
    tag: v0.11.0
    verified_at: 2026/09/21
    pinned_by:
      - platform/docs-site
```

The entry is then exempt from "pinned nowhere" and checked the other way
instead: every project it names has to be a row in
[`.ci/consumers.yml`](../../.ci/consumers.yml), so a mirror belongs to a
project a release bump actually reaches. A path that is not a row fails the
same test, so add the consumer row first if the project is new.

## Add an image

1. Find the reference. It is the `execution-image` default in the component's
   `template.yml`, restated in its `contract.yml`, and restated again as a
   composition input default in `pipelines/`. All three have to move together;
   nothing else ties them.
2. Copy it with the command above.
3. Add the row to `images/mirrored-images.yml`, with `verified_at` set to the
   day you read the digest back, not the day you wrote the row.
4. Point the three references at the mirror, or leave them and carry the prefix
   in a group variable.
5. Run `python3 -m pytest tests/contracts/test_mirrored_images.py`.

## Bump a digest

A digest bump is the same work in the other order: copy the new digest first,
add or update the row, then move the three references. Doing it the other way
round leaves a window where the library pins something the mirror has not got,
which is exactly the failure the drift check exists to catch, so it will tell
you.

## Verify

Read both references back and compare the manifest digest rather than trusting
what the copy reported:

```bash
skopeo inspect --raw docker://<source>@<digest> | sha256sum
skopeo inspect --raw docker://<repository>:<tag> | sha256sum
```

The two sums must match, and must equal the digest in the catalogue. If they do
not, the copy re-encoded the manifest: check that `--preserve-digests` was
passed and that the source reference named a digest rather than a tag.
