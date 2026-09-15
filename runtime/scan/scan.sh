#!/bin/sh
# SBOM and image-scan helpers for the components that act on a built image.
#
# Functions only; sourcing this runs nothing. The scanner components embed it
# verbatim after runtime/registry/image-json.sh, whose identity helpers it uses,
# so both are in scope for the script block that follows.
#
# Embedded rather than called as runtime/scan/scan.sh for the reason in section
# 12: `include:` imports YAML, not files, so a consumer job never has this
# repository checked out. tests/runtime/test_template_script_lockstep.py proves
# each embedded copy is byte-identical to this file.
#
# POSIX sh with busybox applets only. anchore/syft and anchore/grype ship
# busybox and the scanner binary, so there is no jq and no python3 here.

# The scanners' own template output is what counts findings and components, so
# the tool parses its own data model and nothing greps its JSON. One severity or
# one package name per line; the counts are then exact.
ci_tpl_write_scan_templates() {
    mkdir -p "$1"
    printf '{{range .artifacts}}{{.name}}\n{{end}}\n' > "$1/syft-components.tmpl"
    printf '{{range .Matches}}{{.Vulnerability.Severity}}\n{{end}}\n' > "$1/grype-severities.tmpl"
    printf '{{ range . }}{{ range .Vulnerabilities }}{{ .Severity }}\n{{ end }}{{ end }}\n' \
        > "$1/trivy-severities.tmpl"
}

# Count the lines of a listing, telling "nothing matched" apart from "could not
# look". grep exits 1 for the first and 2 or more for the second: an unreadable
# file, a directory where a file was expected, a pattern the tool refused. Every
# count here was written `count=$(grep -c ...) || count=0`, which gave all of
# them the same answer, so a severity listing the job could not read produced a
# clean scan-result.json and gated on nothing. `|| true` has the same defect and
# is not the fix.
#
# ci_tpl_count_matching <file> <grep-option>... <pattern>
ci_tpl_count_matching() {
    ci_tpl_count_file=$1
    shift
    ci_tpl_count_value=$(grep -c "$@" "$ci_tpl_count_file")
    ci_tpl_count_status=$?
    if [ "$ci_tpl_count_status" -ge 2 ]; then
        ci_tpl_fail "grep exited $ci_tpl_count_status reading $ci_tpl_count_file; a listing that cannot be read is not a count of zero"
        return 1
    fi
    [ "$ci_tpl_count_status" -eq 0 ] || ci_tpl_count_value=0
    echo "$ci_tpl_count_value"
}

# --------------------------------------------------------------------------
# Authoritative SBOM
# --------------------------------------------------------------------------

# Validate a CycloneDX SBOM and write the subject record that says which image
# it describes. Section 9.2 requires both files; the predecessor emitted only
# the SBOM, and its sole check was a grep count of '"name"', so a truncated or
# component-free document passed.
#
# ci_tpl_write_sbom_subject <sbom> <components> <image.json> <output>
ci_tpl_write_sbom_subject() {
    sbom=$1
    components=$2
    identity=$3
    output=$4

    [ -s "$sbom" ] || { ci_tpl_fail "syft produced no SBOM at $sbom"; return 1; }

    # The three ways this file can be wrong: not CycloneDX at all, cut short by
    # a killed writer, or structurally fine and empty of packages.
    tr -d ' \n' < "$sbom" | grep -q '"bomFormat":"CycloneDX"' ||
        { ci_tpl_fail "$sbom is not a CycloneDX document"; return 1; }
    [ "$(tr -d ' \n\r' < "$sbom" | tail -c 1)" = "}" ] ||
        { ci_tpl_fail "$sbom is truncated: it does not end with a closing brace"; return 1; }
    [ -f "$components" ] ||
        { ci_tpl_fail "syft wrote no component listing at $components"; return 1; }

    component_count=$(ci_tpl_count_matching "$components" .) || return 1
    [ "$component_count" -ge 1 ] || {
        ci_tpl_fail "$sbom catalogued no components; that is a producer failure, not a clean SBOM"
        return 1
    }

    reference=$(ci_tpl_image_reference "$identity") || return 1
    repository=$(ci_tpl_json_string "$identity" repository)
    digest=$(ci_tpl_json_string "$identity" digest)
    source_commit=$(ci_tpl_json_string "$identity" source_commit)
    sbom_sha256=$(sha256sum "$sbom" | cut -d' ' -f1)

    cat > "$output" <<JSON
{
  "schema_version": 1,
  "image_reference": "$reference",
  "repository": "$repository",
  "digest": "$digest",
  "source_commit": "$source_commit",
  "pipeline_id": "${CI_PIPELINE_ID:-}",
  "job_id": "${CI_JOB_ID:-}",
  "sbom_file": "$(basename "$sbom")",
  "sbom_sha256": "$sbom_sha256",
  "sbom_format": "CycloneDX JSON",
  "component_count": $component_count,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

    [ -s "$output" ] || { ci_tpl_fail "failed to write $output"; return 1; }
    echo "subject recorded: $reference ($component_count components)"
}

# Print the image reference an SBOM claims, but only if it is the image we meant
# to scan. Section 9.2: a job scanning a supplied SBOM validates its declared
# subject; it does not switch targets because a file named sbom.cdx.json exists.
# The predecessor chose its target with `[ -f "sbom.cdx.json" ]`.
#
# ci_tpl_match_sbom_subject <subject.json> <image.json>
ci_tpl_match_sbom_subject() {
    [ -s "$1" ] ||
        { ci_tpl_fail "the SBOM producer left no subject record at $1"; return 1; }
    subject_reference=$(ci_tpl_json_string "$1" image_reference) ||
        { ci_tpl_fail "$1 declares no image_reference"; return 1; }
    [ -n "$subject_reference" ] ||
        { ci_tpl_fail "$1 declares no image_reference"; return 1; }

    intended_reference=$(ci_tpl_image_reference "$2") || return 1

    [ "$subject_reference" = "$intended_reference" ] || {
        ci_tpl_fail "the SBOM describes $subject_reference, not the image to scan ($intended_reference)"
        return 1
    }
    echo "$intended_reference"
}

# --------------------------------------------------------------------------
# Scan result and gate
# --------------------------------------------------------------------------

ci_tpl_severity_rank() {
    case "$(echo "$1" | tr 'A-Z' 'a-z')" in
        critical) echo 5 ;;
        high) echo 4 ;;
        medium) echo 3 ;;
        low) echo 2 ;;
        negligible) echo 1 ;;
        *) echo 0 ;;
    esac
}

