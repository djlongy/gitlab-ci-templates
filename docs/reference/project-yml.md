# `.ci/project.yml`, the consumer project record

A consuming project keeps one of these. It records what that project adopted from
this repository: which composition, under which instance names, with which inputs,
what it still defines itself, and what the adoption removed.

It is metadata, not GitLab configuration. Nothing reads it at pipeline time, no
input is injected from it, and a value that appears here but not in the committed
`.gitlab-ci.yml` has no effect on anything. Section 4 of
`docs/gitlab-ci-agent-standard.md` is the contract; `tests/contracts/project.schema.json`
is the machine-readable form, and `tests/contracts/test_project_yml.py` validates
every record under `examples/` against it and against the pipeline beside it.

Two things make it worth keeping. An agent or a reviewer arriving at a project
reads one short file instead of reverse-engineering the adoption from the YAML,
and a decision that would otherwise live only in a merge request description —
why one gate is advisory, why a job was dropped — stays with the project.

## Fields

| Field | Required | What it holds |
|---|---|---|
| `schema_version` | yes | `1`. |
| `project` | yes | The consuming project's own GitLab path, group included. |
| `shared_ci.project` | yes | The shared CI project the pipeline includes, normally `platform/gitlab-ci-templates`. |
| `shared_ci.ref` | yes | The ref the committed pipeline pins: a full commit SHA or a protected release tag. A branch name is not acceptable (section 14.2), and the schema refuses one. |
| `workload.name` | yes | A composition name where one fits, otherwise the shape of the work: `python-tests`, `docs-site`, `artifact-mirror`, `machine-image`. |
| `workload.adoption` | yes | `composition`, `bespoke`, or `held`. |
| `workload.reason` | when not `composition` | Why this project has no composition, or what its adoption is waiting on. |
| `compositions[].file` | yes | The composition path as the include writes it, for example `/pipelines/container-buildkit.yml`. |
| `compositions[].instances` | yes | Every component instance the include names. One for most compositions; `repository-secret-audit` names two. |
| `compositions[].inputs` | no | The inputs the include sets explicitly, copied as written. |
| `bespoke_jobs[]` | no | Jobs the project still defines itself, each with `name`, `stage` and the `reason` no component covers it. |
| `retired_jobs[]` | no | Jobs the adoption removed, each with `name` and `reason`. |
| `environments[]` | no | Deployment targets, with `target`, `state_id`, `deployment_mode` or `url` where they apply. |
| `exceptions[]` | no | Identifiers of recorded exceptions this project relies on. Ids only; the exception itself lives in `.ci/exceptions.yml`. |
| `notes` | no | What no field above holds. |

`adoption` is the field that carries the interesting cases:

- **`composition`** — the project includes a shared composition and the record
  says which.
- **`bespoke`** — no composition fits and the project keeps its own pipeline. The
  reason is what stops the next reviewer re-opening the question from scratch.
- **`held`** — adoption is blocked. A shared composition cannot express something
  the project's current pipeline does, and adopting it would change a gate nobody
  agreed to change. The reason quotes what is blocked.

## Example

```yaml
---
schema_version: 1
project: demo/api
shared_ci:
  project: platform/gitlab-ci-templates
  ref: 1.0.0
workload:
  name: container-buildkit
  adoption: composition
compositions:
  - file: /pipelines/container-buildkit.yml
    instances:
      - api
    inputs:
      instance: api
      image-repository: registry.example.com/dev/platform/api
      semgrep-rules: ci/semgrep-rules.yml
      lockfiles: go.sum
      build-sources: tags-only
retired_jobs:
  - name: trivy-fs
    reason: >-
      Replaced by api:security-filesystem-trivy, which writes scan-result.json
      and fails on a scanner error instead of ending in `|| true`.
environments:
  - name: prod
    target: registry.example.com/prod
```

A record for every shipped example is under `examples/<workload>/.ci/project.yml`,
including a project that keeps a bespoke job and one that relaxes a single gate.

## Writing one

1. Copy the record from the example whose composition you are adopting.
2. Set `project`, and set `shared_ci.ref` to the ref your `.gitlab-ci.yml` pins.
   The two must agree; a test in this repository checks that for the examples,
   and a consuming project should check it the same way.
3. Copy the inputs from your own include, as written. Defaults are not restated:
   what belongs here is what the project chose.
4. List what you kept and what you dropped. A job that disappeared in an adoption
   with no line in `retired_jobs` is a job nobody decided to lose.
