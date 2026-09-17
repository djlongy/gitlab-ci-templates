#!/bin/sh
# Refuse to publish something no gate looked at.
#
# `gate-jobs` defaults to an empty array on every publishing component, because
# only the composition knows the instance job names. An empty list is not a
# small configuration detail: it is a release with no scan, no signature and no
# readiness check in front of it, and it compiles, runs and goes green. Lint
# cannot see it either, since there is no missing producer to complain about.
#
# So the job says it out loud. A consumer that means to publish ungated sets
# allow-ungated and gets a warning; a consumer that forgot gets a failure
# naming the input that fixes it.
#
# CI_TPL_GATE_JOBS carries the array as a JSON literal, because a GitLab
# variable cannot hold a list. Anything that is not an empty array counts as
# gated: `needs:` already refused to create the pipeline if an entry named a job
# that is not there.
#
# $1 is one further gate name, for a component that still accepts a scalar
# alongside the array. Empty when there is none.
ci_tpl_require_gate() {
    gates=$(echo "${CI_TPL_GATE_JOBS#json}" | tr -d ' \n')
    case "$gates" in
        # A JSON list, which is what the template renders. Anything else is a
        # variable that did not arrive, and an unreadable gate list is no gate
        # list: it fails rather than being read as one entry.
        '['*']') ;;
        *) gates='[]' ;;
    esac
    if [ -n "${1:-}" ] || [ "$gates" != '[]' ]; then
        return 0
    fi
    if [ "${CI_TPL_ALLOW_UNGATED:-false}" = 'true' ]; then
        echo "WARNING: gate-jobs is empty and allow-ungated is set, so nothing in this"
        echo "WARNING: pipeline has checked what this job is about to publish."
        return 0
    fi
    echo "ERROR: gate-jobs is empty, so nothing in this pipeline has checked what this" >&2
    echo "ERROR: job is about to publish: no scan, no signature, no readiness gate." >&2
    echo "ERROR: name those jobs in gate-jobs, or set allow-ungated: true to publish" >&2
    echo "ERROR: anyway and have this warning in the log instead." >&2
    return 1
}
