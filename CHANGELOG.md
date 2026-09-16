# Changelog

Release changes, migration instructions and deprecation deadlines for
`platform/gitlab-ci-templates`. Dates are yyyy/mm/dd.

## 1.1.0 — 2026/09/16

Two things ship in 1.1.0 and they are unrelated. The wiki sync repairs its own
webhook and trigger token instead of depending on a one-time bootstrap script,
and it can run on a shell-executor runner with no registry, no image and no
internet. Two new inputs, one new optional variable, one widened regex and one
dependency pin that had to change. Every default is unchanged: a consumer on
1.0.1 upgrades by changing the `ref` and gets the job it had.

### Added

- **`executor` on `docs-wiki-sync` and the `docs-wiki` composition**, over
  `[docker, shell]`. `shell` renders the job with no `image:` key, which is what
  a shell-executor runner needs: it ignores `image:` and runs the script on the
  host.

  GitLab cannot omit a key conditionally and an empty value is not absence.
  `image: ''` and `image: {name: ''}` are both rejected with
  `image name can't be blank`, measured against
  `POST /api/v4/projects/:id/ci/lint` on 18.9.1-ee. The component therefore
  carries two hidden parents, one with `image:` and one without, and the job
  extends whichever the input names.

- **`docs-wiki-sync` reconciles its own webhook and pipeline trigger token on
  every default-branch run**, gated by the new `webhook-reconcile` input
  (`on` | `off`, default `on`) on both the component and the `docs-wiki`
  composition. Quote the value in your `.gitlab-ci.yml`: bare `off` is the YAML
  boolean `false`, which GitLab rejects with ``` `false` cannot be used because
  it is not in the list of allowed options ```.

  The path a wiki edit travels to reach CI is a project setting, not a file in
  the repository: a webhook on wiki page events whose URL is that project's
  pipeline trigger endpoint, carrying a trigger token. Rename the project, move
  it to another group, change the default branch, or migrate the estate to a
  different server name, and that URL stops resolving to the project. Nothing
  fails. The hourly schedule keeps the sync job green while wiki edits quietly
  stop arriving, and the only repair was for somebody to remember to re-run
  `wiki-bootstrap.sh`. That does not scale to an estate, which is the point of
  this release.

  The step builds the URL the hook must have from the job's own
  `CI_API_V4_URL`, `CI_PROJECT_ID` and `CI_DEFAULT_BRANCH`, compares, and writes
  only on a difference. A second run reports `webhook already correct` and
  writes nothing.

  Two measured GitLab 18.9 behaviours shape it. `GET /projects/:id/hooks`
  returns the hook URL with its trigger token in clear, so the whole URL can be
  compared, and so no line the job prints may carry a URL unelided.
  `GET /projects/:id/triggers` shortens a token created by another user to four
  characters, so a token an operator created by hand cannot be reused: the
  component creates and owns its own, identified by the description `wiki-sync`
  and by its owner, and reports an operator's leftover token rather than
  deleting a credential that is not its to delete. It recognises its webhook by
  the `name` field, `wiki-sync`, because the URL is the thing being repaired.

- **`WIKI_ADMIN_TOKEN`**, optional. The reconcile step needs the `api` scope,
  which the sync itself does not: `write_repository` is enough to move the wiki
  and the branch. A project that would rather keep `WIKI_TOKEN` narrow puts an
  `api`-scope Maintainer token here instead. Absent, the step falls back to
  `WIKI_TOKEN`.

- **`docs/howto/docs-wiki-sync.md`** and **`docs/howto/consuming-the-library.md`**,
  both written for a reader who has never seen this repository. The first covers
  adopting the wiki sync end to end: what it mirrors and how conflicts resolve,
  the three-line include, every input, the token and its scope, verification,
  rolling it out across many repositories, and a troubleshooting table. The
  second explains why components and compositions are separate, the naming
  grammar, when to include which, why `ref: main` is refused, and what
  `.ci/estate.yml` and `.ci/project.yml` are for. Both are linked from
  `README.md`. The wiki-sync guide covers both of this release's inputs,
  including what a shell executor changes and how pyyaml is resolved on a host
  with no index.

- **`tools/ci-local.py`**, which runs one component job's script in the image the
  component pins, against a local checkout. It resolves the job through
  `tools/resolve/`, the same package the CI Lint tests render with, so a local
  render is the harness's own answer rather than a second implementation. The
  resolvers moved out of `tests/pipelines/` into `tools/resolve/` for that
  reason; nothing about what they resolve changed.

- **`docs/howto/local-testing.md`** and **`docs/howto/new-admin-quickstart.md`**.
  The first sets out the three tiers of local check, what each proves, and the
  long list of things a green local run is silent about. The second is a
  numbered walkthrough from `git clone` to a cut release, every command run from
  a fresh clone.

### Changed

- **`execution-image` accepts `name:tag` and an empty value**, not only a
  digest. A digest is still the default and still what Renovate tracks, and
  standard 1.0.5 records the policy: digest-preferred, not digest-required. The
  estate has runners with no reachable registry; requiring a digest there does
  not make anything reproducible, it makes a pipeline that cannot be written.
  The regex still refuses anything that is not an image reference.

