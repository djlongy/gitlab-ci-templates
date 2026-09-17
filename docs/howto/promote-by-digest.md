# Promote an image by digest, on any registry

`container-promote-skopeo` copies one manifest from one repository to another
and then reads the target back to prove the digest is the same. It speaks the
OCI distribution API, so the two ends may be Harbor, Quay, Artifactory or
GitLab's own, in any combination.

Use `container-promote-harbor` instead when both ends are Harbor and the release
carries cosign signatures: that one copies the `.sig` and `.att` referrer tags,
and this one does not.

Throughout, the shared CI project is written `platform/gitlab-ci-templates` and
the registry `registry.example.com`. Substitute your own.

## Steps

1. **Create the target repository and grant the pushing account write on it.**
   A robot account may not create a repository on first push, on Quay or on
   Harbor; the copy then fails with `401 UNAUTHORIZED` on a blob it has every
   right to read.

2. **Set the credentials as CI variables** and decide whether the two ends use
   one identity or two. Mask the passwords. If the registry's certificate comes
   from an internal authority, add a file variable holding the PEM chain.

3. **Add the include.** The source digest comes from a build job in the same
   pipeline:

   ```yaml
   include:
     - project: 'platform/gitlab-ci-templates'
       ref: '1.3.0'
       file: '/templates/container-promote-skopeo/template.yml'
       inputs:
         instance: app
         source-repository: 'registry.example.com/platform/app-candidate'
         target-repository: 'registry.example.com/platform/app'
         source-digest-job: ['app:container-build-buildkit']
         target-tags: ['$CI_COMMIT_TAG']
         source-username-variable: QUAY_USER
         source-password-variable: QUAY_TOKEN
         target-username-variable: QUAY_USER
         target-password-variable: QUAY_TOKEN
         ca-bundle-variable: ESTATE_CA_BUNDLE
         gate-jobs:
           - job: 'app:security-image-trivy'
             artifacts: false
   ```

   `source-digest-job` is a list because GitLab has no conditional `needs`. It
   holds zero or one job name.

4. **Or state the digest, when the artifact was built by an earlier pipeline.**
   Replace `source-digest-job` with

   ```yaml
         source-digest: 'sha256:9573e3f886778700155d62106eaef77aae3ac5014213b13a5d949fc2584ae745'
   ```

   Setting neither fails the job. Setting a tag in `source-digest` fails it too:
   the promotion is of one immutable artifact, not of whatever a name points at
   when the manual button is pressed.

5. **Run it.** The default rules require a protected tag and a manual action.
   Pass `run-rules` from the composition to change that.

6. **Read the evidence.**
   `.ci-artifacts/<instance>/container-promote-skopeo/promote-result.json`
   records the source, the target, the digest and the tags. The job has already
   compared the digest it copied with the one it read back; the file is what
   survives the job.

## What the job fails on

Neither `source-digest` nor a readable `image.json`. An `image.json` that fails
its own schema check. A digest that is not `sha256:` and 64 hex characters. An
empty `target-tags`, or a tag outside `[A-Za-z0-9._-]`. Either credential pair
empty, with the variable names it read in the message. A CA bundle variable
carrying no certificate. A refused copy. A target that cannot be read back, or
whose digest differs from the source.
