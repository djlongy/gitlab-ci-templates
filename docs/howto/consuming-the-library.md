# How this library is put together, and how to consume it

For someone who has just been pointed at this repository and told to use it.

Throughout, the shared CI project is written `platform/gitlab-ci-templates` and
the GitLab server `gitlab.example.com`. Substitute the real ones.

## The shape of it

Everything here is one of two things.

A **component** does one thing. It has a public interface (`spec:inputs`) and it
emits exactly one job, or a small set of jobs that cannot be separated. It does
not decide when it runs, what stage it runs in, or what it depends on. Those are
the caller's business. Components live in `templates/<name>/`.

A **composition** is a whole pipeline. It owns `workflow:` (which pipelines
exist at all), `stages:` (the order things happen in), which components are
included, what each one is called, and the `needs:` edges between them. It
forwards its own inputs down into the components. Compositions live in
`pipelines/<workload>.yml`.

A consumer's `.gitlab-ci.yml` includes **one** composition and passes inputs.
No stages, no job bodies, no jobs disabled.

The split exists because the two halves change for different reasons. What a
Trivy scan does is the same everywhere and changes when Trivy changes. Whether
it blocks a release, and what must have passed before it runs, is a decision
about your pipeline, and hard-coding that decision into the scanner is how a
shared template ends up with fourteen boolean inputs that nobody can reason
about.

## Naming

Component names are `<domain>-<capability>[-<tool>]`, all lowercase:
`container-build-buildkit`, `security-image-trivy`, `docs-wiki-sync`,
`terraform-plan`. The domain is the thing being acted on, the capability is the
action, and the tool suffix appears only where it distinguishes real
alternatives. No team, environment or version ever appears in a name, because
names are API identifiers and renaming one is a breaking change.

Every component takes an `instance` input, and the job it emits is called
`<instance>:<component-name>`. So `api:container-build-buildkit` builds the API
image and `frontend:security-image-trivy` scans the frontend one. That is how
one project can run the same component twice without two jobs colliding, and how
you can see at a glance which chain of jobs belongs to which workload. Artifacts
follow the same convention: `.ci-artifacts/<instance>/<component-name>/`.

## Composition or components

Include a composition when the pipeline **is** the workload: a repository whose
whole purpose is building one image, or mirroring its docs to its wiki. You get the
stages, the ordering and the gates already wired.

Include components directly when the repository has checks of its own. A
repository that already has its own `stages:` and `workflow:` cannot also take a
composition's, since GitLab merges includes into one configuration and each of
those keys can only be defined once. Adding a composition to such a repository
either conflicts or silently replaces what was there.

The documentation repository in this estate is the worked example. It runs a
markdown lint, a link check and a code-quality analysis on every merge request,
and it owns its own `stages:`. The wiki composition's `workflow:` admits only
default-branch push, trigger and schedule pipelines, so adopting the composition
would delete every one of those checks. It therefore includes
`/templates/docs-wiki-sync/template.yml` directly, with a `stage:` of its own
choosing and its own `run-rules`. A repository with nothing in it but
documentation includes the composition and writes three lines.

If you include components directly, you take on what the composition was doing:
choosing the stage, writing the rules, and declaring which job produces any
artifact a component consumes. Read the component's `contract.yml` for what it
needs.

## Getting the library onto your GitLab

`include: project:` resolves against **the GitLab server running the pipeline**.
It cannot reach GitHub, so a project path alone is not enough when the library
lives here. There are two ways in, and they are not equivalent.

**(a) Mirror it into your own GitLab. This is the supported path.** Import or
pull-mirror this repository into your GitLab as `<group>/gitlab-ci-templates`,
protect the release tags so only a Maintainer can move one, and include by
project path at a tag:

```yaml
include:
  - project: '<group>/gitlab-ci-templates'
    ref: '1.1.1'
    file: '/pipelines/docs-wiki.yml'
    inputs:
      instance: docs
```

Everything in this repository works this way: components, compositions, the
estate profile, the release job. It is also the only path on which you control
the tag, which is what pinning is for.

**(b) Remote-include one component straight from GitHub, for a trial.** A
component template is self-contained — it carries the runtime its job executes
embedded in the YAML — so GitLab can fetch one over HTTPS and run it with no
mirror at all:

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/djlongy/gitlab-ci-templates/1.1.1/templates/docs-wiki-sync/template.yml'
    inputs:
      instance: docs
      working-directory: 'docs'
