#!/usr/bin/env bash
# audit-secrets.sh — Static analysis of Terraform files for security anti-patterns.
# Scans all repos under TERRAFORM_BASE_PATH for known issues.
# Exit code: 0 = all checks pass, 1 = one or more checks failed.
#
# Usage:
#   export TERRAFORM_BASE_PATH="/path/to/repos"
#   runtime/audit/audit-secrets.sh
#
# Or pass repos as arguments:
#   runtime/audit/audit-secrets.sh terraform-cloudflare terraform-hypervisor-vms terraform-modules

set -euo pipefail

TERRAFORM_BASE_PATH="${TERRAFORM_BASE_PATH:?TERRAFORM_BASE_PATH must be set}"

if [[ $# -gt 0 ]]; then
  REPOS=("$@")
else
  REPOS=("terraform-cloudflare" "terraform-hypervisor-vms" "terraform-modules")
fi

FAILURES=0
CHECKS_RUN=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

pass() {
  echo -e "  ${GREEN}PASS${NC}: $1"
}

fail() {
  FAILURES=$((FAILURES + 1))
  echo -e "  ${RED}FAIL${NC}: $1"
}

warn() {
  echo -e "  ${YELLOW}WARN${NC}: $1"
}

# Run one search step and let an error stop the audit.
#
# grep exits 0 when it matched, 1 when it did not, and 2 or more when it could
# not look: an unreadable file, a directory where a file was expected, a pattern
# it refused. awk has no "did not match" code at all, so for awk any non-zero
# status is an error. Every search below was written `... 2>/dev/null || true`,
# which collapsed all of those into "no hits" and printed PASS. A repository
# this script could not read was reported as a repository with no findings,
# which is the one answer an audit must never give.
#
# Errors are fatal rather than counted as a FAIL: a failed check says the code
# is wrong, and this says the audit did not run. Those are different answers and
# the caller has to be able to tell them apart.
#
# Pipelines stay outside this wrapper: filters downstream of a search read stdin
# and cannot hit the error cases above, and passing a pipeline through here
# would mean eval.
search() {
  local status=0 threshold=1 out
  if [[ "$1" == "grep" ]]; then threshold=2; fi
  out=$("$@") || status=$?
  if (( status >= threshold )); then
    echo "ERROR: $1 exited ${status}: this search did not complete, so nothing here is a clean result" >&2
    echo "ERROR:   command: $*" >&2
    exit 2
  fi
  printf '%s' "$out"
}

# The file list is part of the verdict: a repository whose tree could not be
# walked contributes no files and no findings, which reads as clean.
collect_tf_files() {
  local root=$1 status=0 listing
  listing=$(mktemp)
  find "$root" -name '*.tf' -type f -not -path '*/.terraform/*' -print0 > "$listing" || status=$?
  if (( status != 0 )); then
    rm -f "$listing"
    echo "ERROR: find exited ${status} walking ${root}; an incomplete file list is not a clean audit" >&2
    exit 2
  fi
  while IFS= read -r -d '' found; do
    TF_FILES+=("$found")
  done < "$listing"
  rm -f "$listing"
}

# Collect all .tf files across repos
TF_FILES=()
for repo in "${REPOS[@]}"; do
  repo_path="${TERRAFORM_BASE_PATH}/${repo}"
  if [[ ! -d "$repo_path" ]]; then
    warn "Repository not found: ${repo_path}"
    continue
  fi
  collect_tf_files "$repo_path"
done

if [[ ${#TF_FILES[@]} -eq 0 ]]; then
  echo "ERROR: No .tf files found under ${TERRAFORM_BASE_PATH}"
  exit 1
fi

echo "Scanning ${#TF_FILES[@]} Terraform files across ${#REPOS[@]} repos"
echo "Base path: ${TERRAFORM_BASE_PATH}"
echo ""

# ─── Check 1: Non-ephemeral vault_kv_secret_v2 data sources ───────────────────
# Related findings: F-03, C-01, C-02, C-03
# Severity: CRITICAL
# Vault reads that contain actual secrets must use ephemeral blocks to prevent
# state persistence. However, non-secret identifiers (account IDs, zone IDs,
# project IDs, etc.) that must persist to state-managed resource attributes are
# allowed to use data sources. Mark these with a trailing comment:
#   data "vault_kv_secret_v2" "ids" { ... }  # audit:non-secret
echo "CHECK 1: Non-ephemeral Vault reads [F-03, C-01, C-02, C-03]"
CHECKS_RUN=$((CHECKS_RUN + 1))
CHECK1_HITS=""
for f in "${TF_FILES[@]}"; do
  # Flag files containing data "vault_kv_secret_v2" that lack the audit:non-secret annotation
  RESULT=$(search awk '
    /data "vault_kv_secret_v2"/ {
      if ($0 !~ /audit:non-secret/) {
        print FILENAME ":" NR ": " $0
      }
    }
  ' "$f")
  [[ -n "$RESULT" ]] && CHECK1_HITS="${CHECK1_HITS}${RESULT}"$'\n'
done
CHECK1_HITS=$(echo "$CHECK1_HITS" | sed '/^$/d')
if [[ -n "$CHECK1_HITS" ]]; then
  fail "Found non-ephemeral vault_kv_secret_v2 data sources (must use ephemeral blocks or add '# audit:non-secret' comment):"
  echo "$CHECK1_HITS" | while read -r line; do echo "    $line"; done
else
  pass "No unexempted non-ephemeral vault_kv_secret_v2 data sources found"
fi

# ─── Check 2: vault_secrets module usage ──────────────────────────────────────
# Related findings: F-03, C-01, M-02
# Severity: CRITICAL
# Module outputs always persist to state. Use direct ephemeral blocks instead.
echo "CHECK 2: Vault secrets module usage [F-03, C-01, M-02]"
CHECKS_RUN=$((CHECKS_RUN + 1))
HITS=$(search grep -rln 'module "vault_secrets"' "${TF_FILES[@]}")
if [[ -n "$HITS" ]]; then
  fail "Found vault_secrets module references (must use direct ephemeral blocks):"
  echo "$HITS" | while read -r f; do echo "    $f"; done
else
  pass "No vault_secrets module references found in consumer repos"
fi

# ─── Check 3: Hardcoded secrets/tokens ────────────────────────────────────────
# Related findings: CRIT-01, F-04, F-07
# Severity: CRITICAL
# Scans both .tf and .md files for common token patterns.
echo "CHECK 3: Hardcoded secrets and tokens [CRIT-01, F-04, F-07]"
CHECKS_RUN=$((CHECKS_RUN + 1))
SECRET_PATTERNS='(gldt-[A-Za-z0-9_-]{20,}|glpat-[A-Za-z0-9_-]{20,}|hvs\.[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|bearer [A-Za-z0-9_-]{20,})'
HITS=""
for repo in "${REPOS[@]}"; do
  repo_path="${TERRAFORM_BASE_PATH}/${repo}"
  [[ -d "$repo_path" ]] || continue
  REPO_RAW=$(search grep -rlE "$SECRET_PATTERNS" "$repo_path" \
    --include='*.tf' --include='*.md' --include='*.yml' --include='*.yaml' \
    --include='*.tfvars' --include='*.tfvars.example')
  REPO_HITS=""
  if [[ -n "$REPO_RAW" ]]; then
    REPO_HITS=$(printf '%s\n' "$REPO_RAW" | grep -v '/.terraform/' || true)
  fi
  [[ -n "$REPO_HITS" ]] && HITS="${HITS}${REPO_HITS}"$'\n'
done
HITS=$(echo "$HITS" | sed '/^$/d')
if [[ -n "$HITS" ]]; then
  fail "Found potential hardcoded secrets:"
  echo "$HITS" | while read -r f; do echo "    $f"; done
else
  pass "No hardcoded secrets detected"
fi

# ─── Check 4: Missing sensitive = true on password/secret/token/key vars ──────
# Related findings: F-01, M-04, LOW-01
# Severity: HIGH
# All variables containing credentials must be marked sensitive to prevent
# exposure in plan output and CI logs.
echo "CHECK 4: Variables missing sensitive = true [F-01, M-04, LOW-01]"
CHECKS_RUN=$((CHECKS_RUN + 1))
CHECK4_RESULT=""
for f in "${TF_FILES[@]}"; do
  RESULT=$(search awk '
    /^variable "[^"]*(_password|_secret|_token|_key|api_key|api_token)[^"]*"/ {
      varname = $0; has_sensitive = 0; brace_count = 0; in_block = 1; next
    }
    in_block && /{/ { brace_count++ }
    in_block && /}/ {
      brace_count--
      if (brace_count <= 0) {
        if (!has_sensitive) { gsub(/^[ \t]+/, "", varname); print FILENAME ": " varname }
        in_block = 0
      }
    }
    in_block && /sensitive/ && /=/ && /true/ { has_sensitive = 1 }
  ' "$f")
  [[ -n "$RESULT" ]] && CHECK4_RESULT="${CHECK4_RESULT}${RESULT}"$'\n'
done
CHECK4_RESULT=$(echo "$CHECK4_RESULT" | sed '/^$/d')
if [[ -n "$CHECK4_RESULT" ]]; then
  fail "Variables missing sensitive = true:"
  echo "$CHECK4_RESULT" | while read -r line; do echo "    $line"; done
else
  pass "All password/secret/token/key variables have sensitive = true"
fi

# ─── Check 5: Local state backends ───────────────────────────────────────────
# Related findings: F-02, F-12
# Severity: CRITICAL
# Local state backends write secrets to disk in plaintext.
echo "CHECK 5: Local state backends [F-02, F-12]"
CHECKS_RUN=$((CHECKS_RUN + 1))
HITS=$(search grep -rln 'backend "local"' "${TF_FILES[@]}")
if [[ -n "$HITS" ]]; then
  fail "Found local state backends (must use remote backend):"
  echo "$HITS" | while read -r f; do echo "    $f"; done
else
  pass "No local state backends found"
fi

# ─── Check 6: allow_unverified_ssl defaults ──────────────────────────────────
# Related findings: HIGH-02, H-03
# Severity: HIGH
# Insecure TLS defaults enable MITM credential interception.
echo "CHECK 6: allow_unverified_ssl defaults [HIGH-02, H-03]"
CHECKS_RUN=$((CHECKS_RUN + 1))
HITS=""
for f in "${TF_FILES[@]}"; do
  RESULT=$(search awk '
    /allow_unverified_ssl/ && /default\s*=\s*true/ {
      print FILENAME ":" NR ": " $0
    }
  ' "$f")
  [[ -n "$RESULT" ]] && HITS="${HITS}${RESULT}"$'\n'
done
HITS=$(echo "$HITS" | sed '/^$/d')
if [[ -n "$HITS" ]]; then
  fail "Found allow_unverified_ssl defaulting to true:"
  echo "$HITS" | while read -r line; do echo "    $line"; done
else
  pass "No allow_unverified_ssl defaults to true"
fi

# ─── Check 7: SSH private key file leaks ─────────────────────────────────────
# Related findings: H-02
# Severity: HIGH
# Private keys loaded via file() are embedded in state by the provisioner.
echo "CHECK 7: SSH private key file() usage [H-02]"
CHECKS_RUN=$((CHECKS_RUN + 1))
HITS=""
for f in "${TF_FILES[@]}"; do
  # grep the file then filter out comments (lines starting with # or //)
  RAW=$(search grep -n 'private_key\s*=\s*file(' "$f")
  RESULT=""
  if [[ -n "$RAW" ]]; then
    RESULT=$(printf '%s\n' "$RAW" | grep -v '^\s*[0-9]*:\s*#' | grep -v '^\s*[0-9]*:\s*//' | sed "s|^|$f:|" || true)
  fi
  [[ -n "$RESULT" ]] && HITS="${HITS}${RESULT}"$'\n'
done
HITS=$(echo "$HITS" | sed '/^$/d')
if [[ -n "$HITS" ]]; then
  fail "Found private_key loaded via file() (leaks key material to state):"
  echo "$HITS" | while read -r line; do echo "    $line"; done
else
  pass "No private_key file() patterns found"
fi

# ─── Check 8: Unpinned provider versions ─────────────────────────────────────
# Related findings: MED-01, MED-02, MED-03
# Severity: MEDIUM
# Unpinned providers allow supply-chain attacks via malicious provider versions.
echo "CHECK 8: Unpinned provider versions [MED-01, MED-02, MED-03]"
CHECKS_RUN=$((CHECKS_RUN + 1))
HITS=""
for f in "${TF_FILES[@]}"; do
  RESULT=$(search awk '
    /required_providers/ { in_rp = 1; rp_depth = 0; next }
    in_rp && /{/ { rp_depth++ }
    in_rp && /}/ { rp_depth--; if (rp_depth <= 0) in_rp = 0; next }
    in_rp && /source\s*=/ {
      source_line = NR; has_version = 0
      # Look for version in the same provider block
    }
    in_rp && /version\s*=/ { has_version = 1 }
    in_rp && /}/ && source_line && !has_version {
      print FILENAME ":" source_line ": provider missing version constraint"
      source_line = 0; has_version = 0
    }
  ' "$f")
  [[ -n "$RESULT" ]] && HITS="${HITS}${RESULT}"$'\n'
done
HITS=$(echo "$HITS" | sed '/^$/d')
if [[ -n "$HITS" ]]; then
  fail "Found unpinned provider versions:"
  echo "$HITS" | while read -r line; do echo "    $line"; done
else
  pass "All providers have version constraints"
fi

# ─── Check 9: .terraform.lock.hcl in .gitignore ─────────────────────────────
# Related findings: F-11, M-03
# Severity: LOW
# Lock files must be committed for reproducible builds and supply-chain integrity.
echo "CHECK 9: Lock file exclusion from git [F-11, M-03]"
CHECKS_RUN=$((CHECKS_RUN + 1))
CHECK9_HITS=""
for repo in "${REPOS[@]}"; do
  gitignore="${TERRAFORM_BASE_PATH}/${repo}/.gitignore"
  if [[ -f "$gitignore" ]]; then
    # Assigned first, then tested: `search` exits from a subshell, and inside a
    # test the exit would be swallowed and read as "no match".
    LOCK_HIT=$(search grep -n '\.terraform\.lock\.hcl' "$gitignore")
    if [[ -n "$LOCK_HIT" ]]; then
      CHECK9_HITS="${CHECK9_HITS}${gitignore}"$'\n'
    fi
  fi
done
CHECK9_HITS=$(echo "$CHECK9_HITS" | sed '/^$/d')
if [[ -n "$CHECK9_HITS" ]]; then
  fail "Found .terraform.lock.hcl excluded in .gitignore:"
  echo "$CHECK9_HITS" | while read -r f; do echo "    $f"; done
else
  pass "Lock files not excluded from git"
fi

# ─── Check 10: Hardcoded vault addresses ─────────────────────────────────────
# Related findings: H-01
# Severity: HIGH
# Vault addresses should be parameterized to avoid topology leaks and enable
# environment portability.
echo "CHECK 10: Hardcoded Vault addresses [H-01]"
CHECKS_RUN=$((CHECKS_RUN + 1))
HITS=""
for f in "${TF_FILES[@]}"; do
  # Match vault provider address blocks only — exclude backend URLs and lock/unlock addresses
  RAW=$(search grep -n 'address\s*=\s*"https\?://[^"]*vault[^"]*"' "$f")
  RESULT=""
  if [[ -n "$RAW" ]]; then
    RESULT=$(printf '%s\n' "$RAW" \
      | grep -v 'var\.' \
      | grep -v 'lock_address' \
      | grep -v 'unlock_address' \
      | grep -v '/api/v4/projects/' \
      | sed "s|^|$f:|" || true)
  fi
  [[ -n "$RESULT" ]] && HITS="${HITS}${RESULT}"$'\n'
done
HITS=$(echo "$HITS" | sed '/^$/d')
if [[ -n "$HITS" ]]; then
  fail "Found hardcoded Vault addresses (should use variables):"
  echo "$HITS" | while read -r line; do echo "    $line"; done
else
  pass "No hardcoded Vault addresses found"
fi

# ─── Check 11: Provider blocks inside child modules ─────────────────────────
# Related findings: HIGH-01
# Severity: HIGH
# Provider configuration in child modules forces credentials through module
# variables, which always persist to state.
echo "CHECK 11: Provider blocks in child modules [HIGH-01]"
CHECKS_RUN=$((CHECKS_RUN + 1))
HITS=""
for repo in "${REPOS[@]}"; do
  repo_path="${TERRAFORM_BASE_PATH}/${repo}"
  [[ -d "$repo_path" ]] || continue
  # Only check inside modules/ directories (child modules, not root modules)
  MODULE_FILES=$(mktemp)
  FIND_STATUS=0
  find "$repo_path" -path '*/modules/*' -name '*.tf' -type f -not -path '*/.terraform/*' -print0 \
    > "$MODULE_FILES" || FIND_STATUS=$?
  if (( FIND_STATUS != 0 )); then
    rm -f "$MODULE_FILES"
    echo "ERROR: find exited ${FIND_STATUS} walking ${repo_path}; an incomplete file list is not a clean audit" >&2
    exit 2
  fi
  while IFS= read -r -d '' f; do
    RAW=$(search grep -n '^provider "' "$f")
    RESULT=""
    if [[ -n "$RAW" ]]; then
      RESULT=$(printf '%s\n' "$RAW" | sed "s|^|$f:|")
    fi
    [[ -n "$RESULT" ]] && HITS="${HITS}${RESULT}"$'\n'
  done < "$MODULE_FILES"
  rm -f "$MODULE_FILES"
done
HITS=$(echo "$HITS" | sed '/^$/d')
if [[ -n "$HITS" ]]; then
  fail "Found provider blocks inside child modules (anti-pattern):"
  echo "$HITS" | while read -r line; do echo "    $line"; done
else
  pass "No provider blocks in child modules"
fi

# ─── Check 12: Default credentials in example files ─────────────────────────
# Related findings: M-01
# Severity: MEDIUM
# Example files should never contain real or commonly-known passwords.
echo "CHECK 12: Default credentials in example files [M-01]"
CHECKS_RUN=$((CHECKS_RUN + 1))
HITS=""
for repo in "${REPOS[@]}"; do
  repo_path="${TERRAFORM_BASE_PATH}/${repo}"
  [[ -d "$repo_path" ]] || continue
  REPO_HITS=$(search grep -rln 'Welcome1!\|P@ssw0rd\|password123\|changeme\|admin123' "$repo_path" \
    --include='*.example' --include='*.sample' --include='*.tfvars.example')
  [[ -n "$REPO_HITS" ]] && HITS="${HITS}${REPO_HITS}"$'\n'
done
HITS=$(echo "$HITS" | sed '/^$/d')
if [[ -n "$HITS" ]]; then
  fail "Found default/known credentials in example files:"
  echo "$HITS" | while read -r f; do echo "    $f"; done
else
  pass "No default credentials in example files"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
CHECKS_PASSED=$((CHECKS_RUN - FAILURES))
echo "Results: ${CHECKS_PASSED}/${CHECKS_RUN} checks passed, ${FAILURES} failed"
echo "════════════════════════════════════════"

if [[ $FAILURES -gt 0 ]]; then
  echo -e "${RED}AUDIT FAILED${NC}"
  exit 1
else
  echo -e "${GREEN}AUDIT PASSED${NC}"
  exit 0
fi
