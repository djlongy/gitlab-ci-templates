# Mirror a repository's docs to its GitLab wiki

This guide is for someone adopting the `docs-wiki` pipeline in their own
project for the first time. It assumes nothing about this repository.

Throughout, the shared CI project is written `platform/gitlab-ci-templates` and
the GitLab server `gitlab.example.com`. Substitute the real ones.

## What it does

A project's `docs/` folder is the source of truth. The wiki is a mirror of it
that people can also edit, and their edits come back.

One run of the job is one cycle, in this order and no other:

1. **Wiki to repository.** Every wiki commit made since the last sync is turned
   back into its `docs/` form and merged into the repository, one repository
   commit per wiki commit, carrying the wiki editor's name, email and date. The
   person who edited the wiki is the author of the commit, not CI.
2. **Push** those commits to the default branch, marked so they do not start
   another pipeline.
3. **Repository to wiki.** The whole wiki is regenerated from `docs/` and
   committed as the CI author.
4. **Push** the wiki.

Step 3 never runs before step 2 has succeeded, so a wiki edit always reaches the
repository before the repository is allowed to overwrite the wiki. If somebody
edits the wiki while the cycle is running, the final push is rejected and the
whole cycle repeats, so that edit is picked up too. Nothing is silently
discarded.

**What starts it.** Three things and nothing else: a push to the default
branch, the wiki webhook firing after somebody edits a page, and an hourly
schedule that catches an edit whose webhook delivery was missed. It never runs
on a merge request, because a merge request has nothing to publish and syncing
from an unmerged branch would overwrite the wiki with content that is not on the
default branch yet.

**Conflict semantics.** A page edited on both sides is merged three ways, using
the version CI last wrote as the common base. Where the two edits touch
different lines, both survive. Where they collide, the newer edit wins, decided
by the wiki commit's author date against the repository's last author date for
that file. A page deleted in the wiki is deleted from `docs/`, unless the
repository edited it after the last sync, in which case the repository copy
stays and the next cycle puts the wiki page back.

**Names change on the way across.** `docs/index.md` becomes the wiki front page
`home.md`, and `docs/section/index.md` becomes `section.md` so the wiki titles
it "section" rather than "index". Links are rewritten to the form GitLab renders
as wiki pages. A `_sidebar.md` is generated from each folder's `.pages` file.
This is all reversed on the way back.

**What is excluded** comes from MkDocs, so one file governs the site and the
wiki: `exclude_docs` skips a path entirely, `not_in_nav` keeps the page but
leaves it out of the sidebar.

## Adopt it

The whole `.gitlab-ci.yml` of a project whose only pipeline is this:

```yaml
include:
  - project: 'platform/gitlab-ci-templates'
    ref: '1.1.0'
    file: '/pipelines/docs-wiki.yml'
```

`ref` must be a release tag or a full commit SHA. A branch name such as `main`
is forbidden: it would change what your pipeline runs without you changing
anything. See [consuming-the-library.md](consuming-the-library.md).

