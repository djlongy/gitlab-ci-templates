#!/usr/bin/env bash
# Group-wide secret scan script
# Standalone version of the CI job for local/manual execution
#
# Usage:
#   export GITLAB_URL=https://gitlab.example.com
#   export GITLAB_TOKEN=<PAT with read_api + read_repository>
#   runtime/audit/group-scan.sh [GROUP_ID]

set -euo pipefail

GITLAB_URL="${GITLAB_URL:?GITLAB_URL must be set}"
GITLAB_TOKEN="${GITLAB_TOKEN:?GITLAB_TOKEN must be set}"
GROUP_ID="${1:-10}"

command -v gitleaks >/dev/null 2>&1 || { echo "ERROR: gitleaks not found in PATH"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq not found in PATH"; exit 1; }

FAILED=0
SCANNED=0
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "=== Group-Wide Secret Scan ==="
echo "GitLab: ${GITLAB_URL}"
echo "Group ID: ${GROUP_ID}"
echo ""

PROJECTS=$(curl -s -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "${GITLAB_URL}/api/v4/groups/${GROUP_ID}/projects?per_page=100&include_subgroups=true" | \
  jq -r '.[] | "\(.id)|\(.path_with_namespace)|\(.http_url_to_repo)"')

for PROJECT in $PROJECTS; do
  PROJECT_ID=$(echo "$PROJECT" | cut -d'|' -f1)
  PROJECT_PATH=$(echo "$PROJECT" | cut -d'|' -f2)
  PROJECT_URL=$(echo "$PROJECT" | cut -d'|' -f3)

  echo "--- Scanning: $PROJECT_PATH ---"

  CLONE_URL=$(echo "$PROJECT_URL" | sed "s|https://|https://scanner:${GITLAB_TOKEN}@|")

  if git clone --depth 1 "$CLONE_URL" "${TMPDIR}/scan-${PROJECT_ID}" 2>/dev/null; then
    SCANNED=$((SCANNED + 1))

    if ! gitleaks detect --source "${TMPDIR}/scan-${PROJECT_ID}" --verbose --redact 2>&1; then
      FAILED=$((FAILED + 1))
      echo "  RESULT: FAILED"
    else
      echo "  RESULT: PASSED"
    fi

    rm -rf "${TMPDIR}/scan-${PROJECT_ID}"
  else
    echo "  RESULT: SKIPPED (clone failed)"
  fi
done

echo ""
echo "============================================"
echo "Repos scanned: $SCANNED"
echo "Repos with leaks: $FAILED"
echo "============================================"

exit "$FAILED"
