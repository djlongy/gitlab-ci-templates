# gitlab-ci-templates

GitLab CI components and complete pipelines for builds, infrastructure checks, security scans and wiki sync.

## Requirements

- GitLab 18.9 or newer; component contracts record runner and runtime requirements.
- For the wiki example: GitLab Runner 16.0+ with an untagged Docker executor, access to GitLab, Docker Hub and your Python package index.

## Usage

This example syncs your repository's `docs/` folder with its GitLab wiki.
Complete the preconditions below in a project that does not already have a pipeline.

1. Save this as your consumer project's `.gitlab-ci.yml`, replacing the project path with your GitLab copy of this library:

   ```yaml
   include:
     - project: 'platform/gitlab-ci-templates'
       ref: '1.4.0'
       file: '/pipelines/docs-wiki.yml'
   ```

2. Stage your pipeline and documentation: `git add .gitlab-ci.yml docs/index.md`
3. Commit them on your default branch: `git commit -m "ci: sync documentation with the project wiki"`
4. Push the commit: `git push origin HEAD`

## Preconditions

- Import this library, including tag `1.4.0`, into your GitLab; protect the tag and grant consumers read access. Compositions use nested local includes and require `include:project`.
- Enable your consumer project's wiki and create `docs/index.md` with the content you want on its home page.
- Set a masked `WIKI_TOKEN` CI/CD variable using a group access token with Maintainer access and `api` scope. Its identity must be allowed to push to the default branch. A protected variable requires a protected default branch.
- Set `PIP_INDEX_URL` to a package index your runner can reach for the pinned Python dependencies, or supply an execution image with PyYAML installed.

## Behaviour

Each composition owns `workflow:` and `stages:`; include one per pipeline.
For an existing pipeline, use a component from `templates/` and supply its stage and required inputs.
An `instance` prefixes job names and artifact paths when the component produces artifacts.

Wiki sync runs on default-branch pushes, wiki triggers and schedules when `WIKI_TOKEN` is available.
It imports wiki edits into your default branch, regenerates the wiki from `docs/`, and reconciles its webhook and pipeline trigger.
It does not create a periodic schedule.

## Contents

- [`pipelines/`](pipelines/) — complete compositions; each YAML file declares its inputs in `spec:inputs`.
- [`templates/`](templates/) — individual components; each `contract.yml` declares jobs, outputs, secrets and execution requirements.
- [`.ci/catalog.yml`](.ci/catalog.yml) — generated component and composition inventory, including availability and execution evidence.
- [`.ci/estate.yml`](.ci/estate.yml) — example environment profile to replace in your fork; it does not configure GitLab or inject inputs.
- [`examples/`](examples/) — consumer pipeline configurations.
- [`runtime/`](runtime/) — job helpers embedded in the templates.
- [`images/`](images/) — execution-image build definitions.
- [`tools/ci-local.py`](tools/ci-local.py) — local execution of one component job in its pinned image.
- [`AGENTS.md`](AGENTS.md) — repository editing and verification commands.

## Expected result

You know it works when `docs:docs-wiki-sync` succeeds and your wiki home page contains `docs/index.md`.
Verify repository access with `git ls-remote '<your-wiki-clone-url>' HEAD`, substituting your wiki's clone URL and using your normal Git credentials.
