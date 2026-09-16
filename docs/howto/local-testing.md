# Testing a component without pushing

A change to a component ships to every consumer on their next pipeline, and the
only way to be sure it works is a real pipeline on a real runner. That loop is
minutes long, and it needs the branch pushed. This page is how to get most of
the way there in seconds, and — just as importantly — what each tier still does
not tell you.

The three tiers are cumulative. None of them replaces the one real consumer run
that section 14.1 of the standard requires before a component is `released`.

## Two measured facts that shape everything below

**1. The CI Lint API cannot run anything, because it does not tell you what to
run.** With `include_jobs: true` it returns each job's `name`, `stage`,
`before_script`, `script`, `after_script`, `tag_list`, `environment`, `when` and
`allow_failure`, and without `dry_run` also `needs`, `only` and `except`. It
returns **neither `image:` nor `variables:`**, in either mode — measured against
a GitLab 18.9.1-ee server on 2026/09/16. A runner needs both. So the lint is a
**cross-check on the merged configuration, never a source for a runner**, and
`tools/ci-local.py` resolves the job locally instead of asking the server for it.

**2. `gitlab-ci-local` cannot run a single component here without
`--json-schema-validation=false`.** Its bundled schema is GitLab 17.7 and
rejects `id_tokens` in `inherit: default:` — which **all 36 components set** —
with *"'4' property must be one of [after_script, artifacts, …] (found
id_tokens)"*. Measured on 4.75.1. Disabling validation to get past a stale
schema also disables it for the mistake you are actually hunting, and that is
exactly why a repo-native runner exists alongside it rather than instead of it.

Neither fact is a complaint about either tool. They are the two constraints that
decide which tier answers which question.

| Tier | Command | Answers |
|---|---|---|
| 1. Runtime unit tests | `python3 -m pytest -q tests/runtime/` | does the shell the template embeds behave |
| 2. Job in its image | `python3 tools/ci-local.py … --job …` | does the whole job script run, and is its exit code right |
| 3. Merged-config lint | `python3 -m pytest -q tests/pipelines/` | does GitLab compile the graph, and which jobs appear |

## The three commands

```bash
# 1. The embedded shell, under stubbed tools. No docker, no network. Seconds.
GIT_CONFIG_GLOBAL=/dev/null python3 -m pytest -q tests/runtime/

# 2. The job's own script, in the image the component pins, against a checkout.
python3 tools/ci-local.py --template quality-dependency-lockfiles \
  --input instance=demo --input lockfiles=none \
  --job demo:quality-dependency-lockfiles --checkout ~/src/myapp

# 3. The merged configuration, compiled by the server. Lints, runs nothing.
export GITLAB_URL=https://gitlab.com GITLAB_PROJECT_ID=<your copy> GITLAB_TOKEN=...
python3 -m pytest -q tests/pipelines/
```

## Tier 1 — the runtime unit tests

`tests/runtime/` extracts each embedded block out of `templates/*/template.yml`
and runs it under a stub toolchain. It tests the copy the job carries, not the
file under `runtime/`, which is the distinction that matters: embedding is where
shell changes meaning, because a literal newline or a heredoc terminator gains
the YAML block scalar's indentation.

What it does not cover: anything about the image. A script that is correct shell
and calls a binary the pinned image does not have passes here and fails in CI.

### Why the runtime lives as real files

A component's job script is embedded in its `template.yml`, so it would be
possible to author the shell there and skip `runtime/` entirely. It is not, for
a reason that has nothing to do with taste: **a script embedded in a YAML block
scalar is invisible to every analyser.** SonarQube and shellcheck scan
`runtime/` and see `.sh` and `.py` files; they see the template as YAML and read
a long string. Authoring in the template would silently drop every scan that
covers this repository's shell.

So edit `runtime/<domain>/`, then regenerate with
`python3 runtime/embed/generate.py --write`. The `--check` mode is the gate that
keeps the embedded copy in lockstep, and the `embedded-runtime` CI job fails on
drift — which is the one thing that stops `runtime/` quietly becoming
documentation for a template that has moved on without it.

**Never edit the embedded copy inside a template.** The next `--write`
overwrites it, and in the meantime the file the analysers scan and the script
the job runs say different things.

## Tier 2 — the job in its own image

Two tools do this. They answer the same question and fail differently.

### `tools/ci-local.py` — this repository's own runner