# A report that is missing, empty, or without the marker its scanner always
# writes means the scan did not complete. That is a failure in advisory mode as
# well as blocking mode (section 10.1), and it is how an unhydratable database
# stops reading as a clean image.
#
# ci_tpl_require_report <path> <marker>
ci_tpl_require_report() {
    [ -s "$1" ] || {
        ci_tpl_fail "the scanner wrote no report at $1; that is a scanner failure, not a clean scan"
        return 1
    }
    grep -q "$2" "$1" || {
        ci_tpl_fail "$1 does not contain the expected marker $2; that is a scanner failure, not a clean scan"
        return 1
    }
}

# Count the severities the scanner reported, write scan-result.json, and decide.
# Returns 0 when nothing gates, 1 when a blocking finding meets the threshold.
# Evidence problems have already failed through ci_tpl_require_report.
#
# ci_tpl_write_scan_result <severities> <output> <scanner> <version> <subject>
#                          <kind> <threshold> <policy-mode> <ignore-unfixed>
#                          <database-updated-at> [<report> ...]
ci_tpl_write_scan_result() {
    severities=$1; output=$2; scanner=$3; scanner_version=$4
    subject_reference=$5; subject_kind=$6; threshold=$7; policy_mode=$8
    ignore_unfixed=$9
    shift 9
    database_updated_at=$1
    shift

    case "$policy_mode" in
        blocking|advisory) ;;
        *) ci_tpl_fail "policy mode must be blocking or advisory, not '$policy_mode'"; return 1 ;;
    esac

    threshold_rank=$(ci_tpl_severity_rank "$threshold")
    [ "$threshold_rank" -ne 0 ] ||
        { ci_tpl_fail "'$threshold' is not a known severity"; return 1; }
    [ -f "$severities" ] ||
        { ci_tpl_fail "the scanner wrote no severity listing at $severities"; return 1; }

    critical=$(ci_tpl_count_matching "$severities" -ci '^critical$') || return 1
    high=$(ci_tpl_count_matching "$severities" -ci '^high$') || return 1
    medium=$(ci_tpl_count_matching "$severities" -ci '^medium$') || return 1
    low=$(ci_tpl_count_matching "$severities" -ci '^low$') || return 1
    negligible=$(ci_tpl_count_matching "$severities" -ci '^negligible$') || return 1
    total=$(ci_tpl_count_matching "$severities" .) || return 1
    unknown=$((total - critical - high - medium - low - negligible))
    [ "$unknown" -ge 0 ] || unknown=0

    at_or_above=0
    if [ "$threshold_rank" -le 5 ]; then at_or_above=$((at_or_above + critical)); fi
    if [ "$threshold_rank" -le 4 ]; then at_or_above=$((at_or_above + high)); fi
    if [ "$threshold_rank" -le 3 ]; then at_or_above=$((at_or_above + medium)); fi
    if [ "$threshold_rank" -le 2 ]; then at_or_above=$((at_or_above + low)); fi
    if [ "$threshold_rank" -le 1 ]; then at_or_above=$((at_or_above + negligible)); fi

    if [ "$ignore_unfixed" = "true" ]; then
        unfixed_treatment=ignored
    else
        unfixed_treatment=gated
    fi

    report_json=''
    separator=''
    for report_path in "$@"; do
        [ -n "$report_path" ] || continue
        report_json="$report_json$separator
      {\"path\": \"$report_path\", \"sha256\": \"$(sha256sum "$report_path" | cut -d' ' -f1)\"}"
        separator=','
    done

    cat > "$output" <<JSON
{
  "schema_version": 1,
  "status": "completed",
  "subject": {
    "kind": "$subject_kind",
    "reference": "$subject_reference"
  },
  "scanner": {
    "name": "$scanner",
    "version": "$scanner_version"
  },
  "database": {
    "updated_at": "${database_updated_at:-unresolved}"
  },
  "counts": {
    "critical": $critical,
    "high": $high,
    "medium": $medium,
    "low": $low,
    "negligible": $negligible,
    "unknown": $unknown,
    "total": $total,
    "at_or_above_threshold": $at_or_above
  },
  "policy": {
    "mode": "$policy_mode",
    "threshold": "$threshold",
    "unfixed_vulnerabilities": "$unfixed_treatment",
    "exceptions": []
  },
  "reports": [$report_json
  ],
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON

    [ -s "$output" ] || { ci_tpl_fail "failed to write $output"; return 1; }
    echo "scan completed: $total finding(s); $at_or_above at or above $threshold"

    if [ "$at_or_above" -gt 0 ]; then
        if [ "$policy_mode" = "advisory" ]; then
            echo "advisory mode: $at_or_above finding(s) at or above $threshold recorded, not gating"
            return 0
        fi
        ci_tpl_fail "$at_or_above finding(s) at or above $threshold on $subject_reference"
        return 1
    fi
    return 0
}
