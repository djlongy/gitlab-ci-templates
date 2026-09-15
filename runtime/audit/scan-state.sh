#!/usr/bin/env bash
# scan-state.sh — Scan Terraform state files for leaked secrets.
# Parses .tfstate JSON and checks for high-entropy strings and known token patterns.
# Exit code: 0 = clean, 1 = potential secrets found.
#
# Usage:
#   export TERRAFORM_BASE_PATH="/path/to/repos"
#   runtime/audit/scan-state.sh
#
# Or pass repos as arguments:
#   runtime/audit/scan-state.sh terraform-cloudflare terraform-hypervisor-vms

set -euo pipefail

TERRAFORM_BASE_PATH="${TERRAFORM_BASE_PATH:?TERRAFORM_BASE_PATH must be set}"

if [[ $# -gt 0 ]]; then
  REPOS=("$@")
else
  REPOS=("terraform-cloudflare" "terraform-hypervisor-vms" "terraform-modules")
fi

FAILURES=0

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

if ! command -v jq &>/dev/null; then
  echo "ERROR: jq is required but not installed"
  exit 1
fi

# Collect state files
STATE_FILES=()
for repo in "${REPOS[@]}"; do
  repo_path="${TERRAFORM_BASE_PATH}/${repo}"
  [[ -d "$repo_path" ]] || continue
  while IFS= read -r -d '' f; do
    STATE_FILES+=("$f")
  done < <(find "$repo_path" -name '*.tfstate' -type f -not -path '*/.terraform/*' -print0 2>/dev/null)
done

if [[ ${#STATE_FILES[@]} -eq 0 ]]; then
  echo "No .tfstate files found under ${TERRAFORM_BASE_PATH}"
  echo -e "${GREEN}SCAN PASSED${NC} (no state files to scan)"
  exit 0
fi

echo "Scanning ${#STATE_FILES[@]} state files"
echo ""

# Sensitive attribute names to check for plaintext values
SENSITIVE_ATTRS=(
  "password"
  "secret"
  "token"
  "private_key"
  "api_key"
  "api_token"
  "credentials"
  "access_key"
  "secret_key"
  "connection_string"
)

for state_file in "${STATE_FILES[@]}"; do
  echo "Scanning: ${state_file}"

  # Check 1: Look for sensitive attributes with non-null, non-empty values
  for attr in "${SENSITIVE_ATTRS[@]}"; do
    HITS=$(jq -r "
      .. | objects |
      to_entries[] |
      select(.key | test(\"${attr}\"; \"i\")) |
      select(.value != null and .value != \"\" and (.value | type) == \"string\" and (.value | length) > 3) |
      \"  attribute: \\(.key) = \\(.value | .[0:8])...\"
    " "$state_file" 2>/dev/null || true)

    if [[ -n "$HITS" ]]; then
      echo -e "  ${RED}FAIL${NC}: Found '${attr}' values in state:"
      echo "$HITS" | head -20
      FAILURES=$((FAILURES + 1))
    fi
  done

  # Check 2: Look for known secret token patterns
  HIGH_ENTROPY=$(jq -r '
    [.. | strings | select(length > 20 and length < 500)] |
    unique[] |
    select(test("^[A-Za-z0-9+/=_-]{20,}$")) |
    select(test("(gldt-|glpat-|hvs\\.|sk-|AKIA|-----BEGIN)"))
  ' "$state_file" 2>/dev/null || true)

  if [[ -n "$HIGH_ENTROPY" ]]; then
    COUNT=$(echo "$HIGH_ENTROPY" | wc -l | tr -d ' ')
    echo -e "  ${RED}FAIL${NC}: Found ${COUNT} potential secret pattern(s) in state"
    echo "$HIGH_ENTROPY" | head -5 | while read -r val; do
      echo "    ${val:0:12}..."
    done
    FAILURES=$((FAILURES + 1))
  fi

  # Check 3: Verify outputs are not leaking secrets
  OUTPUT_SECRETS=$(jq -r '
    .outputs // {} | to_entries[] |
    select(.key | test("password|secret|token|key|credential"; "i")) |
    select(.value.sensitive != true) |
    "  output \"\(.key)\" is not marked sensitive"
  ' "$state_file" 2>/dev/null || true)

  if [[ -n "$OUTPUT_SECRETS" ]]; then
    echo -e "  ${RED}FAIL${NC}: Outputs with secret names not marked sensitive:"
    echo "$OUTPUT_SECRETS"
    FAILURES=$((FAILURES + 1))
  fi
done

# Summary
echo ""
echo "════════════════════════════════════════"
if [[ $FAILURES -gt 0 ]]; then
  echo -e "${RED}STATE SCAN FAILED${NC}: ${FAILURES} issue(s) found"
  echo "Action required: Rotate any exposed credentials and re-apply with ephemeral blocks"
  exit 1
else
  echo -e "${GREEN}STATE SCAN PASSED${NC}: No secrets detected in state files"
  exit 0
fi
