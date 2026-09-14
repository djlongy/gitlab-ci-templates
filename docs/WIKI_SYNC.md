# Wiki sync, step by step

`docs/` in a repo is the source of truth. The project wiki is a generated view of
it, and a wiki edit comes back as a repo commit by the person who made it.

Three parts, in order. Part A is done once for the whole GitLab instance. Part B
is done once per repo. Part C is optional.

Every step says what you should see when it worked. If you do not see it, stop
there and go to [Troubleshooting](#troubleshooting) rather than carrying on.

Placeholders used below: `gitlab.example.com` is your GitLab host,
`platform/ci-templates` is where this repo lives, `group/project` is the repo you
are wiring up.

---

## Part A — once for the whole instance

### A1. Put this repo on your GitLab

Push this repository to `platform/ci-templates` (any path works; use yours
consistently from here on).

**Expected:** `https://gitlab.example.com/platform/ci-templates` opens and shows
`docs/wiki-sync.yml`.

### A2. Create one group access token

In the group that holds your repos: **Settings > Access tokens > Add new token**.

- Name: `wiki-sync`
- Role: **Maintainer**
- Scope: **`write_repository`** only

**Expected:** GitLab shows the token value once. Copy it into your secret store
now. You cannot read it again.

Maintainer is needed because the sync pushes to the default branch, which is
usually protected. `write_repository` is the only scope the sync uses.

### A3. Make the token a group CI variable

In the same group: **Settings > CI/CD > Variables > Add variable**.

- Key: `WIKI_TOKEN`
- Value: the token from A2
- **Masked**: on
- **Protected**: on

**Expected:** the variable list shows `WIKI_TOKEN`, masked, protected.

Protected means it only reaches pipelines on protected branches. That is what you
want, and it is why step B4 checks the default branch is protected.

---

## Part B — once per repo

### B1. Check the repo has a docs folder

The repo needs `docs/index.md` at minimum. If the markdown lives somewhere else,
note the folder name for B3.

**Expected:** `docs/index.md` exists on the default branch.

Already have a wiki and no `docs/`? Do this first, once:

```bash
git clone https://gitlab.example.com/group/project.wiki.git /tmp/wiki
python3 scripts/wiki/wiki-import.py /tmp/wiki docs
```

**Expected:** `docs/` now holds one `.md` per wiki page. Read it, fix anything
odd, commit it. From here the repo is the source of truth.

### B2. Check the default branch is protected

**Settings > Repository > Protected branches**.

**Expected:** the default branch is listed. If it is not, protect it now, or the
protected `WIKI_TOKEN` from A3 will never reach the pipeline.

### B3. Add the include to `.gitlab-ci.yml`

This is the whole CI change. Nothing else.

```yaml
include:
  - project: 'platform/ci-templates'
    file: '/docs/wiki-sync.yml'
    ref: main
```

If the markdown is not in `docs/`, add the folder name too:

```yaml
variables:
  DOCS_DIR: "documentation"
```

**Expected:** **CI/CD > Editor** in the project shows the config as valid and
lists a `wiki` job.

The template's job runs in the `test` stage. If your repo declares its own
`stages:`, keep a `test` in the list or set `wiki: stage:` to one of yours.

### B4. Keep your other jobs out of sync pipelines

A wiki edit starts a pipeline whose only purpose is the sync. Your build and lint
jobs should not run in it. The template gives you a rule fragment for that, so add
this first rule to every other job:

```yaml
rules:
  - !reference [.not_for_wiki_sync, rules]
  - ...your existing rules...
```

**Expected:** nothing visible yet. You will see the effect in B7.

### B5. Commit and merge the include

Merge B3 and B4 to the default branch.

**Expected:** a pipeline runs on the default branch and its `wiki` job passes. The
project wiki now holds one page per file in `docs/`.

### B6. Run the bootstrap once

From a checkout of this templates repo, with a personal access token that has
`api` scope and Maintainer on both projects:

```bash
GITLAB_HOST=gitlab.example.com \
GITLAB_TOKEN=<api-scope token> \
WIKI_SYNC_PROJECT=platform/ci-templates \
bash scripts/wiki/wiki-bootstrap.sh group/project
```

**Expected:** four lines, each either `created` or `already present`:

```
  trigger      created (id N)
  webhook      created (id N, wiki page events only)
  schedule     created (id N, hourly on main)
  allowlist    added to platform/ci-templates
```

The script is safe to re-run at any time; a second run prints `already present`
four times and changes nothing. The trigger token lives only inside the webhook
URL and is never printed.

### B7. Prove it with one wiki edit

Open any wiki page in the UI, add a line, save.

**Expected, within about a minute:**

1. A pipeline appears in the project with source **trigger**.
2. That pipeline contains the `wiki` job and nothing else.
3. The default branch gets one new commit, authored by **you**, touching only the
   one `docs/` file behind that page.

That is both directions proven. The reverse direction (`docs/` edit to wiki page)
runs on every push to the default branch.

---

## Part C — running the sync from your laptop

Useful for a dry run before you trust CI with it.

### C1. Fill in the environment file

Copy it to somewhere **outside** any checkout, so a token can never be committed:

```bash
cp scripts/wiki/.env.example ~/.wiki-sync.env
chmod 600 ~/.wiki-sync.env
```

Edit `~/.wiki-sync.env`: set `GITLAB_HOST`, `GITLAB_PROJECT_PATH`, and
`WIKI_TOKEN` to the token from A2 (or any token with `write_repository` on the
project).

**Expected:** the file holds four filled-in values and is readable only by you.

### C2. Dry run

```bash
cd /path/to/group-project-checkout
source ~/.wiki-sync.env
DRY_RUN=1 bash /path/to/ci-templates/scripts/wiki/wiki-deploy.sh docs /tmp/wiki-clone
```

**Expected:** it prints the wiki URL, the commit count, and what *would* change.
Nothing is pushed.

### C3. Real run

Same command without `DRY_RUN=1`.

**Expected:** `wiki updated from <short sha>`, and the wiki shows your changes.

---

## Troubleshooting

### I edited the wiki and no pipeline started

The webhook did not fire or could not reach the trigger endpoint.

1. Go to **Settings > Webhooks** in the project, open the `wiki-sync` hook, and
   click **Recent events**.
2. Look at the newest delivery. A `201` means GitLab accepted the trigger and the
   problem is elsewhere. A `404` or `403` means the trigger token is gone: re-run
   B6, which mints a new one.
3. No delivery listed at all means the hook is not on wiki page events. Delete it
   and re-run B6.

### A pipeline ran but no commit appeared on the branch

Open the `wiki` job log. The message is near the end.

| In the log | What to do |
|---|---|
| `WIKI_TOKEN is required to clone` | The variable is not reaching the job. Check A3 is masked **and** protected, and that B2 protected the branch. |
| `push to <branch> rejected twice` | The token's role is below Maintainer, or the branch allows nobody to push. Fix the role in A2. |
| `does not exist!` naming the template file | The `include:` path or `ref:` in B3 is wrong, or the allowlist entry from B6 is missing. |
| `wiki still changing after 3 attempts` | Someone kept editing the wiki while it ran. Harmless: re-run the job. |

### The sidebar order is wrong

`_sidebar.md` is generated from each folder's `.pages` file (awesome-pages `nav:`
format). Folders without one are listed alphabetically. Add or edit `.pages` in
`docs/`, push, and the next sync rewrites the sidebar.

### A page should not be published at all

`wiki-sync.py` reads `exclude_docs` and `not_in_nav` from `mkdocs.yml` when the
repo has one. `exclude_docs` drops the page; `not_in_nav` publishes it but keeps
it out of the sidebar.

### Everything works but the commits say "docs ci"

That is correct. Commits made *by the sync* use that identity. Commits made from a
*wiki edit* carry the editor's name, which is the point of B7 step 3. Override the
sync identity with `CI_AUTHOR_NAME` and `CI_AUTHOR_EMAIL` if you want.
