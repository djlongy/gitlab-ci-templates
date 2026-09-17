#!/bin/sh
# Trust an extra certificate authority inside a job container.
#
# Its own file rather than part of image-json.sh because the signing component
# needs it and needs nothing else there: that job runs Python, not the shell
# image helpers, and embedding 250 lines of JSON handling to reach one function
# would be worse than either.
#
# It depends on nothing else here, deliberately: the signing component embeds
# this file and no other.
#
# Embedded verbatim by every component that talks to a registry over TLS;
# tests/runtime/registry/test_ca_bundle.py drives this copy.
# Trust one more certificate authority, for a registry issued by an internal
# one. The PEM comes from the CI variable named by CI_TPL_CA_BUNDLE_VARIABLE,
# which may be a GitLab file variable (the value is a path) or an ordinary one
# (the value is the PEM itself). Nothing is downloaded: a job that has to fetch
# its trust anchor cannot run at a site with no egress, which is the site that
# needs this input.
#
# Empty or unset skips it. A variable holding no certificate is an error rather
# than an empty success, because the job would otherwise fail later with a TLS
# error naming the registry instead of the bundle.
ci_tpl_trust_ca_bundle() {
    [ -n "${CI_TPL_CA_BUNDLE_VARIABLE:-}" ] || return 0
    bundle=$(printenv "$CI_TPL_CA_BUNDLE_VARIABLE") || bundle=''
    if [ -z "$bundle" ]; then
        echo "ERROR: ca-bundle-variable names \$${CI_TPL_CA_BUNDLE_VARIABLE}, which is empty or not defined" >&2
        return 1
    fi
    pem=${CI_TPL_WORK:-/tmp}/ci-tpl-ca-bundle.pem
    if [ -f "$bundle" ]; then
        cat "$bundle" > "$pem"
    else
        printf '%s\n' "$bundle" > "$pem"
    fi
    certs=$(grep -c 'BEGIN CERTIFICATE' "$pem") || certs=0
    if [ "$certs" -eq 0 ]; then
        echo "ERROR: \$${CI_TPL_CA_BUNDLE_VARIABLE} carries no PEM certificate" >&2
        return 1
    fi
    store=${CI_TPL_CA_STORE:-/etc/ssl/certs/ca-certificates.crt}
    if cat "$pem" >> "$store" 2>/dev/null; then
        echo "trusted ${certs} extra certificate(s) from \$${CI_TPL_CA_BUNDLE_VARIABLE}"
        return 0
    fi
    # A non-root execution image cannot write the system store, and refusing
    # there would mean the input works only for images that run as root.
    # Combine the store with the extra anchors and name the copy in the three
    # variables the clients in these jobs read: Go (buildctl, trivy, syft,
    # cosign) reads SSL_CERT_FILE, python-requests reads REQUESTS_CA_BUNDLE and
    # curl reads CURL_CA_BUNDLE.
    combined=${CI_TPL_WORK:-/tmp}/ci-tpl-ca-combined.pem
    if [ -r "$store" ]; then
        cat "$store" "$pem" > "$combined"
    else
        cat "$pem" > "$combined"
    fi
    SSL_CERT_FILE=$combined
    REQUESTS_CA_BUNDLE=$combined
    CURL_CA_BUNDLE=$combined
    export SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
    echo "trusted ${certs} extra certificate(s) from \$${CI_TPL_CA_BUNDLE_VARIABLE}"
    echo "the trust store at ${store} is not writable here, so SSL_CERT_FILE names ${combined}"
}
