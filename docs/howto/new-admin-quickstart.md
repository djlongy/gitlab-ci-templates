# New admin quickstart

Follow this once, top to bottom, to go from a fresh clone to a change you can
defend. Every command was run from a fresh clone of this repository on
2026/09/16. Each step gives the command, the line you should see, and the thing
that usually goes wrong.

This is the public copy of a library that runs on a private GitLab, so the two
steps that need a GitLab server — the merged-configuration lint and the release
job — say what to point them at. Everything else runs offline on your machine.

## 1. Clone and set up

```bash
git clone https://github.com/djlongy/gitlab-ci-templates.git
cd gitlab-ci-templates
python3 -m venv .venv && . .venv/bin/activate
pip install -q pyyaml jsonschema pytest yamllint   # there is no requirements file; this is what CI installs
docker info >/dev/null 2>&1 || echo "start Docker; step 4 needs it"
```

Docker is needed only by step 4, which runs a job's script in the job's own
image. Everything else is pure Python.

**Commonly wrong:** on macOS with Colima, only paths under `$HOME` are shared
with the VM. A checkout in `/tmp` or `/private/tmp` mounts into a container
**empty**, and the job fails claiming your source is missing. Keep every
checkout under your home directory.

## 2. Run the unit tests

```bash
GIT_CONFIG_GLOBAL=/dev/null python3 -m pytest -q tests/contracts/ tests/runtime/ tests/tools/
```

Expect `1608 passed, 4 skipped, 1 xfailed`; the count grows as components land.
`tests/pipelines/` is left out because it needs a GitLab server; step 6 covers
it.

**Commonly wrong:** `GIT_CONFIG_GLOBAL` must be set per command, not exported.
Your own checkout's git reads it too and rejects `/dev/null` with
`bad config line 1`.

## 3. Run the drift checks

```bash
python3 runtime/catalog/generate.py --check
python3 runtime/embed/generate.py --check
```

Both print **nothing** and exit 0: silence is the pass, so check `echo $?` if
unsure.

## 4. Run one component job locally

```bash
mkdir -p ~/demo-checkout
python3 tools/ci-local.py --template quality-dependency-lockfiles \
  --input instance=demo --input lockfiles=none \
  --job demo:quality-dependency-lockfiles --checkout ~/demo-checkout
```

A passing run ends:

```
lockfiles: PASS: the project declares no lockfile (lockfiles: none); nothing to validate.
ci-local: demo:quality-dependency-lockfiles exited 0
ci-local: artifact .ci-artifacts/demo/quality-dependency-lockfiles/lockfile-result.json
```

`exited 0` is the job's own exit code, so `exited 1` means the gate fired: a
working gate, not a broken tool.

**Commonly wrong:** pointing `--checkout` at this repository; the job runs
against a *consumer's* tree. [`local-testing.md`](local-testing.md) says what a
local run does not prove.

## 5. Change a runtime script

Components embed the shell they run, so there are two copies. **Edit `runtime/`,
never the embedded copy** — the next `--write` overwrites it, and the linters
only ever scan `runtime/`.

```bash
$EDITOR runtime/scan/lockfiles.sh                       # 1. edit the real file
python3 runtime/embed/generate.py --check               # 2. now fails, on purpose
python3 runtime/embed/generate.py --write               # 3. re-embed
GIT_CONFIG_GLOBAL=/dev/null python3 -m pytest -q tests/runtime/scan/   # 4. 94 passed
python3 tools/ci-local.py --template quality-dependency-lockfiles \
  --input instance=demo --input lockfiles=requirements.txt \
  --job demo:quality-dependency-lockfiles --checkout ~/demo-checkout   # 5. your edit runs
```

Step 2 prints `embedded runtime is stale in: templates/…/template.yml` and
exits 1. Step 3 prints `refreshed: templates/…/template.yml`. Commit **both**
files; the `embedded-runtime` job fails if you commit only one.

**Commonly wrong:** believing the edit landed because the tests passed. Make the
local job take the branch you changed: above, `lockfiles: none` and a real
lockfile print different lines, so only the second proves the round trip.

## 6. Lint the merged configuration

This is the only check that resolves `include:` for real, and it needs a GitLab
server holding a copy of this repository. Point it at yours:

```bash
export GITLAB_URL=https://gitlab.com          # or your self-managed 18.x server
export GITLAB_PROJECT_ID=<id of your copy>    # default: .ci/estate.yml shared_ci.project_id
export GITLAB_TOKEN=...                       # personal or group token, api scope
python3 tests/pipelines/lint.py .gitlab-ci.yml
python3 tests/pipelines/lint.py .gitlab-ci.yml --ref <a tag on your copy>
```

The first prints `valid .gitlab-ci.yml — 6 job(s): validate:templates, …`. The
second makes GitLab simulate against a real ref, so a tag-gated job such as
`release` appears only when you ask for a tag. That difference is the point.

Say **linted**, never *ran*. Nothing here executes a pipeline.

**Commonly wrong:** the ref must already exist on the server, and `CI_JOB_TOKEN`
is not accepted by this endpoint. Without `GITLAB_TOKEN` the `tests/pipelines/`
tests skip, and a green run with everything skipped proves nothing about merged
configuration.

## 7. Commit and open the pull request

Never commit to `main`; branch, open a PR, squash-merge. Subjects are plain
imperative sentences (`Run the wiki sync on a shell executor with no image`),
not `feat(scope):`. Before pushing, run the full gate:

```bash
yamllint -d "{extends: default, rules: {line-length: disable, truthy: disable}}" \
  .gitlab-ci.yml .github .ci tests runtime templates pipelines examples
python3 runtime/catalog/generate.py --check && python3 runtime/embed/generate.py --check
GIT_CONFIG_GLOBAL=/dev/null python3 -m pytest -q tests/
```

The GitHub Actions workflow runs exactly those on every push and pull request.
A GitLab copy of this repository runs the same checks as a pipeline instead.
The MR pipeline runs six jobs: `validate:templates`, `contracts`,
`pipelines-lint`, `embedded-runtime`, `runtime-tests` and `tools-tests`.

**Commonly wrong:** `pipelines-lint` is gated on `$GITLAB_TOKEN` existing as a
masked CI variable. Absent, the job does not run, and a pipeline missing it is
not one that passed it.

## 8. Cut a release, and pin it

A release is a version tag on `main`. On a GitLab copy the `release` job runs
only for a tag matching `^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?$` **and**
`CI_COMMIT_REF_PROTECTED == "true"`, so only a Maintainer can publish one. It
builds the notes from that tag's `## <tag>` section of `CHANGELOG.md` and fails
if it is missing or empty, so add that section in the pull request, before
tagging.

Consumers then pin the tag `.ci/estate.yml` names as `shared_ci.approved_ref`,
never `main`:

```yaml
include:
  - project: 'platform/gitlab-ci-templates'
    ref: 'REPLACE_WITH_APPROVED_SHARED_CI_REF'   # the approved_ref in .ci/estate.yml
    file: '/pipelines/docs-wiki.yml'
    inputs:
      instance: docs
```

`include: project:` resolves against **your** GitLab, not GitHub, so that path
needs this repository mirrored into your own GitLab first.
[`consuming-the-library.md`](consuming-the-library.md) covers that and the
remote-include alternative for a quick trial.

**Commonly wrong:** presenting `main`, or a tag not yet cut, as an approved ref.
Read `shared_ci.approved_ref` out of `.ci/estate.yml` rather than trusting a
version you saw somewhere. A version heading in `CHANGELOG.md` is written in the
pull request, before the tag exists, so it is not evidence of a release; and a
tag that does exist is not necessarily the approved one.
