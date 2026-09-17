# Build an image and push it to Quay

This guide is for someone whose project has a Dockerfile and a Quay registry,
and who wants a pipeline that builds the image and pushes it. It assumes
nothing about this repository.

Throughout, the shared CI project is written `platform/gitlab-ci-templates`,
the registry `registry.example.com` and the organisation `platform`. Substitute
your own. Everything here works the same against any registry that speaks the
OCI distribution API: Quay, Harbor, Artifactory, GitLab's own.

## The three variables, and why a wrong name looks like a missing credential

The build job needs a username and a password, and it reads them from CI
variables. What it must be told is the **name** of each variable, never the
value:

| Input | What it takes | Default |
|---|---|---|
| `registry-username-variable` | the NAME of the variable holding the username | `HARBOR_USER` |
| `registry-password-variable` | the NAME of the variable holding the password | `HARBOR_PASSWORD` |
| `ca-bundle-variable` | the NAME of a variable holding a PEM bundle to trust | empty, skipped |

If you set `QUAY_USER` and `QUAY_TOKEN` on your project but pass neither input,
the job looks for `HARBOR_USER` and `HARBOR_PASSWORD`, finds nothing, and stops
with

```
ERROR: registry credentials are not set; this job cannot push to registry.example.com
ERROR: username, from $HARBOR_USER: empty or not defined
ERROR: password, from $HARBOR_PASSWORD: empty or not defined
```

The two names in that message are the whole diagnosis: they are what the job
read, and they are what the two inputs change.

## Prepare Quay

Quay does not create a repository on first push unless the pushing account may
create one, and a robot account may not. Create the repository first and give
the robot write on it. Four steps, once per repository:

1. **Organisation.** Quay UI, *Create New Organization*, name it `platform`.
   By API: `POST /api/v1/organization/ {"name": "platform", "email": "..."}`.
2. **Repository.** *Create New Repository* in that organisation, named after the
   image, private. By API: `POST /api/v1/repository` with `{"namespace":
   "platform", "repository": "myapp", "visibility": "private", "repo_kind":
   "image"}`.
3. **Robot account.** Organisation → *Robot Accounts* → *Create Robot Account*,
   named `ci`. Its full name is `platform+ci`, and Quay shows its token once.
   By API: `PUT /api/v1/organization/platform/robots/ci`, which returns the
   token in its response.
4. **Permission.** Repository → *Settings* → *User and Robot Permissions*, add
   `platform+ci` with **Write**. By API: `PUT
   /api/v1/repository/platform/myapp/permissions/user/platform%2Bci` with
   `{"role": "write"}`.

Write is the least privilege that can push: it covers the pull the build also
needs to check what is already there. Admin is not required and grants the robot
the ability to change the repository's own permissions.

Then set two CI variables on the consuming project, *Settings → CI/CD →
Variables*:

- `QUAY_USER` = `platform+ci`. Not masked: Quay robot names contain `+`, which
  GitLab's masking rules reject.
- `QUAY_TOKEN` = the robot's token. **Masked.**

Leave both unprotected while you are proving the pipeline on a branch. A
protected variable reaches protected branches and protected tags only, and a
job on an ordinary branch sees nothing at all — which prints exactly the error
above, with the right variable names in it.

## If the registry certificate comes from your own CA

A job container trusts the public authorities its base image shipped with and
nothing else. A registry whose certificate an internal authority issued is
therefore unreachable from the build, and it fails with a TLS error naming the
registry rather than the trust store.

Add the PEM chain of the issuing authority, and the root above it, as a CI
variable of type **File** — say `ESTATE_CA_BUNDLE` — and pass its name as
`ca-bundle-variable`. Nothing is downloaded: the bundle is already in the job's
environment, which is what a site with no egress needs. A variable of the
ordinary type holding the PEM text works too.

The job prints `trusted 2 extra certificate(s) from $ESTATE_CA_BUNDLE` before it
does anything else. Where the execution image runs as a non-root user and its
trust store is read-only, it says so and names a combined bundle in
`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and `CURL_CA_BUNDLE` instead.

## The whole .gitlab-ci.yml

The smallest thing that builds and pushes. One include, one component:

```yaml
include:
  - project: 'platform/gitlab-ci-templates'
    ref: 'REPLACE_WITH_APPROVED_SHARED_CI_REF'
    file: '/templates/container-build-buildkit/template.yml'
    inputs:
      instance: demo
      image-repository: 'registry.example.com/platform/myapp'
      registry-username-variable: QUAY_USER
      registry-password-variable: QUAY_TOKEN
      ca-bundle-variable: ESTATE_CA_BUNDLE
      tags:
        - '$CI_COMMIT_SHORT_SHA'
```

`image-repository` is the whole repository: registry host, then the path, with
no tag and no digest. The first path segment is the registry the job
authenticates to, so there is no separate registry input to keep in step with
it. The tags are pushed as aliases; the identity the job records is always the
digest.

That include creates one job, `demo:container-build-buildkit`, in the `build`
stage, on a default-branch push and on a tag. It does not run on a merge
request: pushing an image is a mutation.

**The release chain instead.** `pipelines/container-buildkit.yml` is the same
build with secret detection, SAST, a filesystem scan, a lockfile check, an
SBOM, an image scan and a cosign signature around it, and it takes the same
three inputs plus `semgrep-rules` and `lockfiles`. Use it when the image is
going somewhere that matters; use the component above when you want to prove
the registry works.

```yaml
include:
  - project: 'platform/gitlab-ci-templates'
    ref: 'REPLACE_WITH_APPROVED_SHARED_CI_REF'
    file: '/pipelines/container-buildkit.yml'
    inputs:
      instance: myapp
      image-repository: 'registry.example.com/platform/myapp'
      registry-username-variable: QUAY_USER
      registry-password-variable: QUAY_TOKEN
      ca-bundle-variable: ESTATE_CA_BUNDLE
      semgrep-rules: 'ci/semgrep-rules.yml'
      lockfiles: 'none'
