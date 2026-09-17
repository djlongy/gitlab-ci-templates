<!-- GENERATED FILE — do not edit. Rebuild with:
         python3 runtime/catalog/generate.py --write
     Source of truth is templates/<name>/template.yml and
     templates/<name>/contract.yml. The drift check is in
     tests/contracts/test_bricks.py. -->

# Brick cards

One card per component: what it is for, what it must be told, what it reads from
another job, every file it is made of, the one command that tests it, and where
it talks to. A brick is picked out of this list and included on its own;
docs/howto/pick-your-bricks.md assembles three kits from it.

Each card is generated, so a field that is empty here is empty in the template
or the contract, not omitted.

## ansible-check

ansible-playbook --check for one playbook against a real inventory.

- Status: experimental
- Jobs: `{instance}:ansible-check` in `plan`
- Required inputs: `instance`, `playbook`, `inventory`
- Producer inputs: none
- Reads: none
- Files: `templates/ansible-check/template.yml`, `templates/ansible-check/contract.yml`
- Test: `python3 -m pytest tests/pipelines/test_ansible_components.py tests/runtime/ansible/test_ansible_job_shell.py`
- Egress: `registry.example.com`, `managed hosts on tcp/22 for the selected inventory`

## ansible-lint

ansible-lint over a directory of the consumer checkout.

- Status: experimental
- Jobs: `{instance}:ansible-lint` in `verify`
- Required inputs: `instance`
- Producer inputs: none
- Reads: none
- Files: `templates/ansible-lint/template.yml`, `templates/ansible-lint/contract.yml`, `runtime/scan/scan-gate.sh`
- Test: `python3 -m pytest tests/contracts/test_status_matches_evidence.py tests/pipelines/test_ansible_components.py tests/runtime/ansible/test_ansible_job_shell.py tests/runtime/embed/test_generate.py tests/runtime/scan/test_scan_gate.py`
- Egress: `registry.example.com`

## ansible-syntax

ansible-playbook --syntax-check for one playbook.

- Status: experimental
- Jobs: `{instance}:ansible-syntax` in `verify`
- Required inputs: `instance`, `playbook`
- Producer inputs: none
- Reads: none
- Files: `templates/ansible-syntax/template.yml`, `templates/ansible-syntax/contract.yml`
- Test: `python3 -m pytest tests/contracts/test_estate.py tests/pipelines/test_ansible_components.py tests/runtime/ansible/test_ansible_job_shell.py`
- Egress: `registry.example.com`

## container-build-buildkit

Build a Dockerfile with BuildKit and record the pushed image's identity.

- Status: released
- Jobs: `{instance}:container-build-buildkit` in `build`
- Required inputs: `instance`, `image-repository`
- Producer inputs: none
- Reads: none
- Files: `templates/container-build-buildkit/template.yml`, `templates/container-build-buildkit/contract.yml`, `runtime/builder/gate_inputs.sh`, `runtime/registry/ca-bundle.sh`, `runtime/registry/image-json.sh`
- Test: `python3 -m pytest tests/contracts/test_catalog.py tests/contracts/test_container_component_shape.py tests/contracts/test_execution_image_regex.py tests/contracts/test_resource_group.py tests/pipelines/test_compositions.py tests/pipelines/test_container_components.py tests/pipelines/test_security_image_components.py tests/runtime/builder/test_gate_inputs.py tests/runtime/embed/test_generate.py tests/runtime/registry/test_build_secrets.py tests/runtime/registry/test_ca_bundle.py tests/runtime/registry/test_image_json.py tests/runtime/test_subject.py`
- Egress: `the registry host in image-repository: manifest and blob push`, `every registry named in a FROM line of the consumer Dockerfile, and every host that Dockerfile fetches from while it builds`, `nothing else: the execution image carries buildctl, the trust anchor comes from a CI variable rather than a download, and the credentials come from the environment`

## container-build-jib

Build a Java service with Jib and record the pushed image's identity.

