# gitlab-ci-templates

A shared GitLab CI library, built to one written standard. A consuming
project's whole `.gitlab-ci.yml` is one `include:` of a composition plus the
inputs that describe the workload. No stages, no job bodies, no jobs disabled.

Three layers: `templates/` holds 36 single-capability **components**,
`pipelines/` holds 11 **compositions** that wire them into a working pipeline,
and `runtime/` holds the scripts those jobs execute — carried inside the
templates so a component is one include and not an include plus a checkout.

Every component ships a `contract.yml` recording its inputs, the jobs it emits,
its artifacts, the secrets it needs, the image it runs and, in particular, the
**evidence** behind its status. `.ci/catalog.yml` is generated from those
contracts, so the inventory cannot drift from the thing it inventories.

The repository is governed by
[`docs/gitlab-ci-agent-standard.md`](docs/gitlab-ci-agent-standard.md), which
defines the layout, naming, input, artifact and gate contracts every file here
meets.

This is the public copy of a library that runs on a private GitLab. Host names,
registries, project ids and consumer names throughout are illustrative — see
**Forking** below for what to change.

## Usage

Pick a composition from the table below, include it, and supply its inputs.

```yaml
include:
  - project: 'platform/gitlab-ci-templates'
    ref: '<full commit SHA or protected release tag>'
    file: '/pipelines/container-buildkit.yml'
    inputs:
      instance: api
      image-repository: 'registry.example.com/dev/platform/api'
      dockerfile: 'Dockerfile'
      semgrep-rules: 'ci/semgrep-rules.yml'
      lockfiles: 'go.sum'
```

That is the complete `.gitlab-ci.yml` of a project that builds one image from a
Dockerfile and releases it. `instance` names the workload: it becomes the
prefix of every job name (`api:container-build-buildkit`,
`api:security-image-trivy`, …) and the directory every artifact lands in
(`.ci-artifacts/api/<component>/`). Two workloads in one project are two
includes with two instance names.

`ref` must be a full commit SHA or a protected release tag. `.ci/estate.yml`
records `shared_ci.approved_ref`, which is the tag to pin; a reviewed commit SHA
is equally valid. A branch name is a moving target and section 14.2 forbids it.

A worked example for every composition, with the comments explaining each
input, is in [`examples/`](examples/).

## Supported compositions

`released` means a consumer pipeline ran every job the composition creates and
they went green. `experimental` means it has not, and each contract says why —
usually that a gated half (an apply, a build, a publish) has never been
triggered.

Three things that are deliberately NOT a run: a job GitLab created and nobody
started, a job a rule skipped, and a lint. The merged configuration of every
composition here compiles against GitLab 18.9.1-ee and every runtime helper has
unit tests, and neither of those promotes anything. The evidence labels are
cumulative — `source-reviewed`, `yaml-parsed`, `gitlab-linted`,
`runtime-tested`, `integration-tested`, `report-ingestion-tested` — and
`.ci/compatibility.yml` records which each unit holds. Read the status and
evidence for a composition in [`.ci/catalog.yml`](.ci/catalog.yml) before
depending on it.

| Composition | Required inputs | Jobs it emits | Status |
| --- | --- | --- | --- |
| [`container-buildkit`](pipelines/container-buildkit.yml) | `instance`, `image-repository`, `semgrep-rules`, `lockfiles` | verify (4), build, scan (2), attest, publish | experimental |
| [`container-ko`](pipelines/container-ko.yml) | `instance`, `image-repository`, `semgrep-rules`, `lockfiles` | same chain, built by ko, no Dockerfile | experimental |
| [`container-jib`](pipelines/container-jib.yml) | `instance`, `image-repository`, `semgrep-rules`, `lockfiles` | same chain, built by Jib from Gradle | experimental |
| [`terraform-verify`](pipelines/terraform-verify.yml) | `instance` | `terraform-fmt`, `terraform-validate`, `security-filesystem-trivy` | experimental |
| [`terraform-deploy`](pipelines/terraform-deploy.yml) | `instance`, `environment`, `state-id` | verify (3), `terraform-plan`, `terraform-apply` | experimental |
| [`terraform-module`](pipelines/terraform-module.yml) | `instance`, `module-name`, `module-system` | verify (2), `terraform-module-publish` | experimental |
| [`ansible-verify`](pipelines/ansible-verify.yml) | `instance`, `playbook` | `ansible-lint`, `ansible-syntax`, `security-secrets-gitleaks`, optional `ansible-check` | experimental |
| [`helm-chart`](pipelines/helm-chart.yml) | `instance`, `chart-paths`, `chart-path`, `chart-repository` | `helm-validate`, `security-secrets-gitleaks`, `helm-package-publish` | experimental |
| [`kubernetes-gitops`](pipelines/kubernetes-gitops.yml) | `instance`, `manifest-paths` | `kubernetes-validate`, `security-secrets-gitleaks`, optional `helm-validate` | released |
| [`docs-wiki`](pipelines/docs-wiki.yml) | none | `docs-wiki-sync` | released |
| [`container-mirror`](pipelines/container-mirror.yml) | `instance`, `repository-prefix`, `s3-endpoint`, `s3-bucket`, `s3-prefix`, `have-key` | `container-list-rke2`, `container-mirror-skopeo`, `container-export-skopeo` | released |

The 36 components these compositions are built from are individually
includable, for a project whose pipeline is mostly its own. Their inputs,
emitted job names, outputs, required secret names and evidence level are all in
[`.ci/catalog.yml`](.ci/catalog.yml) — the generated inventory, and the only
place to check what actually exists at a given revision.

