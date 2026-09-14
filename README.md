# gitlab-ci-templates

Reusable GitLab CI templates for scanning, building, signing and shipping
container images and Terraform, plus one composed pipeline that wires them
together. Every job is a hidden template (`.name`) with a concrete job beside
it, so a consumer can either take the concrete job as-is or `extends:` the
hidden one and override anything.

## How to include

```yaml
include:
  - project: 'platform/ci-templates'
    file: '/security/gitleaks.yml'
    ref: main
```

Replace `platform/ci-templates` with wherever you host this repo on your own
GitLab. `ref:` can be `main` or a tag.

One example per folder:

```yaml
include:
  # security/ — scanners and gates
  - project: 'platform/ci-templates'
    file: '/security/trivy-fs.yml'
    ref: main
  # build/ — image and chart builders
  - project: 'platform/ci-templates'
    file: '/build/buildkit.yml'
    ref: main
  # terraform/ — validate, lint, plan/apply, module publish
  - project: 'platform/ci-templates'
    file: '/terraform/validate.yml'
    ref: main
  # test/ — post-build smoke tests
  - project: 'platform/ci-templates'
    file: '/test/smoke-test.yml'
    ref: main
  # pipelines/ — the whole chain in one include
  - project: 'platform/ci-templates'
    file: '/pipelines/devsecops.yml'
    ref: main
  # docs/ — docs published to the project wiki
  - project: 'platform/ci-templates'
    file: '/docs/wiki-sync.yml'
    ref: main
```

`examples/single-image.gitlab-ci.yml` and `examples/monorepo.gitlab-ci.yml` are
complete consumer configs. Copy one as your `.gitlab-ci.yml` and change the
image names.

## What is here

| File | Job(s) | Stage |
|---|---|---|
| `security/gitleaks.yml` | `gitleaks` | secret-scan |
| `security/semgrep.yml` | `semgrep` | verify |
| `security/trivy-fs.yml` | `trivy-fs` | verify |
| `security/grype-fs.yml` | `grype-fs` | verify |
| `security/lockfile-check.yml` | `lockfile-check` | verify |
| `security/sonarqube.yml` | `sonarqube` | verify |
| `security/trivy-image.yml` | `trivy-image` | scan |
| `security/grype-image.yml` | `grype-image` | scan |
| `security/trivy-scan.yml` | `trivy-scan` (deprecated, use `trivy-image.yml`) | scan |
| `security/syft-sbom.yml` | `syft-sbom` | scan |
| `security/cosign-sign.yml` | `cosign-sign` | sign |
| `security/dtrack-upload.yml` | `dtrack-upload` | enrich |
| `security/terraform-audit.yml` | `clone-terraform-repos`, `terraform-secret-audit` | audit |
| `build/buildkit.yml` | `buildkit-build` | build |
| `build/ko.yml` | `ko-build` | build |
| `build/jib.yml` | `jib-build` | build |
| `build/helm-oci.yml` | `helm-oci-publish` | build |
| `terraform/validate.yml` | `.terraform-validate` | validate |
| `terraform/lint.yml` | `terraform-lint` | lint |
| `terraform/plan-apply.yml` | `.terraform-plan`, `.terraform-apply` | plan, apply |
| `terraform/module-publish.yml` | `.module-publish` | publish |
| `test/smoke-test.yml` | `.smoke-test` | test |
| `pipelines/devsecops.yml` | stages + includes for the whole chain | — |
| `docs/wiki-sync.yml` | docs-to-wiki publication | — |

## Conventions

- **Semver tags trigger builds.** The templates themselves are permissive: the
  scan and verify jobs run on merge requests, the default branch and any tag,
  and the build, image-scan and sign jobs run on the default branch and any tag.
  The release convention lives in the consumer, and both `examples/` configs
  show it: override the build/scan/sign rules with `$CI_COMMIT_TAG =~
  /^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$/` so a push to `main` runs verify and
  secret-scan only and never produces an image. `smoke-test.yml` already carries
  that regex, and `terraform/module-publish.yml` uses `v`-prefixed tags because
  that is what the GitLab Terraform module registry expects.
- **`APP_VERSION` build-arg.** `buildkit.yml` passes `--opt
  build-arg:APP_VERSION="${BUILDKIT_TAG}"` on every build, so a Dockerfile can
  bake the released version in with `ARG APP_VERSION=dev`. See
  `examples/Dockerfile`.
- **Registry credentials are `REGISTRY_USER` / `REGISTRY_PASSWORD`.** Every
  template that talks to a registry reads that pair and skips auth when unset,
  so anonymous pulls still work.
- **Runner tags are the consumer's choice.** No template pins `tags:` except
  `terraform-audit.yml`, which uses `${RUNNER_TAG}` (default `docker`). If your
  runners do not accept untagged jobs, add `tags:` in your own job override.