- Status: experimental
- Jobs: `{instance}:container-build-jib` in `build`
- Required inputs: `instance`, `image-repository`
- Producer inputs: none
- Reads: none
- Files: `templates/container-build-jib/template.yml`, `templates/container-build-jib/contract.yml`, `runtime/builder/gate_inputs.sh`, `runtime/registry/ca-bundle.sh`, `runtime/registry/image-json.sh`
- Test: `python3 -m pytest tests/contracts/test_container_component_shape.py tests/contracts/test_resource_group.py tests/pipelines/test_container_components.py tests/runtime/builder/test_gate_inputs.py tests/runtime/embed/test_generate.py tests/runtime/registry/test_ca_bundle.py tests/runtime/registry/test_image_json.py`
- Egress: `the consumer project's Gradle plugin and dependency repositories`, `gcr.io: default base image`, `registry.example.com: candidate registry push`

## container-build-ko

Build a Go service with ko and record the pushed image's identity.

- Status: experimental
- Jobs: `{instance}:container-build-ko` in `build`
- Required inputs: `instance`, `image-repository`
- Producer inputs: none
- Reads: none
- Files: `templates/container-build-ko/template.yml`, `templates/container-build-ko/contract.yml`, `runtime/builder/gate_inputs.sh`, `runtime/registry/ca-bundle.sh`, `runtime/registry/image-json.sh`
- Test: `python3 -m pytest tests/contracts/test_container_component_shape.py tests/contracts/test_resource_group.py tests/pipelines/test_container_components.py tests/runtime/builder/test_gate_inputs.py tests/runtime/embed/test_generate.py tests/runtime/registry/test_ca_bundle.py tests/runtime/registry/test_image_json.py`
- Egress: `proxy.golang.org and sum.golang.org: pinned ko install and module verification`, `cgr.dev: default base image`, `registry.example.com: candidate registry push`

## container-export-skopeo

Export a mirrored reference set to S3 as an OCI archive for an air-gapped site.

- Status: released
- Jobs: `{instance}:container-export-skopeo` in `publish`
- Required inputs: `instance`, `list-job`, `list-files`, `repository-prefix`, `s3-endpoint`, `s3-bucket`, `s3-prefix`, `have-key`
- Producer inputs: none
- Reads: none
- Files: `templates/container-export-skopeo/template.yml`, `templates/container-export-skopeo/contract.yml`, `runtime/mirror/egress.sh`, `runtime/mirror/registry.sh`, `runtime/mirror/transfer_manifest.py`, `runtime/publish/gate.sh`
- Test: `python3 -m pytest tests/pipelines/test_container_mirror.py tests/pipelines/test_security_image_components.py tests/runtime/embed/test_generate.py tests/runtime/mirror/test_registry.py tests/runtime/mirror/test_transfer_manifest.py tests/runtime/publish/test_gate.py`
- Egress: `the source registry named by the registry input`, `the s3-endpoint host`, `the vault-addr host, in Vault mode`, `the ca-bundle-url host, when set`

## container-list-rke2

Resolve an RKE2 release manifest into the image reference list the mirror consumes.

- Status: released
- Jobs: `{instance}:container-list-rke2` in `verify`
- Required inputs: `instance`
- Producer inputs: none
- Reads: none
- Files: `templates/container-list-rke2/template.yml`, `templates/container-list-rke2/contract.yml`, `runtime/mirror/egress.sh`, `runtime/mirror/release_list.py`
- Test: `python3 -m pytest tests/pipelines/test_container_mirror.py tests/runtime/embed/test_generate.py tests/runtime/mirror/test_release_list.py tests/tools/test_ci_local.py`
- Egress: `github.com`

## container-mirror-skopeo

Copy a reference list from one registry to another with skopeo, by digest.

- Status: released
- Jobs: `{instance}:container-mirror-skopeo` in `publish`
- Required inputs: `instance`, `list-job`, `list-files`, `repository-prefix`
- Producer inputs: `list-job` (artifacts, required)
- Reads: none
- Files: `templates/container-mirror-skopeo/template.yml`, `templates/container-mirror-skopeo/contract.yml`, `runtime/mirror/egress.sh`, `runtime/mirror/registry.sh`
- Test: `python3 -m pytest tests/pipelines/test_container_mirror.py tests/runtime/embed/test_generate.py tests/runtime/mirror/test_registry.py tests/runtime/publish/test_gate.py`
- Egress: `every source registry named in the list files`, `the destination registry named by the registry input`, `the vault-addr host, in Vault mode`, `the ca-bundle-url host, when set`

## container-promote-harbor