`include: project:` resolves against your own GitLab, so this form needs the
library mirrored into it first. To try the component without mirroring anything,
remote-include its template from GitHub at a tag; that works for components but
not for the composition above. Both paths, and why, are in
[consuming-the-library.md](consuming-the-library.md#getting-the-library-onto-your-gitlab).

If your project already has its own jobs, include the component instead of the
composition, because the composition owns `workflow:` and `stages:` and two of
those cannot be merged:

```yaml
include:
  - project: 'platform/gitlab-ci-templates'
    ref: '1.1.0'
    file: '/templates/docs-wiki-sync/template.yml'
    inputs:
      instance: docs
      stage: verify
```

## Inputs

The composition's inputs. The component takes the same ones plus `stage`,
`max-retries` and `run-rules`, which the composition sets for you.

| Input | Default | Meaning |
| --- | --- | --- |
| `instance` | `docs` | Names the workload. The job is `<instance>:docs-wiki-sync`. Change it only if the name would collide with another job in your pipeline. |
| `working-directory` | `docs` | The documentation folder, relative to your checkout. This is the source of truth. |
| `mkdocs-config` | *(empty)* | The `mkdocs.yml` whose `exclude_docs` and `not_in_nav` decide what reaches the wiki. Empty means your repository root `mkdocs.yml` if you have one, and no filtering if you do not. |
| `webhook-reconcile` | `'on'` | Whether the job repairs its own webhook and trigger token each run. See [Surviving a move or a rename](#surviving-a-move-or-a-rename). **Quote the value.** In YAML, bare `off` is the boolean `false`, and GitLab rejects the pipeline with ``` `false` cannot be used because it is not in the list of allowed options ```. Write `webhook-reconcile: 'off'`. |
| `executor` | `docker` | Which kind of runner the job will land on. `shell` renders the job with no `image:` key, because a shell executor ignores one and runs the script on the host. See [Runners with no registry](#runners-with-no-registry). |
| `execution-image` | a digest-pinned Python image | Ignored when `executor` is `shell`. A digest is the preferred form and the default; `name:tag` is accepted where an internal mirror cannot serve a digest, and an empty value where no image is pulled at all. |
| `runner-tags` | `[]` | Runner selection, if your runners are registered with tags. |

The component adds `stage` (default `verify`), `max-retries` (`0`, `1` or `2`,
default `1`, and it retries only a runner failure, never a failed job) and
`run-rules`, which decides what starts the sync.

## The token

Set **`WIKI_TOKEN`** as a CI/CD variable, on the project or, better, on the
group so every repository inherits it.

Create it at **Settings → Access tokens** on the project (a project access
token) or on the group (a group access token, one token for every repository
under it):

| | |
| --- | --- |
| Role | Maintainer |
| Scope | `write_repository`, or `api` if you want the job to repair its own webhook |
| Expiry | GitLab requires one. A token that expires stops the sync silently, so diarise the renewal. |

Then **Settings → CI/CD → Variables**: name `WIKI_TOKEN`, **Masked** on,
**Protected** on only if your default branch is protected. A protected variable
is invisible on every other ref, which is usually what you want here, because
the job only runs on the default branch anyway.

The job needs the token to clone and push the wiki, and to push the pulled wiki
edits back to the default branch. Without it the job is **skipped**, not failed:
a run that never started is better evidence than one that stopped halfway
through rewriting your wiki.

**`WIKI_ADMIN_TOKEN`** is optional and is only read by the webhook reconcile
step. Set it when you would rather keep `WIKI_TOKEN` at `write_repository`: the
`api` scope can do anything its user can, and the sync itself needs far less
than that. Same creation steps, scope `api`, role Maintainer. If it is not set,
the reconcile step falls back to `WIKI_TOKEN`.

## First-time wiring

A wiki edit reaches CI through a project webhook on wiki page events whose URL
is that project's pipeline trigger endpoint. Something has to create it once.

**If `webhook-reconcile` is `on` and your token has the `api` scope, there is
nothing to do.** The first default-branch pipeline creates the trigger token and
the webhook itself, and says so in the job log.

Otherwise, run the bootstrap script once, from a checkout of the shared CI
project, with a token that has `api` scope and Maintainer on your project:

```bash
GITLAB_HOST=gitlab.example.com GITLAB_TOKEN=<api-scope token> \
  runtime/wiki/wiki-bootstrap.sh platform/my-repo
```

It creates the trigger token, the webhook and an hourly pipeline schedule, and
it skips anything already there, so it is safe to run again. The schedule is a
safety net that catches an edit whose webhook delivery was missed; the reconcile
step does not create it, so run the script once even with reconcile on if you
want that net.

## Verify it worked

Push a change to `docs/` on your default branch and open the job log. A first
run says something like:

```
executor: docker, Python 3.12.7
pyyaml: already importable, installing nothing
  trigger  created (id 8)
  webhook  created (id 58) -> https://gitlab.example.com/api/v4/projects/42/ref/main/trigger/pipeline?token=<token>
wiki:   https://gitlab.example.com/platform/my-repo.wiki.git (main, 3 commits)
docs:   /builds/platform/my-repo/docs on 9f21ac4
wiki has no edits since the last sync
synced 14 pages, 2 attachments, sidebar 16 lines
wiki updated from 9f21ac4
```

A second run says `trigger already present` and `webhook already correct` and
writes nothing. That is the reconcile step being idempotent, not a failure to
act.

Then edit a page in the wiki UI and watch your pipeline list: a new pipeline
with source **trigger** should appear within seconds. That single test exercises
the whole loop, because it is the webhook that starts it.

**There is no evidence artifact, deliberately.** The job produces no files. Its
record is the two git histories: `git log` on your default branch shows each
wiki editor's commit under their own name, and the wiki's own history shows the
CI regeneration commits. A job that claimed success while producing nothing you
can read back would be worse than no artifact.

The trigger token is never printed. It lives inside the webhook URL, so the job
log elides it there too.

## Surviving a move or a rename

The webhook URL contains the project id and the default branch name. Rename the
project, move it to another group, change the default branch, or migrate the
server to a different hostname, and that URL no longer reaches the right place.
Nothing fails: the schedule keeps the job green while wiki edits quietly stop
arriving.

With `webhook-reconcile: on`, every default-branch run rebuilds the URL the hook
should have from the job's own environment, compares it with the hook that is
there, and rewrites it on a difference. A migrated project repairs itself on its
next push, and the job log names the address it replaced.

Two details worth knowing:

- The component **owns its own trigger token**, identified by the description
  `wiki-sync` and by being created by the identity the job authenticates as.
  GitLab shows a trigger token's value in full only to the user who created it
  and shortens everybody else's to four characters, so a token an operator
  created by hand cannot be reused by the job. If it finds one, it makes its own
  and says that yours is now unused. It never deletes it: that credential is not
  the job's to remove.
- It recognises **its webhook by name**, not by URL, since the URL is the thing
  being repaired. Do not rename the `wiki-sync` webhook in the UI. If you do, the
  next run creates a second one and you will get two pipelines per wiki edit.

Turn it off with `webhook-reconcile: 'off'` if your organisation manages
webhooks centrally. The wiring is then yours to maintain. Quote the value:
unquoted `off` is a YAML boolean and GitLab refuses to create the pipeline.

## Runners with no registry

A shell-executor runner ignores `image:` entirely and runs the job script on the
host. If that is what you have, pass two inputs and nothing else:

```yaml
    inputs:
      executor: shell
      runner-tags: [your-shell-runner-tag]
```

The job then runs on the host's own `python3`, `git` and `tar`. Its one Python
dependency is pyyaml, resolved in three steps: the job imports it first, so a
host with the OS package installed fetches nothing; failing that it runs
`pip install --user`, using whatever `PIP_INDEX_URL` and `PIP_TRUSTED_HOST` you
have set as CI variables; failing that it stops with one line naming both
options. Nothing reaches pypi.org by default.

So on an air-gapped host, either install the OS package (`dnf install
python3-pyyaml`, `apt-get install python3-yaml`) or point `PIP_INDEX_URL` at an
index the host can reach.

The reconcile step needs no image either way: it is standard library Python and
calls only your own GitLab server.

## Rolling it out across many repositories

- **The token, once.** A group access token plus a group-level `WIKI_TOKEN`
  variable covers every repository in the group, including ones created later.
  This works on every GitLab tier. Do this first; it is most of the work.
- **The include, per repository.** The `.gitlab-ci.yml` above is three lines and
  the same in every repository. On GitLab Free and Premium there is no supported
  way to inject it from the group, so add the file to each repository. A short
  loop over the API is fine; the file is identical everywhere.
- **Enforcing it from the group** needs Ultimate, through a compliance pipeline
  or a pipeline execution policy. If your instance has it, that is the better
  route, because a repository cannot then drop the include. Check your tier
  before designing around it.
- **The webhook, nowhere.** With reconcile on, no per-repository bootstrap step
  is needed at all. This is the reason it exists: wiring fifty repositories by
  hand, and re-wiring them after a migration, is the part that does not scale.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Job is green but the wiki did not change | The pipeline ran on a branch, or the wiki already matches `docs/`. The sync only runs on the default branch. | Check the pipeline's ref and source. `wiki already current` in the log means the mirror was already correct. |
| Job does not appear in the pipeline at all | `WIKI_TOKEN` is not visible to this pipeline. Every rule requires it. | Check **Settings → CI/CD → Variables**. If the variable is Protected, the branch must be protected too. |
| `wiki has no edits since the last sync` | Normal. Nobody edited the wiki since CI last wrote it. | Nothing. This line is the pull step reporting it reached a decision, not skipping. |
| Editing a wiki page starts no pipeline | The webhook is missing, renamed, or points at an address that no longer resolves. | With reconcile on, push anything to the default branch and the next run repairs it. Otherwise run `wiki-bootstrap.sh`. |
| Every wiki edit starts **two** pipelines | Two webhooks point at the trigger endpoint, usually because one was renamed and the reconcile step created a second. | Delete the one not named `wiki-sync` in **Settings → Webhooks**. |
| `webhook not reconciled: GET /projects/…/hooks returned HTTP 403` | The token is not Maintainer, or lacks the `api` scope. | Grant `api` and Maintainer, or put an `api` token in `WIKI_ADMIN_TOKEN`, or set `webhook-reconcile: off`. The job keeps syncing meanwhile; only the self-repair is off. |
| `ERROR: pyyaml is missing and pip could not install it` | A shell executor on a host with no pyyaml and no reachable Python index. | Install the OS package on the runner host, or set `PIP_INDEX_URL` and, for a plain-HTTP mirror, `PIP_TRUSTED_HOST`. |
| `image name can't be blank` when creating the pipeline | `executor` is `docker` but `execution-image` was set to an empty value. | Either pass an image reference, or set `executor: shell`, which renders no `image:` key at all. |
| The job fails pushing to the default branch | The branch is protected and the token's user is not allowed to push to it. | Allow the token's user to push, or make it Maintainer. |
| It worked, then stopped months later | The access token expired. | Rotate it. GitLab emails before expiry; the sync gives no other warning. |
| A wiki edit was overwritten by the repository version | Both sides changed the same lines and the repository edit was newer. | The wiki edit is not lost: it is in the wiki's own history. Re-apply it, or edit `docs/`, which is the source of truth. |

## Reference

- Component contract: [`templates/docs-wiki-sync/contract.yml`](../../templates/docs-wiki-sync/contract.yml)
- Composition contract: [`pipelines/docs-wiki.contract.yml`](../../pipelines/docs-wiki.contract.yml)
- Worked example: [`examples/docs-wiki/`](../../examples/docs-wiki/)
- Why components and compositions exist: [consuming-the-library.md](consuming-the-library.md)
