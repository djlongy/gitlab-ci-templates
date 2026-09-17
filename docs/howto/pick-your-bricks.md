# Pick your bricks

`pipelines/container-buildkit.yml` is the whole container chain, every brick on.
There are two ways to get a subset of it.

1. **Take the composition and switch bricks off** when what you want is the
   release pipeline minus a step. Every brick after the build has an
   `enable-<brick>` input, default `true`:

   ```yaml
   include:
     - project: 'platform/gitlab-ci-templates'
       ref: 'REPLACE_WITH_APPROVED_SHARED_CI_REF'
       file: '/pipelines/container-buildkit.yml'
       inputs:
         instance: api
         image-repository: registry.example.com/dev/platform/api
         semgrep-rules: p/default
         lockfiles: go.sum
         enable-lockfiles: false             # the rest stay on
   ```

2. **Include the bricks directly** when what you want is not that pipeline at
   all: a repository that builds an image and stops, or one that scans an image
   somebody else built. You then own `stages:` and the `needs:` edges. The three
   kits below are complete files; take the ref from `shared_ci.approved_ref` in
   `.ci/estate.yml`.

## 3. Kit A — build and push, nothing else

```yaml
stages: [build]

include:
  - project: 'platform/gitlab-ci-templates'
    ref: 'REPLACE_WITH_APPROVED_SHARED_CI_REF'
    file: '/templates/container-build-buildkit/template.yml'
    inputs:
      instance: api
      image-repository: registry.example.com/dev/platform/api
```

One job. It writes `.ci-artifacts/api/container-build-buildkit/image.json`, the
digest every other brick names.

## 4. Kit B — build, SBOM, image scan

```yaml
stages: [build, scan]

include:
  - project: 'platform/gitlab-ci-templates'
    ref: 'REPLACE_WITH_APPROVED_SHARED_CI_REF'
    file: '/templates/container-build-buildkit/template.yml'
    inputs:
      instance: api
      image-repository: registry.example.com/dev/platform/api
  - project: 'platform/gitlab-ci-templates'
    ref: 'REPLACE_WITH_APPROVED_SHARED_CI_REF'
    file: '/templates/security-sbom-syft/template.yml'
    inputs:
      instance: api
      build-job: 'api:container-build-buildkit'
  - project: 'platform/gitlab-ci-templates'
    ref: 'REPLACE_WITH_APPROVED_SHARED_CI_REF'
    file: '/templates/security-image-trivy/template.yml'
    inputs:
      instance: api
      build-job: 'api:container-build-buildkit'
```

Both scanners read that same `image.json`, so both describe the digest that was
built rather than whatever the tag points at now.

## 5. Kit C — the full chain, signed and promoted

Kit B, plus:

```yaml
stages: [build, scan, attest, publish]

include:
  # ... the three entries from kit B ...
  - project: 'platform/gitlab-ci-templates'
    ref: 'REPLACE_WITH_APPROVED_SHARED_CI_REF'
    file: '/templates/container-sign-attest-cosign/template.yml'
    inputs:
      instance: api
      build-job: 'api:container-build-buildkit'
      sbom-job: 'api:security-sbom-syft'
      scan-job: 'api:security-image-trivy'
  - project: 'platform/gitlab-ci-templates'
    ref: 'REPLACE_WITH_APPROVED_SHARED_CI_REF'
    file: '/templates/container-promote-harbor/template.yml'
    inputs:
      instance: api
      build-job: 'api:container-build-buildkit'
      gate-jobs:
        - job: 'api:security-image-trivy'
          artifacts: false
        - job: 'api:container-sign-attest-cosign'
          artifacts: false
```

`gate-jobs` defaults to `[]`, and a promotion with an empty gate list is
refused: the job fails before it publishes and names `allow-ungated`, which
publishes anyway and leaves the warning in the log instead. Only the
composition knows the instance job names, so only you can fill this in.

## 6. The rules of the set

- **The build is the only brick with no producer.** Everything after it acts on
  an image, and names where that image comes from.
- **Every image brick takes `build-job` OR `subject-reference`, never both and
  never neither.** Both default to `''`, and the job fails naming both inputs if
  you set none or set two. `build-job` reads the `image.json` a build in this
  pipeline wrote; `subject-reference` is a `repository@sha256:...` this pipeline
  did not build, and the brick then writes an image.json-shaped record of its
  own so everything downstream reads one contract. The bricks that offer the
  choice are syft, both image scanners, cosign, the smoke test, both Vigil
  bricks and the Harbor promotion.
- **Cosign attests what it is given.** `sbom-job` and `scan-job` default to
  `''`: the image is still signed, the attestation with no producer is skipped,
  and the log says which one and why. Name a producer and its evidence becomes
  something the job fails without.
- **Dependency-Track is the exception: it reads the SBOM itself,** so `sbom-job`
  stays required and there is no Dependency-Track without syft.
- **Vigil syncs without a signature.** `sign-job` defaults to `''`, and the sync
  then waits for nothing; it was always a gate rather than a producer.
- **Grype scans the registry when it has no SBOM.** `sbom-job: ''` makes it pull
  the image named by the build or the reference and scan that instead.
- **Promotion and the export refuse an empty `gate-jobs`,** unless you set
  `allow-ungated: true` and take the warning. An empty gate list is a release
  with no scan, no signature and no readiness check in front of it, and it used
  to compile, run and go green.
- **Gitleaks, Semgrep, the filesystem scan and the lockfile check need nothing.**
  They read the checkout.
- **A brick you leave out has to leave every `needs:` list that named it.** A
  `needs:` entry cannot name a job that is not in the pipeline — not even
  `optional: true`, which section 8.3 forbids — so blank the input that named
  it, or drop the entry from `gate-jobs`, or nothing compiles.

Switching a brick off in the composition (way 1) does this for you: each
dependant's include rule carries its producers' switches, so `enable-syft:
false` also removes Dependency-Track, the signature, both Vigil bricks and the
promotion rather than leaving a dangling need. That is deliberate — a promotion
whose scan or signature was switched off would publish on no evidence — and it
is measured: deleting one of those conjuncts and resolving the composition with
the brick off gives `undefined need` from the CI Lint API.

One card per brick, with its files, inputs, tests and egress, is in
[../bricks.md](../bricks.md).