Promote a tested, signed image from the candidate Harbor project to the release project, by digest.

- Status: experimental
- Jobs: `{instance}:container-promote-harbor` in `publish`
- Required inputs: `instance`
- Producer inputs: none
- Reads: `.ci-artifacts/{instance}/{build-component}/image.json`
- Files: `templates/container-promote-harbor/template.yml`, `templates/container-promote-harbor/contract.yml`, `runtime/httpjson.py`, `runtime/promote/harbor.py`, `runtime/publish/gate.sh`, `runtime/subject.py`
- Test: `python3 -m pytest tests/contracts/test_git_strategy.py tests/contracts/test_runner_images.py tests/pipelines/test_compositions.py tests/pipelines/test_security_image_components.py tests/runtime/embed/test_generate.py tests/runtime/promote/test_harbor.py tests/runtime/publish/test_gate.py tests/runtime/test_subject.py`
- Egress: `registry.example.com`

## container-promote-skopeo

Promote one digest from one OCI repository to another, on any registry.

- Status: released
- Jobs: `{instance}:container-promote-skopeo` in `publish`
- Required inputs: `instance`, `source-repository`, `target-repository`
- Producer inputs: `gate-jobs` (gate list, default `[]`), `source-digest-job` (gate list, default `[]`)
- Reads: `.ci-artifacts/{instance}/{source-digest-component}/image.json`
- Files: `templates/container-promote-skopeo/template.yml`, `templates/container-promote-skopeo/contract.yml`, `runtime/promote/skopeo.sh`, `runtime/registry/ca-bundle.sh`, `runtime/registry/image-json.sh`
- Test: `python3 -m pytest tests/contracts/test_git_strategy.py tests/runtime/embed/test_generate.py tests/runtime/promote/test_skopeo.py tests/runtime/registry/test_ca_bundle.py tests/runtime/registry/test_image_json.py`
- Egress: `the source registry`, `the target registry`, `the Alpine package mirror the execution image is configured with`

## container-sign-attest-cosign

Sign one built image with cosign and attach the evidence the release requires.

- Status: released
- Jobs: `{instance}:container-sign-attest-cosign` in `attest`
- Required inputs: `instance`
- Producer inputs: none
- Reads: `.ci-artifacts/{instance}/security-sbom-syft`, `.ci-artifacts/{instance}/{build-component}/image.json`, `.ci-artifacts/{instance}/{scan-component}`
- Files: `templates/container-sign-attest-cosign/template.yml`, `templates/container-sign-attest-cosign/contract.yml`, `runtime/httpjson.py`, `runtime/registry/ca-bundle.sh`, `runtime/sign/cosign_attest.py`, `runtime/sign/cosign_checksums.txt`, `runtime/subject.py`
- Test: `python3 -m pytest tests/pipelines/test_compositions.py tests/pipelines/test_security_image_components.py tests/runtime/embed/test_generate.py tests/runtime/registry/test_ca_bundle.py tests/runtime/sign/test_cosign_attest.py tests/runtime/test_subject.py`
- Egress: `registry.example.com`, `vault.example.com`, `github.com (the pinned cosign release asset)`

## container-smoke-test

Run a Compose-based smoke test against exact built image identities.

- Status: experimental
- Jobs: `{instance}:container-smoke-test` in `test`
- Required inputs: `instance`, `image-identities`
- Producer inputs: none
- Reads: none
- Files: `templates/container-smoke-test/template.yml`, `templates/container-smoke-test/contract.yml`, `runtime/registry/ca-bundle.sh`, `runtime/registry/image-json.sh`
- Test: `python3 -m pytest tests/contracts/test_container_component_shape.py tests/pipelines/test_composition_catalog.py tests/pipelines/test_compositions.py tests/pipelines/test_container_components.py tests/runtime/embed/test_generate.py tests/runtime/registry/test_ca_bundle.py tests/runtime/registry/test_image_json.py`
- Egress: `registry.example.com: pull the images under test`

## docs-wiki-sync

Two-way mirror between a repository's documentation folder and that project's GitLab wiki. The repository is the source of truth: a wiki edit becomes a repository commit authored by whoever made it, then the folder is regenerated into the wiki as the CI author.