- **pyyaml is resolved in three steps instead of installed unconditionally.**
  `python3 -c 'import yaml'` first, so a host with python3-pyyaml installs
  nothing; then `pip install --user -r requirements.txt`, which uses whatever
  `PIP_INDEX_URL` and `PIP_TRUSTED_HOST` the estate set; then one line naming
  both options and a failed job. Nothing reaches pypi.org by default.

- **`runtime/wiki/requirements.txt` pins a version, not hashes.** Its hashes
  covered the source distribution and the two CPython 3.12 wheels, which is
  every artefact the pinned execution image could resolve. On a shell executor
  the interpreter is the host's -- EL9 ships 3.9 -- pip resolves the wheel built
  for that interpreter, and `--require-hashes` fails on exactly the air-gapped
  hosts it was meant to protect. The runtime's tests now run under 3.9 as well
  as 3.12.

- **`runtime/wiki/wiki-bootstrap.sh` now runs the same reconcile code the job
  runs**, so the first-time path and the self-healing path cannot disagree. It
  keeps creating the hourly safety-net schedule, which the job does not touch.

- **The bootstrap script no longer adds the project to the templates project's
  job-token allowlist.** That step existed so a job could clone this repository
  for its scripts at run time. Components have carried their runtime embedded
  since 1.0.0, so nothing clones anything, and the step only widened an
  allowlist for no reason.

### Upgrading from 1.0.1

Change your `ref` to `1.1.0`. Both new shapes are opt-in and every default is
unchanged, so for most consumers there is nothing else to do.

Two exceptions. If your `WIKI_TOKEN` has only `write_repository`, the webhook
reconcile step says so and leaves your webhook alone; the sync keeps working
exactly as it did. To get the self-repair, widen that token to `api` or set
`WIKI_ADMIN_TOKEN`. To decline it, pass `webhook-reconcile: 'off'`. And if your
runners are shell executors, pass `executor: shell`, which renders the job with
no `image:` key at all.

Failure semantics for the reconcile step: its first API call is a capability
probe, and an authorization failure there is reported rather than fatal. Every
API failure after that probe fails the job.


## 1.0.1 — 2026/09/16

One fix, for a failure that only appears when two jobs share a runner
concurrency slot. No input, job name or artifact path changes.

### Fixed

- **A component job whose image runs as a non-root uid could not write to its
  own build directory.** Every component job now sets
  `FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR: 'true'`, which makes the runner read
  the execution image's uid and gid and take ownership of the build directory
  for them, instead of relying on `umask 0000` to make what the helper wrote
  group-writable.

  Measured on a documentation consumer, across four jobs in two main pipelines:
  `quality-sonarqube` failed with
  `java.nio.file.AccessDeniedException: /builds/<project>/.git/objects/4c`
  each time it ran in the same slot immediately after `docs-wiki-sync`, which
  runs as root in that build directory and writes git objects into it. In a
  different slot the same configuration is green, which is why 1.0.0-rc.2
  looked clean across eleven projects. umask governs the permissions on a file
  as it is created; it cannot change the ownership of one a root process
  created earlier, and the scanner image runs as uid 1000.

  This is the second half of the class section 13.1 opened on. 1.0.0 stopped a
  consumer's `default: cache:` from being restored into a component job, which
  covered a root cache helper. It did not cover a root sibling job, a git
  object cache carried between jobs in a slot, or anything else that wrote the
  directory before the job started. A second consumer hit the same thing one
  level up: `mkdir /builds/<project>/.ci-tpl: Permission denied` in slot
  `concurrent-0` after a root job, with the inheritance rule in place and no
  cache restored. The project root itself was the unwritable thing.

  The ownership passes once, when the runner creates the build container, so
  after every predefined stage that can write the directory and before the
  job's own script. It covers the project root recursively, and the runner
  skips it entirely when the image's user is root.

  The flag is set on every component, not only on the three whose default image
  is non-root today (`quality-sonarqube` at uid 1000 and the two grype
  components at uid 65532). `execution-image` is a consumer input everywhere,
  so a default that is root says nothing about the image a job runs in, and a
  per-component rule cannot be checked without resolving images against a
  registry. On a root image the flag sets the ownership the directory already
  had. `tests/contracts/test_build_directory_ownership.py` holds it across
  every component, and standard 1.0.4 records the rule in section 13.1.

  One precondition comes with it: the execution image must carry the POSIX
  `id` utility, which the runner calls with `-u` and `-g`. All 18 images the
  components pin were checked on 2026/09/16 and every one resolves it. A
  consumer supplying an image without `id` is the one new way to break a job
  that worked.

- **The mirror trust-bundle test proved the filesystem, not the check.**
  `ci_tpl_trust_bundle` wrote the fetched bundle to a hardcoded path under
  `/usr/local/share`, so the test that a certificate-free bundle fails could
  only run where that directory is writable: green on a workstation and in a
  root container, a permission error on an unprivileged runner. A new
  `CI_TPL_CA_BUNDLE_PATH` names the path and defaults to exactly the value that
  was hardcoded, which is the only path `update-ca-certificates` reads, so no
  pipeline behaves differently. The test points it at a temporary directory.

  This fix shipped in the public copy first, where a GitHub Actions runner
  exposed it during the 1.0.0 publication.