It resolves a component, a composition or a consumer `.gitlab-ci.yml` using
`tools/resolve/`, the same package the lint tests render with, so what `--show`
prints is what the tests lint. Then it runs the job's `before_script` +
`script` in the job's `image:`, with the variables a runner would set.

```bash
# Which jobs does this produce?
python3 tools/ci-local.py --composition docs-wiki --input instance=docs --list

# What would actually run? Prints the script, runs nothing.
python3 tools/ci-local.py --composition docs-wiki --input instance=docs \
  --job docs:docs-wiki-sync --show

# Run it.
python3 tools/ci-local.py --template security-secrets-gitleaks --input instance=demo \
  --job demo:security-secrets-gitleaks --checkout ~/src/myapp

# A consumer's own file, including the `include: project:` of this repository,
# resolved against this working tree: what the next release does to them.
python3 tools/ci-local.py --config ~/src/myapp/.gitlab-ci.yml --list

# Cross-check the same merged configuration against the server.
export GITLAB_URL=https://gitlab.com GITLAB_PROJECT_ID=<your copy> GITLAB_TOKEN=...
python3 tools/ci-local.py --config ~/src/myapp/.gitlab-ci.yml --lint
```

`--lint` sends every resolved job, hidden parents included. A component that
selects between two shapes, as `docs-wiki-sync` does for the docker and shell
executors, has a public job that `extends:` a hidden one, and a payload without
it is rejected with *"unknown keys in `extends`"* — a valid configuration that
looks broken.

A job whose `needs:` producer wrote artifacts takes them from a previous run:

```bash
python3 tools/ci-local.py --template security-sbom-upload-dtrack --input instance=demo \
  --job demo:security-sbom-upload-dtrack --checkout ~/src/myapp \
  --artifacts-from ~/src/myapp-sbom/.ci-artifacts
```

Credentials go in an env file, never on the command line. Start from
`tools/ci-local.env.example`.

**One fixture set, two drivers.** The consumer fixtures under
`tests/pipelines/fixtures/` are the files the CI Lint harness posts to the
server, and `--fixture` runs those same bytes locally:

```bash
python3 tools/ci-local.py --fixture container-mirror --list
python3 tools/ci-local.py --fixture container-mirror --job lab:container-list-rke2 --show
```

Keeping one set means the local loop and the gate cannot drift into disagreeing
about a consumer's shape. `tests/tools/` asserts that every
`*.consumer.gitlab-ci.yml` under `fixtures/` resolves through this resolver too,
so a fixture cannot end up exercised by only one of the two. Files there that
are not consumer pipelines, such as the `security-image-components.yml`
component matrix, are deliberately not offered.

Two environment facts are worth knowing before the first run:

- **The checkout must be under `$HOME`.** Colima's VM only shares paths beneath
  it. A checkout elsewhere mounts as an empty directory and the job fails saying
  the source is missing, which reads like a bug in the component. The runner
  warns when it sees one.
- **`CI_COMMIT_TAG` is unset, not empty**, because that is what a runner does on
  a branch pipeline. Set it in the env file only to exercise a tag-gated path.

### `gitlab-ci-local` — the third-party runner