- Status: released
- Jobs: `{instance}:docs-wiki-sync` in `verify`
- Required inputs: `instance`
- Producer inputs: none
- Reads: none
- Files: `templates/docs-wiki-sync/template.yml`, `templates/docs-wiki-sync/contract.yml`, `runtime/httpjson.py`, `runtime/wiki/docfilter.py`, `runtime/wiki/reconcile.py`, `runtime/wiki/requirements.txt`, `runtime/wiki/wiki-deploy.sh`, `runtime/wiki/wiki-import.py`, `runtime/wiki/wiki-pull.py`, `runtime/wiki/wiki-sync.py`
- Test: `python3 -m pytest tests/contracts/test_build_directory_ownership.py tests/contracts/test_execution_image_regex.py tests/contracts/test_status_matches_evidence.py tests/pipelines/test_docs_audit_trigger_components.py tests/runtime/embed/test_generate.py tests/runtime/embed/test_marker_completeness.py tests/runtime/wiki/test_reconcile.py tests/runtime/wiki/test_wiki_pull.py tests/tools/test_ci_local.py`
- Egress: `gitlab.example.com`, `registry.example.com, only when executor is docker`, `the configured Python package index, only when python3 cannot already import yaml. PIP_INDEX_URL and PIP_TRUSTED_HOST are read from the job environment; with neither set and no OS package present the job fails with a message naming both options rather than reaching pypi.org.`

## helm-package-publish

Package a Helm chart and publish it to the chart repository.

- Status: experimental
- Jobs: `{instance}:helm-package-publish` in `publish`
- Required inputs: `instance`, `chart-path`, `chart-repository`
- Producer inputs: none
- Reads: none
- Files: `templates/helm-package-publish/template.yml`, `templates/helm-package-publish/contract.yml`, `runtime/helm/package_publish.sh`, `runtime/kubernetes/toolchain.sh`
- Test: `python3 -m pytest tests/pipelines/test_helm_kubernetes_components.py tests/runtime/embed/test_generate.py tests/runtime/helm/test_helm_package_publish.py tests/runtime/kubernetes/test_toolchain.py`
- Egress: `registry.example.com`

## helm-validate

Lint and template a Helm chart against the consumer checkout.

- Status: experimental
- Jobs: `{instance}:helm-validate` in `verify`
- Required inputs: `instance`, `chart-paths`
- Producer inputs: none
- Reads: none
- Files: `templates/helm-validate/template.yml`, `templates/helm-validate/contract.yml`, `runtime/helm/validate.sh`, `runtime/kubernetes/toolchain.sh`
- Test: `python3 -m pytest tests/pipelines/test_helm_kubernetes_components.py tests/runtime/embed/test_generate.py tests/runtime/helm/test_helm_validate.py tests/runtime/kubernetes/test_toolchain.py`
- Egress: `registry.example.com`, `github.com`, `objects.githubusercontent.com`, `raw.githubusercontent.com`

## kubernetes-validate

Validate Kubernetes manifests against the cluster API schemas.

- Status: released
- Jobs: `{instance}:kubernetes-validate` in `verify`
- Required inputs: `instance`, `manifest-paths`
- Producer inputs: none
- Reads: none
- Files: `templates/kubernetes-validate/template.yml`, `templates/kubernetes-validate/contract.yml`, `runtime/kubernetes/toolchain.sh`, `runtime/kubernetes/validate.sh`
- Test: `python3 -m pytest tests/pipelines/test_helm_kubernetes_components.py tests/runtime/embed/test_generate.py tests/runtime/kubernetes/test_kubernetes_validate.py tests/runtime/kubernetes/test_toolchain.py`
- Egress: `registry.example.com`, `github.com`, `objects.githubusercontent.com`, `raw.githubusercontent.com`

## quality-dependency-lockfiles

Prove every declared lock file exists and pins what it resolves.

- Status: released
- Jobs: `{instance}:quality-dependency-lockfiles` in `verify`
- Required inputs: `instance`, `lockfiles`
- Producer inputs: none
- Reads: none
- Files: `templates/quality-dependency-lockfiles/template.yml`, `templates/quality-dependency-lockfiles/contract.yml`, `runtime/scan/lockfiles.sh`
- Test: `python3 -m pytest tests/pipelines/test_compositions.py tests/pipelines/test_security_source_components.py tests/runtime/embed/test_generate.py tests/runtime/scan/test_lockfiles.py tests/tools/test_ci_local.py`
- Egress: `registry.example.com`