### Upgrading from 1.0.0

Repin the ref to `1.0.1`. Nothing else changes: no input, job name, artifact
path or rule moves, and no behaviour changes in a job that was already green.

## 1.0.0 — 2026/09/16

The first stable release. It is 1.0.0-rc.1 and 1.0.0-rc.2 unchanged, plus the
work below, which is what a release needs and a candidate does not: a signal
when a runner dies, a release object for the tag itself, no clone for the jobs
that read no file, and two search steps that can no longer report a clean
result for something they failed to read.

Consumers ran rc.2 for real, across eleven projects. Twelve components and
three compositions are now `released`; everything else stays `experimental` and
its contract says why. A status here is a statement about evidence and not
about how finished something looks, and three things do not count as evidence:
a job GitLab created and nobody started, a job a rule skipped, and a lint.

`.ci/estate.yml` now records `shared_ci.approved_ref: 1.0.0`. That is the ref a
consumer pins, and section 14.2 forbids `main`.

### Added

- **`retry` on every component, and only for a dead runner.** A new typed input
  `max-retries` (0, 1 or 2, default 1) sets `retry: max`, and `retry: when` is
  `runner_system_failure` alone. A job that loses its runner runs again; a
  failed scan, gate or publish does not. `retry` is the one keyword that can
  turn a failed gate green without any of the shapes section 10.1 already
  forbids, so `tests/contracts/test_retry_policy.py` sweeps every component,
  every composition and this repository's own pipeline and rejects the
  shorthand form, a missing `when`, and any other condition.

- **A `release` job.** A protected tag matching
  `^[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$` now creates a GitLab Release with
  `release-cli`, taking its notes from this file's section for that tag. A tag
  with no section fails the job rather than publishing blank notes. Section
  14.2's catalogue publication needs this mechanism to exist; until now a
  version tag was a bare git ref. The image is pinned by digest and pulled from
  `registry.gitlab.com`, not the estate mirror: that mirror proxies Docker Hub
  and `release-cli` is published only by GitLab.

- **A `ref` argument on the lint harness.** `tests/pipelines/lint.py --ref`
  passes the branch or tag that GitLab's dry run should simulate against, which
  is what sets `CI_COMMIT_TAG`. Without it a tag-gated job looks absent
  everywhere and a tag rule cannot be tested at all.

- **`release-tag-pattern` on the three container compositions.** GitLab
  protected-tag patterns are globs and cannot express a version number, so a
  project that protects `*` built, pushed, signed and promoted on every tag it
  created. The input decides which tags are release tags, applied once in
  `workflow:` so a tag that does not match creates no pipeline at all rather
  than one whose jobs are all gated off. The default is semver with an optional
  pre-release suffix, and this repository's own `release` job uses the same
  expression, held identical by a test.

- **`lockfiles: none`.** `quality-dependency-lockfiles` refuses an empty input
  and the container compositions always instantiate it, so a project with no
  third-party dependencies could not get a green pipeline: one consumer repository is a
  stdlib-only Go module whose go.sum is empty. `none` is an explicit
  declaration, recorded as `declared: none` in the evidence. An empty value is
  still an error, and `none` alongside a path is refused.

### Changed

- **Seven jobs no longer clone the consumer repository.** `GIT_STRATEGY: none`
  now covers `container-promote-harbor`, `security-sbom-upload-dtrack`,
  `security-sync-vigil` and `security-verify-vigil`, joining the two release
  triggers and the repository audit that already had it. Each reads only a
  named producer's artifacts and the APIs it calls.
  `tests/contracts/test_git_strategy.py` holds both halves together: a job that
  gains a `working-directory` input or opens a path under the checkout fails
  there rather than in a consumer's pipeline.

- **Standard 1.0.2.** Section 14.3 now requires a deprecated component to print
  a banner at the top of its job output for the whole migration window, naming
  its replacement and the removal release. Nothing has to change to comply: no
  component is deprecated today. The section 16 record carries the reasoning.

- **A component job no longer inherits a consumer's `default:` block.** Every
  component declares
  `inherit: default: [tags, timeout, interruptible, retry, id_tokens]`, so a
  consumer's global `image`, `before_script`, `after_script`, `cache`,
  `services`, `artifacts` and `hooks` stop at the component boundary while
  runner selection and timeouts still apply. A consumer pipeline on the
  originating estate, 2026/09, is why: `quality-sonarqube` died at
  `mkdir $CI_PROJECT_DIR/.ci-tpl: Permission denied` because that project's
  `default: cache:` was restored into a container that runs as uid 1000, and
  the same component passes everywhere without a global cache. Standard 1.0.3
  records the rule in section 13.1.