- **Tool versions are pinned** in each template's `variables:` block and bumped
  by Renovate. Trivy is held at 0.69.3, the last release before the 0.69.4-6
  supply-chain compromise.
- **Scanner artifacts feed the GitLab SAST widget** via `artifacts: reports:
  sast:`, and `sonarqube.yml` re-imports the Semgrep and Trivy SARIF. Overriding
  `needs:` on the `sonarqube` job replaces that list rather than merging with
  it, which silently stops the import; re-add the two scanner jobs if you do.

## Variables

Every variable a template reads, with its default. Blank means required.

### security/

| Template | Variable | Default |
|---|---|---|
| gitleaks | (none) | |
| semgrep | `SEMGREP_VERSION` | `1.79.0` |
| | `SEMGREP_RULES` | `p/default` |
| trivy-fs | `TRIVY_VERSION` | `0.69.3` |
| | `TRIVY_SEVERITY` | `CRITICAL,HIGH` |
| | `TRIVY_EXIT_CODE` | `1` |
| | `TRIVY_SCANNERS` | `vuln,secret,misconfig` |
| | `TRIVY_SKIP_DIRS` | *(empty)* |
| trivy-image | `TRIVY_IMAGE` | *(required)* |
| | `TRIVY_VERSION` | `0.69.3` |
| | `TRIVY_SEVERITY` | `CRITICAL,HIGH` |
| | `TRIVY_EXIT_CODE` | `1` |
| | `TRIVY_IGNORE_UNFIXED` | `true` |
| | `SBOM_ENABLED` / `SBOM_FORMAT` | `true` / `cyclonedx` |
| | `SARIF_ENABLED` | `true` |
| | `REGISTRY_USER` / `REGISTRY_PASSWORD` | *(unset: anonymous)* |
| trivy-scan | `TRIVY_IMAGE` | *(required)* |
| | `TRIVY_VERSION` | `0.69.3` |
| | `TRIVY_SEVERITY` | `CRITICAL,HIGH,MEDIUM` |
| | `TRIVY_EXIT_CODE` | `0` |
| grype-fs | `GRYPE_VERSION` | `v0.118.0` |
| | `GRYPE_SEVERITY` | `high` |
| | `GRYPE_EXIT_CODE` | `1` |
| | `GRYPE_ONLY_FIXED` | `true` |
| | `GRYPE_EXTRA_ARGS` | *(empty)* |
| grype-image | `GRYPE_IMAGE` | *(required)* |
| | `GRYPE_VERSION` | `v0.118.0` |
| | `GRYPE_SEVERITY` / `GRYPE_EXIT_CODE` | `high` / `1` |
| | `GRYPE_ONLY_FIXED` / `GRYPE_EXTRA_ARGS` | `true` / *(empty)* |
| | `SBOM_ENABLED` / `SBOM_FORMAT` | `false` / `cyclonedx-json` |
| | `REGISTRY_USER` / `REGISTRY_PASSWORD` | *(unset: anonymous)* |
| syft-sbom | `SYFT_IMAGE` | *(required; a syft source, e.g. an image ref or `dir:.`)* |
| | `SYFT_VERSION` | `v1.20.0` |
| | `SBOM_FORMAT` | `cyclonedx-json` |
| | `SBOM_FILE` | `sbom.cdx.json` |
| | `REGISTRY_USER` / `REGISTRY_PASSWORD` | *(unset: anonymous)* |
| lockfile-check | (none — detects Python, Node, Go, Gradle, Helm from the tree) | |
| sonarqube | `SONAR_HOST_URL` / `SONAR_TOKEN` | *(required, set as CI variables)* |
| | `SONAR_PROJECT_KEY` | *(derived: `${CI_PROJECT_NAMESPACE}:${CI_PROJECT_NAME}`)* |
| | `SONAR_SOURCES` | `.` |
| | `SONAR_EXCLUSIONS` / `SONAR_EXTRA_ARGS` | *(empty)* |
| | `SONAR_SARIF_PATHS` | *(empty: autodetects semgrep/trivy SARIF)* |
| | `SONAR_QUALITYGATE_WAIT` | `false` |
| | `GIT_DEPTH` | `0` |
| cosign-sign | `IMAGE_DIGEST` | *(required)* |
| | `COSIGN_VERSION` | `v2.4.1` |
| | `VAULT_ADDR` | `https://vault.example.com:8200` |
| | `VAULT_TOKEN` | *(empty: uses the CI JWT)* |
| | `COSIGN_KEY` | `hashivault://cosign` |
| | `SBOM_FILE` / `VULN_FILE` | `sbom.cdx.json` / `trivy-report.json` |
| | `COSIGN_SIGN_ENABLED` | `true` |
| | `COSIGN_ATTEST_SBOM` / `_SLSA` / `_VULN` | `true` |
| | `COSIGN_TLOG_UPLOAD` | `false` |
| dtrack-upload | `DTRACK_URL` / `DTRACK_API_KEY` | *(required)* |
| | `DTRACK_PROJECT_NAME` | `${CI_PROJECT_NAME}` |
| | `DTRACK_PROJECT_VERSION` | `${CI_COMMIT_SHORT_SHA}` |
| | `SBOM_FILE` | `sbom.cdx.json` |
| terraform-audit | `TERRAFORM_REPOS` | *(required, space-separated repo names)* |
| | `GITLEAKS_VERSION` | `v8.21.2` |
| | `RUNNER_TAG` | `docker` |