## quality-sonarqube

Analyse the checkout with SonarQube and wait for its quality gate.

- Status: released
- Jobs: `{instance}:quality-sonarqube` in `verify`
- Required inputs: `instance`, `sarif-report-jobs`
- Producer inputs: none
- Reads: none
- Files: `templates/quality-sonarqube/template.yml`, `templates/quality-sonarqube/contract.yml`, `runtime/scan/sonar_gate.py`
- Test: `python3 -m pytest tests/contracts/test_build_directory_ownership.py tests/contracts/test_inherit_default.py tests/pipelines/test_security_source_components.py tests/runtime/embed/test_generate.py tests/runtime/scan/test_sonar_gate.py`
- Egress: `registry.example.com`, `sonarqube.example.com`

## release-trigger-jenkins

Start a Jenkins job from a GitLab pipeline, authenticating through the GitLab ID token -> Vault JWT login -> Jenkins API token chain. Replaces the hidden job `.jenkins_trigger_base` in promote/jenkins-trigger.yml.

- Status: experimental
- Jobs: `{instance}:release-trigger-jenkins` in `deploy`
- Required inputs: `instance`, `job-path`
- Producer inputs: none
- Reads: none
- Files: `templates/release-trigger-jenkins/template.yml`, `templates/release-trigger-jenkins/contract.yml`
- Test: `python3 -m pytest tests/contracts/test_git_strategy.py tests/pipelines/test_docs_audit_trigger_components.py`
- Egress: `vault.example.com`, `jenkins.example.com`, `registry.example.com`, `the Alpine package mirror, for curl and jq`

## release-trigger-semaphore

Start a Semaphore task template from a GitLab pipeline, authenticating through the GitLab ID token -> Vault JWT login -> Semaphore API token chain. Replaces the hidden job `.semaphore_trigger_base` in promote/semaphore-trigger.yml.

- Status: experimental
- Jobs: `{instance}:release-trigger-semaphore` in `deploy`
- Required inputs: `instance`, `project-name`, `template-name`
- Producer inputs: none
- Reads: none
- Files: `templates/release-trigger-semaphore/template.yml`, `templates/release-trigger-semaphore/contract.yml`
- Test: `python3 -m pytest tests/contracts/test_git_strategy.py tests/pipelines/test_docs_audit_trigger_components.py`
- Egress: `vault.example.com`, `semaphore.example.com`, `registry.example.com`, `the Alpine package mirror, for curl and jq`

## security-filesystem-grype

Grype vulnerability scan of the consumer checkout.

- Status: experimental
- Jobs: `{instance}:security-filesystem-grype` in `verify`
- Required inputs: `instance`
- Producer inputs: none
- Reads: none
- Files: `templates/security-filesystem-grype/template.yml`, `templates/security-filesystem-grype/contract.yml`, `runtime/scan/scan-gate.sh`
- Test: `python3 -m pytest tests/pipelines/test_security_source_components.py tests/runtime/embed/test_generate.py tests/runtime/scan/test_grype_offline_database.py tests/runtime/scan/test_scan_gate.py tests/runtime/scan/test_version_probes_survive_no_match.py`
- Egress: `registry.example.com`, `grype.anchore.io for the vulnerability database`

## security-filesystem-trivy

Trivy vulnerability, secret and misconfiguration scan of the consumer checkout.

- Status: released
- Jobs: `{instance}:security-filesystem-trivy` in `verify`
- Required inputs: `instance`
- Producer inputs: none
- Reads: none
- Files: `templates/security-filesystem-trivy/template.yml`, `templates/security-filesystem-trivy/contract.yml`, `runtime/scan/scan-gate.sh`
- Test: `python3 -m pytest tests/pipelines/test_compositions.py tests/pipelines/test_security_source_components.py tests/runtime/embed/test_generate.py tests/runtime/scan/test_scan_gate.py tests/runtime/scan/test_version_probes_survive_no_match.py`
- Egress: `registry.example.com`, `the trivy vulnerability database and check bundle sources`

## security-image-grype

Grype vulnerability scan of one built image, from its SBOM or from the image.