- **`release-trigger-jenkins` accepts a `job-path` starting with an
  underscore.** Jenkins does, one consumer's seed job is
  `_seed-all-dsl-jobs`, and the input regex refused it, so the include failed
  pipeline creation with an input validation error the consumer could not fix.

### Released

Twelve components, on consumer pipelines named by project and pipeline id in
`.ci/compatibility.yml`: `docs-wiki-sync`, `quality-sonarqube`,
`security-secrets-gitleaks`, `security-sast-semgrep`,
`security-filesystem-trivy`, `kubernetes-validate`, `terraform-fmt`,
`terraform-validate`, `terraform-plan`, `container-list-rke2`,
`container-mirror-skopeo` and `container-export-skopeo`.

Three compositions, each having run every job it creates: `docs-wiki`,
`kubernetes-gitops` and `container-mirror`.

`quality-sonarqube` is the one component with `report-ingestion-tested`
evidence: a consumer imported two SARIF reports into SonarQube Community
Build and the job read the gate verdict back from the server.

Everything else stays `experimental`. The reasons, in the contracts:
`terraform-apply` was created as a manual job on a Terraform consumer
and nobody started it; the three `ansible-*` components were skipped behind a
red preflight on an Ansible consumer; `release-trigger-jenkins` could not create a
pipeline at all until the fix below; `quality-dependency-lockfiles` ran and
went red on the gap `none` closes; the container chain past `verify` has never
run because its only consumer builds on tags and no tag has been pushed. The
compositions `terraform-deploy`, `container-buildkit` and `terraform-module`
each had their verify half executed and their apply, build or publish half not,
which is the half each one exists for.

### Fixed

- **A search that errored was read as a search that found nothing.** grep exits
  1 when it matched nothing and 2 or more when it could not look. `scan.sh`
  counted severities with `count=$(grep -c ...) || count=0`, so a severity
  listing the job could not read produced a `scan-result.json` with every count
  at zero and passed the gate. `audit-secrets.sh` ended every search
  `2>/dev/null || true` and printed PASS for a repository it never opened. Both
  now separate the cases; the audit exits 2, because an audit that did not run
  is neither a clean verdict nor a finding. `find` gets the same treatment: a
  tree it could not walk contributes no files, and no files read as no
  findings.

- **A numeric component input rendered as `true` in the lint harness.**
  `tests/pipelines/render.py` mapped values through
  `{True: "true", False: "false"}`, and Python hashes 1 and 0 equal to `True`
  and `False`. Nothing had a numeric default until `max-retries`; the first one
  made GitLab answer "retry max is not a number".

### Upgrading from 1.0.0-rc.2

Repin the ref to `1.0.0`. No job name, artifact path or input name changes, and
every new input has a default, so an existing include needs no edit. Read the
three behaviour changes below before you do it.

Three changes are visible in a job that already worked. A job whose runner dies
now runs again instead of failing the pipeline. The four jobs above start
without a checkout, so their logs no longer open with a clone. And a project
with a `default:` block will see component jobs stop picking it up: if you
relied on a global `before_script` or `image` to prepare one, move that into
the component's inputs or into a job of your own. `default: tags:` and
`default: timeout:` are unaffected.

A consumer of a container composition that creates non-semver tags should check
`release-tag-pattern` before repinning: tags outside the pattern now create no
pipeline, where before they built and pushed.

### Upgrading from 1.0.0-rc.1 or earlier

Read the rc.2 entry below as well: it carries three run-time fixes found by
running rc.1 in real consumer projects. A consumer still on 0.2.0 or on a flat
include path should read the rc.1 entry, which maps every removed path to what
replaced it.

## 1.0.0-rc.2 — 2026/09/16

Two corrections found by running rc.1 against real consumer projects, the first
entries in `.ci/exceptions.yml`, and `main`'s container-mirror work brought onto
the 1.0.0 line. No component or composition is removed, and no input changes
meaning for a pipeline that already works.

### Fixed

- **`terraform-validate` could not validate a module repository.** The job
  always passed `-lockfile=readonly`, and a repository that ships reusable
  modules commits no `.terraform.lock.hcl` — `terraform-module-publish` excludes
  it from the archive. A Terraform consumer's merge-request pipeline failed
  all four `*:terraform-validate` jobs with "Provider dependency changes
  detected ... the lock file is read-only". A new input `lockfile-mode`
  (`readonly` | `module`, default `readonly`) selects the behaviour. `module`
  runs `terraform init -backend=false -input=false` without the readonly flag
  and still without `-upgrade`, so init records what it resolved rather than
  moving versions. An unrecognised value fails the job.

  `readonly` is the default, so every existing consumer keeps today's
  behaviour and no pinned pipeline changes. `terraform-plan` and
  `terraform-apply` act on root modules and keep the flag unconditionally.

- **`pipelines/terraform-module.yml` now passes `lockfile-mode: module`.** The
  composition sets it; the mode is a property of a module repository, not a
  choice a consumer makes, so the composition's own inputs are unchanged. A
  consumer of this composition needs no edit beyond the ref bump.

