# AGENTS.md — gitlab-ci-templates

For GitLab pipeline or shared-template work, read `docs/gitlab-ci-agent-standard.md`, the approved `.ci/estate.yml` and `.ci/catalog.yml` at the selected revision before editing. Use the defined layout, component inputs, job names and artifact contracts. Prefer released compositions. Do not invent unavailable components or weaken gates. Validate the changed graph and report execution evidence precisely.

## What is in place today

The standard is implemented. `templates/` holds the components, `pipelines/`
holds the compositions and `.ci/catalog.yml` is generated from their contracts.
The flat per-tool directories that predated the standard (`security/`, `build/`,
`terraform/`, `test/`, `docs/`) are gone; `docs/migrations/0.x-to-1.0.md` maps
each removed path to what replaced it.

Read `.ci/estate.yml` before writing anything that touches the estate. It ships
describing a fictional estate, because this is the public copy of a library that
runs elsewhere; a fork replaces it. Its `unresolved` values are facts nobody has
verified, not blanks to fill in by guessing.

`.ci/estate.yml` records `shared_ci.approved_ref`, the protected release tag a
consumer pins. Section 14.2 forbids `main`, so do not present a branch as an
approved ref, and do not present a release tag that has not been cut yet as one
either.

## Verification commands

Run these from the repository root. None of them executes a pipeline; say
"linted", never "ran", when reporting on the lint results.

```bash
# YAML across the repository, matching what CI runs.
yamllint -d "{extends: default, rules: {line-length: disable, truthy: disable}}" \
  .gitlab-ci.yml .ci/ tests/ runtime/ templates/ pipelines/ examples/

# Estate profile schema, catalogue drift, component contract schema.
python3 -m pytest -q tests/contracts/

# Rebuild the catalogue after adding or changing a template.
python3 runtime/catalog/generate.py --write

# Merged-configuration lint against a real server. Needs GITLAB_TOKEN with api
# scope, plus GITLAB_URL and GITLAB_PROJECT_ID naming the project that holds
# this repository. Without a token these tests SKIP, and a green run with
# everything skipped proves nothing.
python3 -m pytest -q tests/pipelines/

# Lint one file directly.
python3 tests/pipelines/lint.py path/to/.gitlab-ci.yml

# Runtime helper tests, and the drift gate for the runtime the templates carry
# (regenerate it with: python3 runtime/embed/generate.py --write).
GIT_CONFIG_GLOBAL=/dev/null python3 -m pytest -q tests/runtime/

# Run one component job's script in the image the component pins. This one DOES
# execute: it is the only command here that does, and it executes one job
# against a local checkout, never a pipeline.
python3 tools/ci-local.py --template <component> --input instance=demo \
  --job demo:<component> --checkout ~/src/<consumer>
```

`docs/howto/local-testing.md` is the guide to that loop: the three tiers, what
each proves, and the long list of things a green local run is silent about.
Nothing there replaces the one real consumer pipeline run a component needs
before it can be called `released`.

The CI Lint API will not accept `CI_JOB_TOKEN`: it is not on that token's
[documented resource allowlist](https://docs.gitlab.com/ci/jobs/ci_job_token/).
`GITLAB_TOKEN` must be a personal or group access token, masked when set as a CI
variable.

## Evidence labels

Section 14.1 defines them and they are cumulative, not interchangeable:
`source-reviewed`, `yaml-parsed`, `gitlab-linted`, `runtime-tested`,
`integration-tested`, `report-ingestion-tested`. A green `tests/pipelines/` run
earns `gitlab-linted` and nothing above it.

Three things that are not a run, and none of them promotes a component: a job
GitLab created and nobody started, a job a rule skipped, and a lint.