- Status: experimental
- Jobs: `{instance}:security-image-grype` in `scan`
- Required inputs: `instance`
- Producer inputs: none
- Reads: `.ci-artifacts/{instance}/security-sbom-syft`, `.ci-artifacts/{instance}/{build-component}/image.json`
- Files: `templates/security-image-grype/template.yml`, `templates/security-image-grype/contract.yml`, `runtime/registry/ca-bundle.sh`, `runtime/registry/image-json.sh`, `runtime/scan/scan.sh`
- Test: `python3 -m pytest tests/pipelines/test_security_image_components.py tests/pipelines/test_security_source_components.py tests/runtime/embed/test_generate.py tests/runtime/registry/test_ca_bundle.py tests/runtime/registry/test_image_json.py tests/runtime/scan/test_grype_offline_database.py tests/runtime/scan/test_scan.py tests/runtime/scan/test_version_probes_survive_no_match.py`
- Egress: `grype.anchore.io`

## security-image-trivy

Trivy vulnerability scan of one built image, by digest.

- Status: released
- Jobs: `{instance}:security-image-trivy` in `scan`
- Required inputs: `instance`
- Producer inputs: none
- Reads: `.ci-artifacts/{instance}/{build-component}/image.json`
- Files: `templates/security-image-trivy/template.yml`, `templates/security-image-trivy/contract.yml`, `runtime/registry/ca-bundle.sh`, `runtime/registry/image-json.sh`, `runtime/scan/scan.sh`
- Test: `python3 -m pytest tests/pipelines/test_compositions.py tests/pipelines/test_security_image_components.py tests/runtime/builder/test_gate_inputs.py tests/runtime/embed/test_generate.py tests/runtime/publish/test_gate.py tests/runtime/registry/test_ca_bundle.py tests/runtime/registry/test_image_json.py tests/runtime/scan/test_scan.py tests/runtime/sign/test_cosign_attest.py`
- Egress: `registry.example.com`, `Trivy vulnerability database source (ghcr.io by default)`

## security-repository-audit

Scan a named set of OTHER repositories: clone each one, look for leaked secrets, and optionally run the Terraform static and state checks over them. Replaces security/group-scan.yml and the three jobs in security/terraform-audit.yml.

- Status: experimental
- Jobs: `{instance}:security-repository-audit` in `verify`
- Required inputs: `instance`
- Producer inputs: none
- Reads: none
- Files: `templates/security-repository-audit/template.yml`, `templates/security-repository-audit/contract.yml`, `runtime/audit/audit-secrets.sh`, `runtime/audit/clone-repositories.sh`, `runtime/audit/gitleaks-scan.sh`, `runtime/audit/scan-state.sh`
- Test: `python3 -m pytest tests/contracts/test_git_strategy.py tests/pipelines/test_docs_audit_trigger_components.py tests/runtime/audit/test_clone_repositories.py tests/runtime/audit/test_gitleaks_scan.py tests/runtime/embed/test_generate.py`
- Egress: `gitlab.example.com`, `registry.example.com`, `the Alpine package mirror, for bash, curl, jq and git`

## security-sast-semgrep

Semgrep static analysis of the consumer checkout.

- Status: released
- Jobs: `{instance}:security-sast-semgrep` in `verify`
- Required inputs: `instance`, `rules-ref`
- Producer inputs: none
- Reads: none
- Files: `templates/security-sast-semgrep/template.yml`, `templates/security-sast-semgrep/contract.yml`, `runtime/scan/scan-gate.sh`
- Test: `python3 -m pytest tests/pipelines/test_compositions.py tests/pipelines/test_security_source_components.py tests/runtime/embed/test_generate.py tests/runtime/scan/test_scan_gate.py`
- Egress: `registry.example.com`, `the host serving the rules-ref, when it is a URL rather than a path in the checkout`

## security-sbom-syft

Authoritative CycloneDX SBOM for one built image.