- **`security-secrets-gitleaks` could not pass.** The finding count was taken
  with `grep -o '"RuleID"' … | wc -l` inside a command substitution. The runner
  runs each script block under `set -eo pipefail`, grep exits 1 when it matches
  nothing, so a clean report aborted the job before the gate ran and before
  `scan-result.json` was written. A GitOps consumer's merge-request pipeline: the
  job logged "no leaks found" and exited 1. A clean tree aborted and a dirty tree
  gated, so the component passed never. The count now captures grep's status
  explicitly: exit 1 is no match, exit 2 or more is a read failure that still
  fails the job. `|| true` was not used: it would have turned an unreadable
  report into a clean scan.

- **Four more captures had the same shape and are now `sed`.** Each was
  `producer | grep X | sed 's/X//'` with a `${VAR:-unknown}` fallback below it
  that could never run, so a tool whose output lacked the field killed the job
  with no message: the grype database snapshot and status and the grype version
  in `security-filesystem-grype`, the grype database time in
  `security-image-grype`, and the trivy database snapshot in
  `security-filesystem-trivy`. A guard test now fails any future `CI_TPL_`
  capture that pipes through grep.

- **`docs-wiki-sync` shipped without a file it loads, and hid the failure.** The
  embed marker listed `wiki-pull.py` and not the `wiki-import.py` it loads, so
  the job copied one and died on the other. The failure was then swallowed:
  `wiki-deploy.sh` runs its cycle from an `if`, which turns `set -e` off for the
  whole call tree, and the pull function ended in a `git rev-parse` that
  succeeded. A wiki-sync consumer's job went green with
  `FileNotFoundError` in its trace and the wiki-to-repo pull skipped. The marker
  now lists the file, both python steps propagate their failure, and a lockstep
  test walks every vendored Python file's own imports and fails when the marker
  does not carry one.

### Added

- `.ci/exceptions.yml` records its first five exceptions on the originating
  estate; the public copy ships one illustrative entry in their place. Three
  of them each keep a bespoke pipeline whose
  one capability belongs to a single repository, so no shared component covers
  it. Two more cover the bespoke
  Grype database mirror, a capability two repositories share and therefore a
  candidate for a later component. One of them records that its job also runs on
  a default-branch push with no gate, which is an accepted risk rather than a
  compensating control. Owner `longy`, granted 2026/09/15, expiring 2027/09/15, each with
  its compensating controls.
- `.ci/compatibility.yml` has a non-empty `integration-tested` list for the
  first time. `docs-wiki-sync` and the `docs-wiki` composition ran on
  1.0.0-rc.1 in three wiki-sync consumers on 2026/09/16, all green. Those runs
  are what exposed the `docs-wiki-sync` defect above, so the evidence covers the
  docs-to-wiki direction only; the wiki-to-repo pull needs a re-run on rc.2.
  Every catalogue status stays `experimental`.
- `tests/contracts/exceptions.schema.json` and
  `tests/contracts/test_exceptions.py` validate that file: unique ids in the
  `^[A-Z]+-[0-9]+$` form a `.ci/project.yml` `exceptions:` list must satisfy, an
  expiry later than the grant, and a `rule` field quoting text the standard
  contains.

- **Three components and a composition merged from `main`.**
  `container-list-rke2`, `container-mirror-skopeo` and
  `container-export-skopeo`, composed by `pipelines/container-mirror.yml`, with
  `examples/container-mirror/`. They resolve an RKE2 release image list, mirror
  a reference list into a registry, and ship to an object store only the layers
  the receiving side does not already hold. `main` shipped them as tags 0.1.0 to
  0.2.0 while this branch was being built, and its one consumer pins 0.2.0.

  Credentials come from CI/CD variables by default: `REGISTRY_USER`,
  `REGISTRY_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`. A non-empty
  `vault-addr` swaps all four for a Vault JWT login and makes `vault-role`,
  `vault-kv-path` and `vault-s3-kv-path` required.

  Conformed to the standard on the way in, with no input renamed, removed or
  made required and no job name changed. What did change: the `execution-image`
  default moves to `docker.io/alpine` at the same digest,
  so the runners pull from the estate mirror and Renovate can see the pin; the
  job shell moves to `runtime/mirror/` and is embedded by
  `runtime/embed/generate.py`; the artifact archives carry names; all three
  contracts are rewritten against `tests/contracts/contract.schema.json`; the
  composition gains a contract, a branch-pipeline guard and a project record;
  and the hand-linted fixtures become tests that run against the Lint API.

  Three failure paths that used to pass now fail: a Vault login or registry read
  no longer puts a credential on a command line, a CA bundle carrying no
  certificate is an error, and a release that resolves to no reference, or a
  mirror that recorded fewer digests than it listed, ends the job.

  Evidence: `gitlab-linted` and `runtime-tested`. The `integration-tested` claim
  the two skopeo components carried is removed. It was true of tag 0.2.0, which
  its consumer ran, and it is not true of this code. The suite is 1696
  tests, up from rc.1's 1547.

### Upgrading from 1.0.0-rc.1

Repin the ref. Nothing else is required, and no input changes meaning.

