#!/usr/bin/env bash
# One-time wiring so a project's wiki edits reach its pipeline.
#
#   usage: GITLAB_HOST=gitlab.example GITLAB_TOKEN=<api-scope PAT> \
#          runtime/wiki/wiki-bootstrap.sh group/project
#
# Creates, only where it is missing:
#   1. a pipeline trigger token, description "wiki-sync"
#   2. a project webhook on wiki page events only, pointing at that trigger endpoint
#   3. an hourly pipeline schedule on the default branch, "wiki sync safety net"
#   4. the project on the templates project's job-token allowlist, so the wiki job may
#      clone the scripts with its CI_JOB_TOKEN (skipped when the templates project is public)
#
# Run it again any time: every step looks the thing up first and reports "already present".
# The trigger token exists only inside the webhook URL and is never printed; GitLab redacts
# it when reading the hook back, so a missing webhook means a fresh trigger token is minted
# and the old "wiki-sync" one is dropped.
#
# WIKI_SYNC_PROJECT  path of the templates project (default: platform/gitlab-ci-templates)
# GITLAB_TOKEN needs api scope, Maintainer on the project and on the templates project.
set -euo pipefail

project=${1:?usage: wiki-bootstrap.sh group/project}
host=${GITLAB_HOST:?set GITLAB_HOST, e.g. gitlab.example}
: "${GITLAB_TOKEN:?set GITLAB_TOKEN to an api-scope token}"
templates=${WIKI_SYNC_PROJECT:-platform/gitlab-ci-templates}

api="https://$host/api/v4"
umask 077
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# curl with the token in a header file, so it never reaches a command line or the job log
printf 'header = "PRIVATE-TOKEN: %s"\n' "$GITLAB_TOKEN" > "$work/curlrc"
gl() { curl -fsS --config "$work/curlrc" "$@"; }
enc() { python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$1"; }
jqf() { python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],""))' "$1" "$2"; }

gl "$api/projects/$(enc "$project")" > "$work/p.json"
pid=$(jqf "$work/p.json" id)
branch=$(jqf "$work/p.json" default_branch)
echo "project $project (id $pid, default branch $branch)"

trigger_url_prefix="$api/projects/$pid/ref/$branch/trigger/pipeline"

# ---- 1 + 2. trigger token and the webhook that carries it ---------------------------------
gl "$api/projects/$pid/hooks" > "$work/hooks.json"
hook_id=$(python3 - "$work/hooks.json" "$trigger_url_prefix" <<'PY'
import json, sys
pre = sys.argv[2]
for h in json.load(open(sys.argv[1])):
    if h.get("url", "").startswith(pre) and h.get("wiki_page_events"):
        print(h["id"]); break
PY
)
if [ -n "$hook_id" ]; then
  echo "  trigger      already present (inside webhook id $hook_id)"
  echo "  webhook      already present (id $hook_id)"
else
  gl "$api/projects/$pid/triggers" > "$work/trg.json"
  old=$(python3 -c 'import json,sys
print(next((t["id"] for t in json.load(open(sys.argv[1])) if t.get("description")=="wiki-sync"), ""))' "$work/trg.json")
  if [ -n "$old" ]; then
    gl -o /dev/null -X DELETE "$api/projects/$pid/triggers/$old"
    echo "  trigger      replaced the unused wiki-sync token (id $old)"
  fi
  gl -X POST --data-urlencode "description=wiki-sync" \
     "$api/projects/$pid/triggers" > "$work/new.json"
  echo "  trigger      created (id $(jqf "$work/new.json" id))"
  python3 - "$work/new.json" "$trigger_url_prefix" "$work/hook.json" <<'PY'
import json, sys, urllib.parse
tok = json.load(open(sys.argv[1]))["token"]
json.dump({"url": "%s?token=%s" % (sys.argv[2], urllib.parse.quote(tok, safe="")),
           "wiki_page_events": True, "push_events": False, "enable_ssl_verification": True,
           "name": "wiki-sync", "description": "wiki edits start a sync pipeline"},
          open(sys.argv[3], "w"))
PY
  gl -X POST -H 'Content-Type: application/json' --data @"$work/hook.json" \
     "$api/projects/$pid/hooks" > "$work/hookout.json"
  echo "  webhook      created (id $(jqf "$work/hookout.json" id), wiki page events only)"
fi

# ---- 3. hourly schedule, the safety net if a webhook delivery is ever missed ----------------
gl "$api/projects/$pid/pipeline_schedules" > "$work/sched.json"
sid=$(python3 -c 'import json,sys
print(next((s["id"] for s in json.load(open(sys.argv[1])) if s.get("description")=="wiki sync safety net"), ""))' "$work/sched.json")
if [ -n "$sid" ]; then
  echo "  schedule     already present (id $sid)"
else
  gl -X POST --data-urlencode "description=wiki sync safety net" \
     --data-urlencode "ref=$branch" --data-urlencode "cron=0 * * * *" \
     "$api/projects/$pid/pipeline_schedules" > "$work/sout.json"
  echo "  schedule     created (id $(jqf "$work/sout.json" id), hourly on $branch)"
fi

# ---- 4. job-token allowlist on the templates project ---------------------------------------
gl "$api/projects/$(enc "$templates")" > "$work/t.json"
tid=$(jqf "$work/t.json" id)
if [ "$(jqf "$work/t.json" visibility)" = public ]; then
  echo "  allowlist    not needed ($templates is public)"
elif [ "$tid" = "$pid" ]; then
  echo "  allowlist    not needed (this is the templates project)"
else
  gl "$api/projects/$tid/job_token_scope/allowlist" > "$work/al.json"
  if python3 -c 'import json,sys
sys.exit(0 if any(p["id"]==int(sys.argv[2]) for p in json.load(open(sys.argv[1]))) else 1)' "$work/al.json" "$pid"; then
    echo "  allowlist    already present on $templates"
  else
    gl -o /dev/null -X POST --data-urlencode "target_project_id=$pid" \
       "$api/projects/$tid/job_token_scope/allowlist"
    echo "  allowlist    added to $templates"
  fi
fi

cat <<EOF

still yours to do, once:
  - $branch is a protected branch (the group WIKI_TOKEN variable is protected)
  - the group CI variable WIKI_TOKEN is visible to this project
  - $project has a docs folder with at least an index.md, and .gitlab-ci.yml includes
    /pipelines/docs-wiki.yml from $templates, or /templates/docs-wiki-sync/template.yml
    when the project runs checks of its own and owns its stages: and workflow:
prove it: edit a wiki page in the UI, then watch the pipeline at
  https://$host/$project/-/pipelines
EOF
