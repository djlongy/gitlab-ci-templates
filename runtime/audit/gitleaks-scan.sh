#!/usr/bin/env bash
# gitleaks-scan.sh — scan every cloned repository and tell findings apart from
# execution failures.
#
# Section 10.1 requires the two to be distinguishable: a blocking finding fails
# the job, and so does a scanner that crashed, timed out or produced no parsable
# report. security/group-scan.yml conflated them — it read gitleaks' exit status
# as "leaks: yes/no", so a gitleaks that failed to start counted as a repository
# with leaks, and a repository that never cloned counted as nothing at all.
#
# Usage:
#   gitleaks-scan.sh <repositories-directory> <reports-directory>
#
# Environment:
#   CI_TPL_GITLEAKS_ARGS  extra arguments passed to `gitleaks detect`
#
# Exit codes:
#   0  every repository scanned, no findings
#   1  at least one repository has findings
#   2  a scan did not complete: missing tool, non-finding exit status, or a
#      report that is absent or unparsable
#   3  usage error, or no repository to scan

set -euo pipefail

repositories="${1:?usage: gitleaks-scan.sh <repositories-directory> <reports-directory>}"
reports="${2:?usage: gitleaks-scan.sh <repositories-directory> <reports-directory>}"

[ -d "$repositories" ] || { echo "gitleaks-scan: $repositories is not a directory" >&2; exit 3; }
for tool in gitleaks jq; do
  command -v "$tool" >/dev/null 2>&1 || { echo "gitleaks-scan: $tool is not on PATH" >&2; exit 2; }
done
mkdir -p "$reports"

findings=0
errors=0
scanned=0

for repository in "$repositories"/*; do
  [ -d "$repository" ] || continue
  name=$(basename "$repository")
  case "$name" in .*) continue ;; esac
  report="$reports/$name.json"
  scanned=$((scanned + 1))

  echo "--- gitleaks: $name"
  status=0
  gitleaks detect \
    --source "$repository" \
    --report-format json \
    --report-path "$report" \
    --redact \
    --no-banner \
    ${CI_TPL_GITLEAKS_ARGS:-} || status=$?

  # A report that cannot be parsed means the scan did not complete, whatever
  # the exit status said.
  if [ ! -s "$report" ] || ! jq -e 'type == "array"' "$report" >/dev/null 2>&1; then
    echo "gitleaks-scan: $name produced no parsable report (exit $status)" >&2
    errors=$((errors + 1))
    continue
  fi

  count=$(jq 'length' "$report")
  case "$status" in
    0)
      if [ "$count" -ne 0 ]; then
        # Documented exit code 1 means findings; 0 with findings in the report
        # is a contradiction, so the result is not trusted.
        echo "gitleaks-scan: $name reported $count finding(s) with exit 0" >&2
        errors=$((errors + 1))
      else
        echo "    clean"
      fi
      ;;
    1)
      echo "    $count finding(s)"
      findings=$((findings + count))
      ;;
    *)
      echo "gitleaks-scan: $name failed to scan (exit $status)" >&2
      errors=$((errors + 1))
      ;;
  esac
done

echo "gitleaks-scan: $scanned repositories, $findings finding(s), $errors error(s)"
[ "$scanned" -gt 0 ] || { echo "gitleaks-scan: nothing was scanned" >&2; exit 3; }
[ "$errors" -eq 0 ] || exit 2
[ "$findings" -eq 0 ] || exit 1
exit 0
