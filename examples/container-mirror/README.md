# container-mirror consumer example

`.gitlab-ci.yml` here is the whole consumer configuration. Replace
`REPLACE_WITH_APPROVED_SHARED_CI_REF` with the approved shared-CI commit SHA or
protected tag before use.

The consumer keeps only data: `images.txt` (one reference per line, tag or tag
and digest) and, when an RKE2 release should be resolved as well,
a manifest named by the `config-file` input.

Credentials are project CI/CD variables. `REGISTRY_USER` and
`REGISTRY_PASSWORD` cover the registry, `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` the object store. Setting `vault-addr` swaps all four
for a Vault JWT login, which is the shape a consumer with an estate Vault uses;
`vault-role`, `vault-kv-path` and `vault-s3-kv-path` then become required.

`changes-paths` names `.gitlab-ci.yml` on purpose. Narrow the list to the data
files and a Renovate bump of the include ref stops running the pipeline that
would prove the bump.

`egress-proxy: "$EGRESS_PROXY"` is a useful shape where some projects tunnel.
The value lands in a job variable and the runner expands it at job start, so a
project that sets `EGRESS_PROXY` tunnels and a project that does not reaches the
internet directly, without two pipeline definitions.