Three consumers get a behaviour change from the repin alone, all of them a fix:
a module repository that was failing `terraform-validate` starts passing; a
project running `security-secrets-gitleaks` on a clean tree stops failing; and a
`docs-wiki-sync` consumer starts pulling wiki edits back into its repository,
which rc.1 silently skipped. A root-module Terraform pipeline is unaffected.

The mirror consumer is upgrading from 0.2.0, not from rc.1. Its include needs no
edit beyond the ref: every input it passes still exists, nothing it relies on a
default for became required, and its own `.gitlab-ci.yml` is now a fixture in
`tests/pipelines/fixtures/` that is resolved and linted against this tree.

## 1.0.0-rc.1 — 2026/09/15

The repository adopts `docs/gitlab-ci-agent-standard.md`. Every capability it
had is now a typed component under `templates/`, composed into complete
pipelines under `pipelines/`. The old flat include paths are deleted in this
release. There is no deprecation window and no compatibility wrapper: a
consumer pins 1.0.0, migrates, and moves off its old ref.

Nothing in this release has been integration-tested. See **Known limitations**
before pinning a consumer to it.

### Added — components

33 components, each `templates/<name>/template.yml` plus a machine-readable
`contract.yml`. All are `introduced_in: 1.0.0`, `minimum_gitlab: 18.9`, status
`experimental`. Each emits one job named `<instance>:<component-name>`.

**Container build and release**
`container-build-buildkit`, `container-build-ko`, `container-build-jib`,
`container-smoke-test`, `container-sign-attest-cosign`,
`container-promote-harbor`.

**Source verification**
`security-secrets-gitleaks`, `security-sast-semgrep`,
`security-filesystem-trivy`, `security-filesystem-grype`,
`quality-dependency-lockfiles`, `quality-sonarqube`.

**Image scanning, SBOM and supply-chain gates**
`security-image-trivy`, `security-image-grype`, `security-sbom-syft`,
`security-sbom-upload-dtrack`, `security-sync-vigil`, `security-verify-vigil`.

**Terraform**
`terraform-fmt`, `terraform-validate`, `terraform-plan`, `terraform-apply`,
`terraform-module-publish`.

**Ansible**
`ansible-lint`, `ansible-syntax`, `ansible-check`.

**Kubernetes and Helm**
`helm-validate`, `helm-package-publish`, `kubernetes-validate`.

**Docs, audits and release triggers**
`docs-wiki-sync`, `security-repository-audit`, `release-trigger-jenkins`,
`release-trigger-semaphore`.

### Added — compositions

10 catalogued compositions, each owning exactly one `workflow:` and one
`stages:`, one builder and one authoritative SBOM producer:

`container-buildkit`, `container-ko`, `container-jib`, `terraform-verify`,
`terraform-deploy`, `terraform-module`, `ansible-verify`, `helm-chart`,
`kubernetes-gitops`, `docs-wiki`.

`pipelines/repository-secret-audit.yml` also ships as a root composition for a
project whose whole pipeline is the scheduled estate audit. It carries no
`contract.yml`, so it is not in `.ci/catalog.yml` and is not covered by the
catalogue drift check.

A worked consumer example for every composition is in `examples/<workload>/`.

### Added — repository infrastructure

- `.ci/estate.yml` — the verified estate profile. Unverified facts are
  literally `unresolved` with a reason, never guessed.
- `.ci/catalog.yml` — generated inventory of every component and composition:
  inputs, emitted job names, outputs, required secret names, evidence level.
- `.ci/compatibility.yml` — tested server, runner and report-mode
  combinations, and the evidence level held per component.
- `.ci/exceptions.yml` — the record of reviewed deviations. Empty; no
  exception process exists yet.
- `tests/contracts/`, `tests/pipelines/`, `tests/runtime/` — schema and
  catalogue drift checks, CI-Lint fixtures run against the real server, and
  unit tests for every runtime helper. 1547 tests.
- `runtime/<domain>/` — the shell and Python the jobs execute, with tests. It
  is embedded into each component by `runtime/embed/generate.py`, and
  `--check` fails the pipeline when a template and `runtime/` have drifted.
- `docs/migrations/0.x-to-1.0.md` — per old include path, the replacement and
  its inputs.

### Removed

Every file below is deleted. Including one now fails pipeline creation with
`Local file ... does not exist`. The replacement emits different job names,
takes typed inputs and writes its artifacts elsewhere, so this is a migration
and not a path substitution: `docs/migrations/0.x-to-1.0.md` gives the inputs
that carry the old behaviour for each one.