- Status: released
- Jobs: `{instance}:security-sbom-syft` in `scan`
- Required inputs: `instance`
- Producer inputs: none
- Reads: `.ci-artifacts/{instance}/{build-component}/image.json`
- Files: `templates/security-sbom-syft/template.yml`, `templates/security-sbom-syft/contract.yml`, `runtime/registry/ca-bundle.sh`, `runtime/registry/image-json.sh`, `runtime/scan/scan.sh`
- Test: `python3 -m pytest tests/pipelines/test_compositions.py tests/pipelines/test_security_image_components.py tests/runtime/embed/test_generate.py tests/runtime/registry/test_ca_bundle.py tests/runtime/registry/test_image_json.py tests/runtime/scan/test_scan.py tests/runtime/scan/test_unreadable_listing_is_not_zero.py tests/runtime/sign/test_cosign_attest.py`
- Egress: `registry.example.com`

## security-sbom-upload-dtrack

Upload the authoritative SBOM to Dependency-Track and wait for its analysis.

- Status: experimental
- Jobs: `{instance}:security-sbom-upload-dtrack` in `scan`
- Required inputs: `instance`, `sbom-job`, `api-url`
- Producer inputs: `gate-jobs` (gate list, default `[]`), `sbom-job` (artifacts, required)
- Reads: `.ci-artifacts/{instance}/security-sbom-syft`
- Files: `templates/security-sbom-upload-dtrack/template.yml`, `templates/security-sbom-upload-dtrack/contract.yml`, `runtime/dtrack/dtrack.py`, `runtime/httpjson.py`
- Test: `python3 -m pytest tests/contracts/test_git_strategy.py tests/runtime/dtrack/test_dtrack.py tests/runtime/embed/test_generate.py`
- Egress: `The Dependency-Track instance named by the api-url input`

## security-secrets-gitleaks

Gitleaks secret detection over the checkout and its commit history.

- Status: released
- Jobs: `{instance}:security-secrets-gitleaks` in `verify`
- Required inputs: `instance`
- Producer inputs: none
- Reads: none
- Files: `templates/security-secrets-gitleaks/template.yml`, `templates/security-secrets-gitleaks/contract.yml`, `runtime/scan/scan-gate.sh`
- Test: `python3 -m pytest tests/pipelines/test_compositions.py tests/pipelines/test_container_components.py tests/pipelines/test_security_source_components.py tests/runtime/embed/test_generate.py tests/runtime/scan/test_gitleaks_job_shell.py tests/runtime/scan/test_scan_gate.py`
- Egress: `registry.example.com`

## security-sync-vigil

Ask Vigil to ingest a freshly signed image, and fail if it will not.

- Status: experimental
- Jobs: `{instance}:security-sync-vigil` in `attest`
- Required inputs: `instance`, `vigil-url`
- Producer inputs: none
- Reads: `.ci-artifacts/{instance}/{build-component}/image.json`
- Files: `templates/security-sync-vigil/template.yml`, `templates/security-sync-vigil/contract.yml`, `runtime/httpjson.py`, `runtime/subject.py`, `runtime/vigil/vigil.py`
- Test: `python3 -m pytest tests/contracts/test_git_strategy.py tests/pipelines/test_security_image_components.py tests/runtime/embed/test_generate.py tests/runtime/test_subject.py tests/runtime/vigil/test_vigil.py`
- Egress: `The Vigil instance named by the vigil-url input`

## security-verify-vigil

Evaluate Vigil's supply-chain readiness verdict for one built image.

- Status: experimental
- Jobs: `{instance}:security-verify-vigil` in `attest`
- Required inputs: `instance`, `sync-job`, `vigil-url`
- Producer inputs: none
- Reads: `.ci-artifacts/{instance}/{build-component}/image.json`
- Files: `templates/security-verify-vigil/template.yml`, `templates/security-verify-vigil/contract.yml`, `runtime/httpjson.py`, `runtime/subject.py`, `runtime/vigil/vigil.py`
- Test: `python3 -m pytest tests/contracts/test_git_strategy.py tests/pipelines/test_compositions.py tests/pipelines/test_security_image_components.py tests/runtime/embed/test_generate.py tests/runtime/test_subject.py tests/runtime/vigil/test_vigil.py`
- Egress: `The Vigil instance named by the vigil-url input`

## terraform-apply

Applies the exact saved plan produced by terraform-plan, and nothing else. It is the other half of the split of terraform/plan-apply.yml, whose apply job took its plan from GitLab's implicit "download every earlier artifact", named no producer, checked no evidence and re-ran `terraform init` through a shared parent that could just as easily have re-planned.

