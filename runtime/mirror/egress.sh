#!/bin/sh
# Outbound proxy selection for the container mirror components.
#
# Straight-line rather than a function library: this is the first before_script
# entry of all three components and has to take effect before the package
# manager makes its first request. GitLab runs before_script and script in one
# shell, so the exports reach the job's own commands.
#
# Both spellings of every variable are exported. curl reads the lower case
# names, skopeo and the AWS CLI read the upper case ones, and setting only one
# of each pair is a proxy half the job ignores.
set -eu

if [ -n "${CI_TPL_EGRESS_PROXY:-}" ]; then
  HTTP_PROXY=$CI_TPL_EGRESS_PROXY
  HTTPS_PROXY=$CI_TPL_EGRESS_PROXY
  http_proxy=$CI_TPL_EGRESS_PROXY
  https_proxy=$CI_TPL_EGRESS_PROXY
  NO_PROXY=${CI_TPL_NO_PROXY:-}
  no_proxy=${CI_TPL_NO_PROXY:-}
  export HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy
  echo "egress: via proxy $CI_TPL_EGRESS_PROXY"
else
  echo "egress: direct"
fi