| Removed path | Replacement |
| --- | --- |
| `build/buildkit.yml` | `templates/container-build-buildkit` |
| `build/ko.yml` | `templates/container-build-ko` |
| `build/jib.yml` | `templates/container-build-jib` |
| `build/helm-oci.yml` | `templates/helm-package-publish` |
| `test/smoke-test.yml` | `templates/container-smoke-test` |
| `security/gitleaks.yml` | `templates/security-secrets-gitleaks` |
| `security/semgrep.yml` | `templates/security-sast-semgrep` |
| `security/trivy-fs.yml` | `templates/security-filesystem-trivy` |
| `security/grype-fs.yml` | `templates/security-filesystem-grype` |
| `security/lockfile-check.yml` | `templates/quality-dependency-lockfiles` |
| `security/sonarqube.yml` | `templates/quality-sonarqube` |
| `security/trivy-image.yml` | `templates/security-image-trivy` |
| `security/trivy-scan.yml` | `templates/security-image-trivy` |
| `security/grype-image.yml` | `templates/security-image-grype` |
| `security/syft-sbom.yml` | `templates/security-sbom-syft` |
| `security/dtrack-upload.yml` | `templates/security-sbom-upload-dtrack` |
| `security/cosign-sign.yml` | `templates/container-sign-attest-cosign` |
| `security/vigil-notify.yml` | `templates/security-sync-vigil` |
| `security/vigil-check.yml` | `templates/security-verify-vigil` |
| `security/group-scan.yml` | `templates/security-repository-audit` |
| `security/terraform-audit.yml` | `templates/security-repository-audit` |
| `terraform/lint.yml` | `templates/terraform-fmt` |
| `terraform/validate.yml` | `templates/terraform-validate` |
| `terraform/plan-apply.yml` | `templates/terraform-plan` + `templates/terraform-apply` |
| `terraform/module-publish.yml` | `templates/terraform-module-publish` |
| `promote/harbor-promote.yml` | `templates/container-promote-harbor` |
| `promote/jenkins-trigger.yml` | `templates/release-trigger-jenkins` |
| `promote/semaphore-trigger.yml` | `templates/release-trigger-semaphore` |
| `docs/wiki-sync.yml` | `templates/docs-wiki-sync` |
| `pipelines/devsecops.yml` | `pipelines/container-buildkit.yml`, `container-ko.yml`, `container-jib.yml`, `helm-chart.yml` |

`scripts/wiki/` moved to `runtime/wiki/`, and its tests to
`tests/runtime/wiki/`. `scripts/audit-secrets.sh`, `scripts/group-scan.sh` and
`scripts/scan-state.sh` moved to `runtime/audit/`.

### Changed — what a consumer sees after migrating

Everything below is what changes when a consumer moves off a removed path and
onto the component or composition that replaced it.

- **Every job is renamed.** `<instance>:<component-name>`, so `gitleaks`
  becomes `api:security-secrets-gitleaks`. Any consumer rule, `needs:` or
  required-check that names a job by its old name must be updated in the same
  change. Branch protection required-status-check names are not in this
  repository and are not updated by it.
- **Gates are blocking.** `security/semgrep.yml` ended both scans in
  `|| true`, so its `allow_failure: false` gated nothing. The
  `security-sast-semgrep` component distinguishes the tool's findings exit code
  from an execution error, and the container compositions run it blocking. A
  project onboarding a backlog uses the documented advisory mode explicitly
  (`examples/container-buildkit-onboarding/`), which still fails on a tool
  crash, a missing database or malformed output.
- **Execution images are digest-pinned.** Every component's `execution-image`
  input defaults to `<repository>@sha256:<digest>`, mirrored through
  `docker.io/` where the mirror carries the image. A tag
  is not a digest and is no longer accepted by the input regex.
- **Terraform apply consumes a saved plan and never re-plans.** The apply job
  names its plan job explicitly and re-checks the plan's hash, age, engine
  version, lockfile hash, state id, working directory and source commit before
  applying. `allow-destroy` permits applying a plan that is already
  destructive; it does not create a destroy path.
- **The wiki runtime is vendored into the component.** `docs/wiki-sync.yml`
  cloned its scripts from this repository at a moving `WIKI_SYNC_REF` (default
  `main`), so pinning the YAML pinned nothing. `docs-wiki-sync` carries the
  code at the revision the consumer pinned.
- **`.not_for_wiki_sync` is gone.** That fragment suppressed every job in a
  consumer's pipeline on trigger and schedule sources, which cost one consumer
  its scheduled pipelines. A repository that needs to exclude its own jobs from
  wiki-triggered pipelines now declares that itself.
- **Artifacts moved.** Every artifact is written under
  `.ci-artifacts/<instance>/<component-name>/`, and the archive name carries
  the instance and component. Anything that collected an artifact by its old
  path must be repointed.
- **Publish, promote, apply and trigger jobs are gated.** They run on a
  protected tag, on `when: manual`, or both, per composition. Nothing that
  mutates an external system fires unconditionally on a push, and no component
  ships an unconditional mutation as its default.
- **`security/cosign-sign.yml` could be included on its own and could not
  lint.** `container-sign-attest-cosign` takes its producers as explicit
  inputs and the composition wires them, so standalone inclusion is no longer a
  supported mode.

### Known limitations

- **Nothing is integration-tested.** Every component and composition is
  `experimental`. The evidence held is `gitlab-linted` — the merged
  configuration compiles on gitlab.example.com 18.9.1-ee — plus `runtime-tested`
  for the components whose runtime has unit tests. No pipeline in this release
  has been executed end to end, no image has been built, signed or promoted by
  it, and `.ci/compatibility.yml` claims no `integration-tested` row.