```

## What a successful build prints

```
trusted 2 extra certificate(s) from $ESTATE_CA_BUNDLE
#1 [internal] load build definition from Dockerfile
#4 [internal] load metadata for registry.example.com/mirror/library/alpine:3.20
#8 exporting to image
#8 pushing layers
#8 pushing manifest for registry.example.com/platform/myapp:1a2b3c4d
pushed registry.example.com/platform/myapp@sha256:<64 hex> as registry.example.com/platform/myapp:1a2b3c4d
```

The last line is the point: a repository and a digest, which is what every
later job consumes. The job also writes
`.ci-artifacts/demo/container-build-buildkit/image.json`, the identity record,
alongside BuildKit's own `metadata.json` as the evidence the digest came from.

Check it from anywhere that can reach the registry. From a runner, in a job,
with a client that is itself in your mirror:

```yaml
verify-pushed-image:
  image:
    name: registry.example.com/mirror/regclient/regctl:alpine
    entrypoint: ['']
  script:
    # regctl runs as a non-root uid, so the bundle is named rather than installed.
    - export SSL_CERT_FILE="$ESTATE_CA_BUNDLE"
    - printf '%s' "$QUAY_TOKEN" | regctl registry login "$REGISTRY" -u "$QUAY_USER" --pass-stdin
    - regctl image digest "$REGISTRY/platform/myapp:1a2b3c4d"
    - regctl manifest head "$REGISTRY/platform/myapp:1a2b3c4d"
```

`skopeo inspect docker://…` and `crane digest …` print the same thing if either
is in your mirror. Neither is on Docker Hub, so a proxy cache of Docker Hub
cannot serve them; `regclient/regctl` is.

## What the release chain leaves in Quay

Quay 3.15.7, checked on a real push:

| Artefact | Where it ends up | What Quay does with it |
|---|---|---|
| the image | `platform/myapp:<tag>` | stored as an OCI manifest; the digest matches `image.json` |
| OCI labels | the image config | shown in the UI and returned by `GET /api/v1/repository/<org>/<repo>/manifest/<digest>/labels` |
| cosign signature | tag `sha256-<digest>.sig` | an ordinary tag, listed beside the image |
| SBOM and vulnerability attestations | tag `sha256-<digest>.att` | two DSSE envelopes, predicate types `https://cyclonedx.org/bom` and `https://cosign.sigstore.dev/attestation/vuln/v1` |
| the SBOM itself, the scan report, `image.json` | GitLab job artifacts | nothing is pushed to the registry |

What Quay drops: the **referrers API** answers `200` with an empty list for a
signed image, because cosign writes its artefacts under the `.sig` and `.att`
tag convention rather than as OCI 1.1 referrers. `cosign verify` finds them;
`regctl artifact list` and anything else that discovers by referrer does not.

## The three failures you will actually hit

**1. "registry credentials are not set".** The names in the message are what the
job read. Either the variables have other names — pass those names through the
two inputs — or they are protected and this pipeline is not on a protected ref.
Check *Settings → CI/CD → Variables*: the "Protected" column is the one that
bites, and a protected variable is invisible rather than empty.

**2. `unauthorized: access to the requested resource is not authorized`,** after
the build has already run. Authentication worked and authorisation did not: the
robot has no write permission on that repository, or the repository does not
exist and the robot may not create it. Both are fixed in Quay, not in the
pipeline. `GET /api/v1/repository/platform/myapp/permissions/user/` lists who
holds what.

**3. `tls: failed to verify certificate: x509: certificate signed by unknown
authority`.** The job container does not trust the authority that issued the
registry certificate. Set `ca-bundle-variable`. If it is already set and the
error persists, the bundle is missing the root: include the whole chain, issuing
CA and root, not just the one that signed the leaf.

## Air-gapped sites

Every network call this pipeline makes is either a registry call you configured
or a tool download you can redirect:

| Job | Reaches | Offline path |
|---|---|---|
| build | the registry in `image-repository`; every registry in a `FROM`; anything the Dockerfile itself downloads | name your proxy cache or local registry in both the input and the Dockerfile |
| build | the BuildKit execution image | `execution-image` takes `<mirror>/moby/buildkit:v0.33.0` by tag as well as by digest |
| SAST | the Semgrep registry, if `semgrep-rules` names a pack | point it at a rules file in your own checkout |
| filesystem and image scan | trivy's vulnerability database, `ghcr.io` by default | `trivy-db-repository`, `trivy-java-db-repository` |
| SBOM | the registry holding the built image | already yours |
| sign | the pinned cosign binary from GitHub releases | `cosign-release-url`; the checksum stays pinned, so a mirror cannot change which bytes are accepted |
| sign | your Vault, for the signing key | already yours |
| secret detection, lockfile check | nothing | — |

Nothing else leaves the runner. There is no telemetry call, no update check, and
the CA bundle arrives as a variable rather than a download for exactly this
reason.
