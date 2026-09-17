#!/bin/sh
# Refuse a build whose two gate inputs disagree.
#
# The three builders choose between a stage barrier and a `needs:` list with
# mutually exclusive rules on an `include:`, and the rule can only read
# `gate-jobs-set`: an array input cannot be tested for emptiness in an include
# rule (measured on gitlab.example.com 18.9.1-ee, 2026/09/17 — with an empty list
# the condition never matches, with a non-empty one GitLab answers
# `include:rule if invalid expression syntax`).
#
# Nothing in the YAML can then check the boolean against the array, and both
# ways of disagreeing are silent:
#
#   gate-jobs filled, gate-jobs-set false -> the list is read by nothing, the
#     job keeps `dependencies: []`, and the consumer believes it is gated.
#   gate-jobs-set true, gate-jobs empty   -> `needs: []`, so the job has no
#     edges AND no stage barrier: it starts at once, ahead of every gate the
#     stage used to hold it behind.
#
# The second is the dangerous one and neither is visible in a green pipeline,
# so the job says it before it builds anything.
#
# CI_TPL_GATE_JOBS carries the array as a JSON literal, because a GitLab
# variable cannot hold a list. A value that is not a JSON list is not a gate
# list: it reads as empty rather than as one entry.
ci_tpl_require_gate_inputs() {
    gates=$(echo "${CI_TPL_GATE_JOBS#json}" | tr -d ' \n')
    case "$gates" in
        '['*']') ;;
        *) gates='[]' ;;
    esac
    set_flag=${CI_TPL_GATE_JOBS_SET:-false}
    if [ "$gates" != '[]' ] && [ "$set_flag" != 'true' ]; then
        echo "ERROR: gate-jobs names jobs but gate-jobs-set is $set_flag, so nothing" >&2
        echo "ERROR: reads the list: this build keeps its stage barrier and waits for" >&2
        echo "ERROR: no named gate. Set gate-jobs-set: true beside gate-jobs." >&2
        return 1
    fi
    if [ "$gates" = '[]' ] && [ "$set_flag" = 'true' ]; then
        echo "ERROR: gate-jobs-set is true but gate-jobs is empty, so this build gets" >&2
        echo "ERROR: 'needs: []': no edges and no stage barrier either, and it starts" >&2
        echo "ERROR: ahead of every gate its stage used to hold it behind. Name the" >&2
        echo "ERROR: gates in gate-jobs, or set gate-jobs-set: false." >&2
        return 1
    fi
    return 0
}