### build/

| Template | Variable | Default |
|---|---|---|
| buildkit | `IMAGE_NAME` | *(required)* |
| | `BUILDKIT_VERSION` | `v0.18.2` |
| | `BUILDKIT_TAG` | `${CI_COMMIT_SHORT_SHA}` |
| | `BUILD_CONTEXT` / `DOCKERFILE_PATH` | `.` / `Dockerfile` |
| | `BUILDKIT_PLATFORM` | `linux/amd64` |
| | `BUILDKIT_OCI_MEDIATYPES` | `true` |
| | `BUILDKIT_BUILD_ARGS` | *(empty; space-separated `KEY=VALUE`)* |
| | `REGISTRY_USER` / `REGISTRY_PASSWORD` | *(unset: anonymous)* |
| ko | `KO_DOCKER_REPO` | *(required)* |
| | `KO_APP_PATH` | `.` |
| | `KO_DEFAULTBASEIMAGE` | `cgr.dev/chainguard/static:latest` |
| | `GOFLAGS` / `CGO_ENABLED` | `-trimpath` / `0` |
| jib | `JIB_TARGET_IMAGE` | *(required)* |
| | `JIB_BASE_IMAGE` | `gcr.io/distroless/java21-debian12:nonroot` |
| helm-oci | `HELM_REGISTRY` | *(required)* |
| | `HELM_PROJECT` | `charts` |
| | `CHART_PATH` | `.` |

### terraform/ and test/

| Template | Variable | Default |
|---|---|---|
| validate | `DEPLOY_DIR` | *(required)* |
| lint | `LINT_DIR` | `.` |
| plan-apply | `DEPLOY_DIR` | *(required)* |
| module-publish | `MODULE_DIR`, `MODULE_NAME`, `MODULE_SYSTEM` | *(required)* |
| smoke-test | `IMAGE_NAME` | *(required)* |
| | `SMOKE_TEST_TARGET` | `test-smoke` |
| | `DOCKER_HOST` | `tcp://docker:2375` |
| | `DOCKER_TLS_CERTDIR` | *(empty)* |
| | `TEST_HOST` | `docker` |

`cosign-sign.yml` has a hard `needs: trivy-image`, so include
`security/trivy-image.yml` in the same pipeline or the job will not resolve.

## How this was verified

Two throwaway projects on a private GitLab: one holding a copy of this tree so
`include: project:` resolves, one consumer that includes it.

**Lint.** Every template was posted to `POST /projects/:id/ci/lint` with
`include_jobs=true`, one variant each: the stages the template needs, an
`include:` of the template, and a probe job extending the hidden template where
the file defines no concrete job. All 25 variants (22 templates, the composed
`pipelines/devsecops.yml`, and both `examples/*.gitlab-ci.yml`) returned
`valid: true`. The composed pipeline resolved 12 jobs, the monorepo example 16,
the single-image example 12.

**Run.** A consumer with a Dockerfile, a pinned `requirements.txt` beside a
`requirements.in`, and a small Terraform module ran the templates for real.
Green: `terraform-lint`, `terraform-validate`, `semgrep`, `lockfile-check`,
`trivy-fs`, `gitleaks`, `syft-sbom`. `syft-sbom` ran in filesystem mode with
`SYFT_IMAGE: "dir:."` and produced a CycloneDX SBOM.

The registry, signing and publishing templates are **lint-only**:
`buildkit.yml`, `cosign-sign.yml`, `trivy-image.yml`, `grype-image.yml`,
`helm-oci.yml`, `jib.yml`, `ko.yml`, `dtrack-upload.yml`, `sonarqube.yml`,
`terraform/plan-apply.yml` and `terraform/module-publish.yml` all need a
registry, a Vault transit key, a SonarQube server, a Dependency-Track instance
or a Terraform backend to do anything, so they were proven to resolve and to
produce the expected jobs, not executed.

## Docs to wiki sync

`docs/` is the source of truth for documentation; the project wiki is a
generated view of it. `docs/wiki-sync.yml` is the template that publishes it.
See [docs/WIKI_SYNC.md](docs/WIKI_SYNC.md).