- **`container-smoke-test` has no runner.** It needs a privileged runner and
  both instance runners are `privileged: false`. The component is complete and
  the composition leaves it off by default.
- **The GitLab licence tier is unresolved.** The licence API returns 403 to the
  token available, so `native_sarif`, `policy_enforcement` and
  `deployment_approvals` are unknown. Scan reports are consumed as artifacts
  and imported into SonarQube; nothing depends on GitLab rendering them.
- **No approved shared-CI ref exists.** `.ci/estate.yml` records
  `shared_ci.approved_ref` as `unresolved`, and every consumer pins `main`
  today, which section 14.2 forbids. Pin a reviewed SHA until a protected
  release tag is cut.
- **`.ci/estate.yml` still records `images: unresolved`.** That was true before
  the components existed; each component now pins its own digest. The estate
  profile has not been re-measured against them.
- **No estate execution images.** `images/` does not exist. Components install
  what they need at job time: `make`, `curl`, `jq`, and the pinned `cosign`,
  `kubeconform` and `kustomize` binaries, each verified by checksum. Building
  estate images is the follow-up that removes those installs.
- **No exception process.** `.ci/exceptions.yml` is an empty record and
  `security.defaults.exception_policy` in the estate profile is `unresolved`.
- **The `pipelines-lint` token is over-privileged.** The lint harness asks
  GitLab to simulate creating a pipeline on the protected default branch, which
  needs Maintainer, and the variable holding that token cannot be protected
  because a protected variable never reaches a merge-request pipeline. Giving
  the harness a `ref` input would let the token drop to Developer. Still open.
## Released before the standard

Tags 0.1.0, 0.1.1 and 0.2.0 shipped the container-mirror work while the rest
of the repository still used the flat include paths. They are kept verbatim
because a consumer pins 0.2.0 today. What they describe is now part of
1.0.0-rc.2 above.

## 0.2.0

### Changed

- `container-mirror-skopeo` and `container-export-skopeo` read their
  credentials from CI/CD variables by default: `REGISTRY_USER` and
  `REGISTRY_PASSWORD` for the registry, `AWS_ACCESS_KEY_ID` and
  `AWS_SECRET_ACCESS_KEY` for the object store. A project with no Vault can now
  run the pipeline with four masked variables and an include.
- The Vault JWT login is still there and is selected by a non-empty
  `vault-addr`, which also makes `vault-role`, `vault-kv-path` and
  `vault-s3-kv-path` required. The KV paths and every field name are inputs;
  none of them has a default naming a particular Vault layout.
- `registry` is required unless `vault-addr` is set, because with no Vault
  there is nowhere else to read the host from.
- `ca-chain-url` is now `ca-bundle-url`. Breaking: a consumer passing the old
  name gets an unknown-input error.
- `config-file` defaults to empty and an empty value resolves no RKE2 release,
  writing an empty `images.txt`. A consumer that keeps its own reference list
  no longer needs a release manifest it does not want.
- `extra-list-files` defaults to `images.txt`, so the smallest working consumer
  is one reference file and an include.
- `examples/container-mirror/` carries no site-specific endpoint, KV path or
  proxy exclusion list. A narrowed `changes-paths` names `.gitlab-ci.yml` so a
  bump of the include ref still runs the pipeline that proves it.

### Added

- `tests/pipelines/container-mirror/vault-mode.yml`, which compiles the
  composition with the Vault option on. `defaults.yml` now covers the variable
  path.

## 0.1.1

### Fixed

- `container-mirror-skopeo` validates `CI_TPL_DRY_RUN` before it copies
  anything. A project or group CI variable outranks a job variable, so an
  unguarded `CI_TPL_DRY_RUN=true` set anywhere above the job turned every
  mirror into a green run that wrote nothing. The value must now be exactly
  `true` or `false`, and `true` is accepted only on a merge request; anything
  else exits 1 naming the variable.
- `tests/pipelines/container-mirror/README.md` predicted the wrong CI Lint
  message for `defaults.yml`. On GitLab 18.9 it is `The pipeline did not run.
  Review the workflow:rules configuration for the pipeline.`

## 0.1.0

### Added

- `templates/container-list-rke2`, `templates/container-mirror-skopeo` and
  `templates/container-export-skopeo`, with a `contract.yml` each. They resolve
  an RKE2 release image list, mirror a reference list into a registry, and ship
  to an object store only the layers the receiving side does not already hold.
  Status `experimental`.
- `pipelines/container-mirror.yml`, the composition that owns `workflow:` and
  `stages: [verify, publish]` for those three, plus
  `examples/container-mirror/.gitlab-ci.yml` and the fixtures under
  `tests/pipelines/container-mirror/`.
- `.ci/catalog.yml`, covering the entries written to the standard's target
  layout. The flat directories that predate the standard are not listed.

These are the first files in this repository written to the layout in the
GitLab CI standard. Everything under `build/`, `security/`, `terraform/`,
`test/`, `promote/` and `docs/` keeps its current shape and its current include
paths.