```

Your GitLab must be able to reach `raw.githubusercontent.com` outbound, and the
job still needs its own `stage:` and rules because a component does not decide
those.

Two limits to know before choosing (b):

- **A remote include is pinned by a tag on a host you do not control.** The
  fetch happens at pipeline creation, against whatever that URL serves then. A
  project-path include at a tag on your own GitLab, with protected tags, is the
  only form where nobody outside your estate can change what your pipeline runs.
- **Compositions cannot be remote-included.** `pipelines/*.yml` reach their
  components through `include: local:`, which only resolves inside a project.
  Fetched over `remote:` there is no project to resolve against, and GitLab
  18.9.1-ee rejects the pipeline outright with
  ``Local file `templates/docs-wiki-sync/template.yml` does not have project!``
  (measured through the CI Lint API, 2026/09/16, remote-including
  `pipelines/docs-wiki.yml` at a tag). The same lint of the component template
  beside it returned valid and emitted `docs:docs-wiki-sync`. So path (b) is
  components only. A whole composition needs path (a).

## Pinning, and why `main` is refused

```yaml
include:
  - project: 'platform/gitlab-ci-templates'
    ref: '1.1.1'
    file: '/pipelines/docs-wiki.yml'
```

`ref` must be a release tag or a full commit SHA. Never a branch.

A branch is a moving target. Pinned to `main`, your pipeline changes when
somebody else merges something, with no change on your side, no review, and no
way to reproduce last week's run. That is true of the YAML and it is equally
true of the code the jobs execute: a component carries its runtime embedded in
the template, generated from `runtime/` and drift-checked in CI, precisely so
that pinning the include pins the executable behaviour too. Nothing is fetched
from a moving branch at job time.

Release tags here are protected, so only a Maintainer can create or move one.
Version numbers mean what they usually mean: a patch is a compatible fix, a
minor adds a capability or an input, and a major removes or renames an
interface, changes an output schema, or deliberately changes a default in a way
consumers will notice.

To upgrade, change the `ref` and read the CHANGELOG entry between your old tag
and the new one. Nothing else moves under you.

## The estate profile and the approved ref

`.ci/estate.yml` records facts about the environment this library is used in:
the GitLab version, the runners and what they can do, the registries, the
canonical environment names, which scanners block and at what severity.
Components never hard-code any of that; a composition or a consumer passes it
in. It is where you look to answer "what should I put for `runner-tags`" rather
than guessing.

`shared_ci.approved_ref` in that file is the tag consumers are expected to pin.
It is the answer to "which version should I use". A reviewed commit SHA is
equally valid if you need something that is not yet tagged.

One thing to understand before relying on the file: a value of `unresolved`
means nobody has verified that fact, and it is not a blank to be filled in by
guessing. It is deliberately recorded as unknown.

## The project record

Each consuming repository keeps a `.ci/project.yml`: which composition it
adopted, at which ref, under which instance names, and with which inputs.

Nothing reads it at pipeline time. It exists so that the next person to open the
repository can see what was adopted and what was decided, without reverse
engineering it from the `.gitlab-ci.yml` and a year of commit messages. It is
also what makes an estate-wide question answerable: which repositories are still
on the old tag, which ones adopted the scanner, which ones opted out and why.

Its schema is `tests/contracts/project.schema.json` and the field guide is
[`docs/reference/project-yml.md`](../reference/project-yml.md).

## Where to look next

| You want | Read |
| --- | --- |
| What exists, its status and its evidence | [`.ci/catalog.yml`](../../.ci/catalog.yml), generated from the contracts |
| What a component needs, emits and fails on | `templates/<name>/contract.yml` |
| A worked `.gitlab-ci.yml` per composition | [`examples/`](../../examples/) |
| The rules all of this follows | [`docs/gitlab-ci-agent-standard.md`](../gitlab-ci-agent-standard.md) |
| Moving off the pre-1.0 include paths | [`docs/migrations/0.x-to-1.0.md`](../migrations/0.x-to-1.0.md) |
| Mirroring docs to a wiki, end to end | [docs-wiki-sync.md](docs-wiki-sync.md) |

Read the `status` field before depending on anything. `released` means a real
consumer pipeline ran every job that composition creates and they went green.
`experimental` means it has not, and the contract says which part has never been
exercised.