`container-mirror` is the one composition that takes its credentials from
CI/CD variables by default: `REGISTRY_USER` and `REGISTRY_PASSWORD` for the
registry, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` for the object
store. Setting its `vault-addr` input swaps all four for a Vault JWT login.

## Prerequisites

Per composition, not universally:

- **GitLab 18.9 or newer.** Every component declares `minimum_gitlab: 18.9` and
  uses typed `spec:inputs`.
- **Runners with the `docker` executor.** The profile these were written
  against registers them untagged and unprivileged, so `runner-tags` defaults
  to `[]`. The `container-smoke-test` component needs a privileged runner.
- **An OCI registry**, for any container composition: CI variables
  `HARBOR_USER` and `HARBOR_PASSWORD`, a candidate project and a release
  project. `container-promote-harbor` drives the Harbor API specifically; the
  build, scan and sign components are registry-agnostic.
- **HashiCorp Vault**, for signing: a JWT role and the id_token the composition
  declares. The signing key never leaves Vault.
- **SonarQube**, for `quality-sonarqube`: `SONAR_TOKEN` and `SONAR_HOST_URL`.
- **Protected tags**, for anything that publishes or promotes. Those jobs are
  gated on a protected tag and, where the composition says so, on `when:
  manual` as well.
- **`WIKI_TOKEN`**, for `docs-wiki`: a group access token, masked and
  protected, which means the default branch must be protected.

Which of these your estate actually provides is recorded in
[`.ci/estate.yml`](.ci/estate.yml). A value marked `unresolved` there was not
verified; it is not a blank to fill in by guessing.

### Execution images

Every job runs a digest-pinned image, and every third-party image is pulled
from `docker.io`. If your estate fronts Docker Hub with a pull-through cache,
set `images.mirror_prefix` in `.ci/estate.yml` to that cache and rewrite the
references: **the digests do not change**, because a pull-through cache serves
the upstream manifest unchanged, so `<cache>/<ref>@sha256:<d>` and
`docker.io/<ref>@sha256:<d>` are the same image. That equivalence was checked
against a live cache before this release was published. Keep the Renovate
lookup names pointing at Docker Hub either way; a bot that cannot authenticate
to your registry logs "found no results" and silently stops proposing updates.

## Forking

1. Push this repository to your own GitLab, as the project that consumers will
   `include:` from.
2. Rewrite [`.ci/estate.yml`](.ci/estate.yml). It ships describing a fictional
   estate, and it is the file every later reader trusts instead of rediscovering
   yours: GitLab host and version, the shared-CI project and its numeric id, the
   runners, the registry endpoints, the Vault address and roles, your
   environment names. Set `EXAMPLE_PROFILE = False` in
   `tests/contracts/test_estate.py` once it describes something real — that
   turns on the check that no illustration was left behind.
3. Empty [`.ci/exceptions.yml`](.ci/exceptions.yml) of its one illustrative
   entry and record your own.
4. Set `images.mirror_prefix` if you do not pull from Docker Hub directly, and
   rewrite the references to match.
5. Cut a protected release tag. Consumers pin that tag, never a branch.

## Migrating from the old include paths

The flat include paths are gone. Every one of them was deleted in 1.0.0, and a
consumer still pinned to one must move to the component or composition that
replaced it. Pin the 1.0.0 tag, migrate, then move off the old ref.

[`docs/migrations/0.x-to-1.0.md`](docs/migrations/0.x-to-1.0.md) maps every
removed path to its replacement and works through three real consumer
pipelines.

## Working in this repository

- [`AGENTS.md`](AGENTS.md) — what to read before editing, and the verification
  commands.
- [`CHANGELOG.md`](CHANGELOG.md) — release changes and deprecation deadlines.
- [`docs/gitlab-ci-agent-standard.md`](docs/gitlab-ci-agent-standard.md) — the
  standard itself.

## Running the tests

Needs Python 3.11 or newer and `pyyaml`, `jsonschema`, `pytest`, `yamllint`.
`jq` and `git` are needed by the audit-script tests, which run the shell for
real rather than mocking it.

```bash
pip install pyyaml jsonschema pytest yamllint

yamllint -d "{extends: default, rules: {line-length: disable, truthy: disable}}" \
  .gitlab-ci.yml .ci/ tests/ runtime/ templates/ pipelines/ examples/
python3 runtime/catalog/generate.py --check     # catalogue drift
python3 runtime/embed/generate.py --check       # embedded-runtime drift
GIT_CONFIG_GLOBAL=/dev/null python3 -m pytest -q tests/
```

`GIT_CONFIG_GLOBAL=/dev/null` isolates the tests from your own git config; set
it per command, not as an exported variable, or git rejects `/dev/null` with
"bad config line 1".

Everything under `tests/pipelines/` validates **merged** configuration through
the GitLab CI Lint API, which is the only check here that resolves `include:`
for real. It needs a server, so those tests **skip without `GITLAB_TOKEN`** — a
run with no token is green and proves nothing about merged configuration. To
run them against your own GitLab:

```bash
export GITLAB_URL=https://gitlab.example.com   # default: https://gitlab.com
export GITLAB_PROJECT_ID=42                    # default: .ci/estate.yml shared_ci.project_id
export GITLAB_TOKEN=...                        # personal or group token, api scope
GIT_CONFIG_GLOBAL=/dev/null python3 -m pytest -q tests/pipelines/
```

The endpoint is project-scoped because `include:` resolution depends on project
context, so `GITLAB_PROJECT_ID` must name the project holding your copy of these
templates. `CI_JOB_TOKEN` is not accepted by the CI Lint API.

`python3 tests/pipelines/lint.py path/to/.gitlab-ci.yml` lints one file
directly, with `--host` and `--project-id` overriding the variables above.

## Licence

MIT. See [`LICENSE`](LICENSE).