[`gitlab-ci-local`](https://github.com/firecow/gitlab-ci-local) (`brew install
gitlab-ci-local`) runs a whole pipeline rather than one job: it honours the
`needs:` graph, passes artifacts between jobs and starts `services:`. It is the
better tool when the question is about the graph.

It needs two flags to run anything in this repository, measured on 4.75.1:

```bash
export GCL_IGNORE_PREDEFINED_VARS=FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR
gitlab-ci-local --json-schema-validation=false demo:quality-dependency-lockfiles
```

`--json-schema-validation=false` is not optional. Its bundled schema is from
GitLab 17.7 and rejects three things the server accepts, the first of which
every component here sets:

- `id_tokens` in `inherit: default:` — *"'4' property must be one of
  [after_script, artifacts, …] (found id_tokens)"*. All 36 components set it.
- `tags: []` — *"property 'tags' must not have fewer than 1 items"*. The
  `runner-tags` input defaults to an empty array.
- `inherit.default` as an array at all — *"'default' property type must be
  boolean"*.

Disabling schema validation to get past a stale schema also disables it for the
mistake you are actually hunting, which is the reason `tools/ci-local.py` exists
alongside it rather than instead of it.

Two more things it gets wrong locally: `include: local:` does not follow a
symlinked directory (*"Local include file cannot be found"* — copy the tree
instead), and with no `origin` remote `CI_PROJECT_PATH` becomes
`fallback.group/fallback.project`.

It also derives the predefined variables from your local git checkout, so a
rule keyed on `CI_PIPELINE_SOURCE`, `CI_MERGE_REQUEST_*` or
`CI_COMMIT_REF_PROTECTED` is decided by whichever branch you happen to be on.
Pin them explicitly; `tools/ci-local.env.example` ships each one with a comment
saying what it changes.

### How a private `include: project:` actually resolves

Both `include: project:` and `include: component:` do work against a private
GitLab, with no token, measured cold on 4.75.1 (522 ms and 496 ms from an empty
cache in a fresh repository). They resolve over **SSH, as you**, not as CI:

```
git archive --remote=ssh://git@gitlab.example.com:22/platform/gitlab-ci-templates.git \
  1.0.0 templates/quality-dependency-lockfiles/template.yml | tar -xC ...
```

Break SSH and the include fails with *"Project include could not be fetched"*,
which is the proof that SSH, not `CI_JOB_TOKEN`, is what gates it. `CI_JOB_TOKEN`
is never populated locally and could not fetch a private include if it were.

**None of that makes `include: project:` the right thing for the local loop**,
and the reason is not capability. A `project:` include at a released ref fetches
the released file from the server, which is precisely *not* the change you are
testing. Use `include: local:` against the worktree, where the file you edited
is the file that runs. The pushed self-test and the consumer fixtures are where
the project reference earns its keep, because there the question is whether a
consumer pinned to a real tag still resolves.

`tools/ci-local.py` takes the other half of that deliberately: a `project:`
include naming *this* repository resolves against **the working tree**, ignoring
the ref, so a consumer fixture pinned to `1.0.0` shows what the next release
would do to it. It refuses a `project:` include of any other repository, because
nothing on disk can answer for one.

### What tier 2 does not prove

Neither runner is a GitLab Runner. A green job here is silent about:

- **`rules:`** — nothing evaluates them. `tools/ci-local.py` runs the job you
  name whether or not a real pipeline would create it.
- **`services:`** — `tools/ci-local.py` starts none. `gitlab-ci-local` does.
- **`id_tokens:` and JWTs** — minted by the server per job. There is no local
  equivalent, and a personal token substituted for `CI_JOB_TOKEN` has different
  scopes, so it exercises a path the job will not take.
- **Masked and protected variables** — a protected variable is absent from an
  unprotected branch's pipeline, and that absence is a common real failure that
  cannot happen locally.
- **`needs:` and `artifacts:` as the runner does them** — `--artifacts-from`
  copies a directory in. It does not test the upload, the expiry, the artifact
  name, or a producer that did not run.
- **Build-directory ownership** — `FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR` exists
  because a non-root image inherits a build directory a previous job in the same
  concurrency slot wrote as root. A fresh local mount never reproduces it.
- **The shell executor** — a component with no `image:`, which is
  `docs-wiki-sync` with `executor: shell`, has nothing for docker to run. Its
  evidence comes from `tests/runtime/wiki/` and a real run.

## Tier 3 — the merged configuration

`tests/pipelines/` compiles configuration through the CI Lint API. It is the
only check here that resolves `include:` for real, and the only one that proves
`needs:` resolves, the stages are declared, and which job names appear. It
executes nothing: report **linted**, never *ran*.

The API returns each job's `name`, `stage`, `before_script`, `script`,
`after_script`, `tag_list`, `environment`, `when` and `allow_failure`, and
without `dry_run` also `needs`, `only` and `except`. It returns neither `image:`
nor `variables:` in either mode, measured against 18.9.1-ee on 2026/09/16. That
is why `tools/ci-local.py` resolves the job locally instead of asking the server
for it, and offers `--lint` as a cross-check rather than a source.

## The rule that none of this changes

A component is not `released` until one real consumer pipeline has run it. Every
tier above is a faster way to arrive at that run with fewer things wrong, not a
substitute for it. Section 14.1 defines the evidence labels and they are
cumulative: tier 1 earns `runtime-tested`, tier 3 earns `gitlab-linted`, and
neither earns `integration-tested`. Tier 2 earns `runtime-tested` against the
real image, which is stronger than a stubbed run and still short of a pipeline.
