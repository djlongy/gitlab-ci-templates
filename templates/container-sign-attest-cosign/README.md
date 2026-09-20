# container-sign-attest-cosign

## Publish the verification key

The component signs with a key the signer holds, and by default that key
lives in Vault Transit (`hashivault://<key>`), so the private half never
leaves Vault. The public half has to reach reviewers some other way, and the
sign job's log is the wrong way: verifying a pipeline with a key that same
pipeline printed proves only that the pipeline is self-consistent.

Export the public key once and commit it to this directory as `cosign.pub`.
Reviewers and consumers then verify against a file that is reviewed, has
history, and changes only through a merge request:

```
cosign verify --key templates/container-sign-attest-cosign/cosign.pub \
  --insecure-ignore-tlog \
  <registry>/<repository>@sha256:<digest>
```

`--insecure-ignore-tlog` is required for a signature that was not uploaded to
a transparency log, which is the case whenever the signer has no path to
Rekor. Without it `cosign verify` fails looking for a log entry that was never
made.

`tests/contracts/test_cosign_inventory.py` checks the committed key is a
public PEM and, when cosign is on PATH, that it verifies every digest in
`images/runner-images.yml`. Both checks skip while no key is committed.