- Status: experimental
- Jobs: `{instance}:terraform-apply` in `deploy`
- Required inputs: `instance`, `plan-job`, `environment`, `state-id`
- Producer inputs: `gate-jobs` (gate list, default `[]`), `plan-job` (artifacts, required)
- Reads: none
- Files: `templates/terraform-apply/template.yml`, `templates/terraform-apply/contract.yml`, `runtime/terraform/tfguard.sh`
- Test: `python3 -m pytest tests/contracts/test_resource_group.py tests/contracts/test_status_matches_evidence.py tests/contracts/test_terraform_components.py tests/pipelines/test_compositions.py tests/pipelines/test_terraform_lint.py tests/runtime/embed/test_generate.py tests/runtime/terraform/test_tfguard.py`
- Egress: `registry.example.com`, `gitlab.example.com`, `registry.terraform.io`, `vault.example.com`

## terraform-fmt

Checks Terraform source formatting. It replaces terraform/lint.yml, which was named "lint" while running `terraform fmt -check -recursive`; section 5.1 requires the name to describe the actual capability.

- Status: released
- Jobs: `{instance}:terraform-fmt` in `verify`
- Required inputs: `instance`
- Producer inputs: none
- Reads: none
- Files: `templates/terraform-fmt/template.yml`, `templates/terraform-fmt/contract.yml`
- Test: `python3 -m pytest tests/contracts/test_catalog.py tests/contracts/test_terraform_components.py tests/pipelines/test_compositions.py tests/pipelines/test_terraform_lint.py`
- Egress: `registry.example.com`

## terraform-module-publish

Publishes one Terraform module to the GitLab Terraform Module Registry on a protected release tag. It replaces terraform/module-publish.yml, which packed everything under MODULE_DIR — `.terraform/`, state files, tfvars and any saved plan went into the archive — hard-required a `v` prefix, and uploaded without ever asking whether that version already existed.

- Status: experimental
- Jobs: `{instance}:terraform-module-publish` in `publish`
- Required inputs: `instance`, `module-name`, `module-system`
- Producer inputs: none
- Reads: none
- Files: `templates/terraform-module-publish/template.yml`, `templates/terraform-module-publish/contract.yml`, `runtime/terraform/tfguard.sh`
- Test: `python3 -m pytest tests/contracts/test_terraform_components.py tests/pipelines/test_compositions.py tests/pipelines/test_terraform_lint.py tests/runtime/embed/test_generate.py tests/runtime/terraform/test_tfguard.py`
- Egress: `registry.example.com`, `gitlab.example.com`

## terraform-plan

Produces the reviewable plan for one Terraform root, state and environment, plus the evidence record terraform-apply validates before it mutates anything (section 9.3). It is one half of the split of terraform/plan-apply.yml; the other half is templates/terraform-apply.

- Status: released
- Jobs: `{instance}:terraform-plan` in `plan`
- Required inputs: `instance`, `environment`, `state-id`
- Producer inputs: none
- Reads: none
- Files: `templates/terraform-plan/template.yml`, `templates/terraform-plan/contract.yml`, `runtime/terraform/tfguard.sh`
- Test: `python3 -m pytest tests/contracts/test_resource_group.py tests/contracts/test_terraform_components.py tests/pipelines/test_compositions.py tests/pipelines/test_terraform_lint.py tests/runtime/embed/test_generate.py tests/runtime/terraform/test_tfguard.py`
- Egress: `registry.example.com`, `gitlab.example.com`, `registry.terraform.io`, `vault.example.com`

## terraform-validate

Validates an initialised Terraform configuration without backend access and without production credentials. It replaces terraform/validate.yml, whose undeclared DEPLOY_DIR silently fell back to the checkout root and whose `terraform init -backend=false` was free to rewrite the provider lockfile.

- Status: released
- Jobs: `{instance}:terraform-validate` in `verify`
- Required inputs: `instance`
- Producer inputs: none
- Reads: none
- Files: `templates/terraform-validate/template.yml`, `templates/terraform-validate/contract.yml`
- Test: `python3 -m pytest tests/contracts/test_terraform_components.py tests/pipelines/test_compositions.py tests/pipelines/test_terraform_lint.py tests/runtime/terraform/test_terraform_validate_job_shell.py`
- Egress: `registry.example.com`, `registry.terraform.io`, `gitlab.example.com`
