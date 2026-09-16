# GitLab CI repository and agent implementation standard

**Document version:** 1.0.5  
**Prepared:** 2026/09/15  
**Applies to:** shared GitLab CI configuration for applications, containers, Terraform, Ansible, Kubernetes, security, releases and infrastructure deployment.  
**Reference repository:** [djlongy/gitlab-ci-templates](https://github.com/djlongy/gitlab-ci-templates), reviewed at commit `87b4138c0ca12ac904d33c2383a6d885e678d324`.

This is the target architecture and operating guide for agents. It is not a statement that the existing repository already implements these contracts. A component name in this document is a reserved target name until the repository catalogue marks it released. The document does not install an agent skill, modify GitLab settings, or authorise a deployment.

The objective is predictable implementation: agents select a known composition, supply project-specific inputs, connect declared artifacts, and run established checks. They must not redesign the layout or rediscover the same interfaces for every project.

### Navigation

| Start here | Implementation | Operations |
| --- | --- | --- |
| [Agent quick start](#1-agent-quick-start) | [Naming](#5-naming-rules) | [Workload recipes](#11-workload-wiring-recipes) |
| [Architecture decisions](#2-fixed-architecture-decisions) | [Contracts](#6-component-and-composition-contracts) | [Runtime packaging](#12-runtime-packaging-and-reproducibility) |
| [Repository layout](#3-canonical-repository-layout) | [Stages and sources](#7-stages-and-pipeline-sources) | [Reference YAML](#13-reference-yaml-consistent-component-to-composition-wiring) |
| [Estate profile](#4-estate-profile-establish-once-reuse-consistently) | [Dependencies](#8-dependency-graph-rules) | [Verification and releases](#14-verification-and-release-gates) |
| [Agent task recipes](#16-agent-task-recipes) | [Artifacts](#9-artifact-contracts) | [Migration map](#15-migration-map-for-the-reviewed-repository) |
| [Agent adoption](#18-how-to-make-agents-use-this-document) | [Security](#10-security-identity-and-failure-semantics) | [Definition of done](#17-definition-of-done) |

## 1. Agent quick start

When asked to create or modify a pipeline:

1. Read the repository's applicable agent instructions and this standard. Follow the active user's scope and the environment's authority and access controls.
2. Read `.ci/estate.yml` for GitLab capabilities, trusted runners, approved execution images and deployment settings. If absent, produce a draft and identify only the missing values needed for the task; do not guess endpoints, roles or permissions.
3. Read `.ci/catalog.yml` from the selected shared-CI revision. Select a released composition or component. Do not infer availability from this document's target inventory.
4. Read the consuming project's `.ci/project.yml`, if present, and current pipeline. Preserve intentional project behaviour and unrelated work.
5. Prefer a supported composition. Use individual components when the workload requires a composition that does not yet exist.
6. Apply the naming, input and artifact contracts below. Derive job names mechanically; never guess upstream names or artifact paths.
7. Validate using the repository's established checks. For a new interface, changed graph, changed security gate or deployment operation, exercise the affected failure paths as well.
8. Report what changed, the revision used, what was verified, and any remaining environment prerequisite. Do not describe a linted pipeline as executed.

**Routine reuse:** use the pinned catalogue and validated estate profile; do not reopen architecture decisions or research every tool. Verify current upstream documentation when introducing a keyword, module, tool version, integration or unsupported capability, when a profile is stale, or when instructions require it. Pipeline compilation and focused validation still happen for each change.

**Never silently:** invent a component; remove a mandatory gate; change the Terraform engine; switch deployment ownership; substitute a tag for a digest; run a production apply to test YAML; or claim organisation-wide enforcement from an ordinary include.

## 2. Fixed architecture decisions

| Decision | Standard |
| --- | --- |
| Public atomic capabilities | GitLab Components in `templates/<component-name>/template.yml` |
| Complete pipelines | Typed, versioned `include:project` entry points in `pipelines/<workload>.yml` |
| Composition publication | Keep compositions in `pipelines/`; do not also publish `pipeline-*` components in this architecture version |
| Public component behaviour | Including a capability normally creates its executable job(s); hidden jobs are private implementation details |
| Reuse inside one shared repository | Compositions include local component YAML at their own revision and explicitly forward inputs |
| Global pipeline ownership | Exactly one root composition owns `workflow:` and `stages:` in each pipeline |
| Security enforcement | Linked security policy project or another explicitly documented external control; ordinary includes remain editable configuration |
| Tool choice | Tool-specific components; each composition selects documented defaults |
| Release unit | Version the shared repository together; extract only for independently justified ownership or release cadence |
| Consumer configuration | Small include plus declared inputs; no repeated central shell logic |
| Multi-workload projects | One root composition with distinct component instances, or child pipelines with separate global configuration |

GitLab supports the component directory form above and project includes with inputs. A local include nested in included configuration resolves in that configuration's repository context. This is why a composition can stay aligned with its component files without embedding a second version number. See [component structure](https://docs.gitlab.com/ci/components/#directory-structure) and [include behaviour](https://docs.gitlab.com/ci/yaml/includes/).

## 3. Canonical repository layout

Use the following exact responsibilities. Create directories when they have real contents; do not scaffold dozens of empty capabilities.

| Path | Contents and ownership |
| --- | --- |
| `README.md` | Usage first: supported entry points, prerequisites, one minimal example, links to catalogue and standard |
| `CHANGELOG.md` | Release changes, migration instructions and deprecation deadlines |
| `LICENSE.md` | Approved repository licence |
| `AGENTS.md` | Short pointer to this standard and verification commands; preserve other applicable instructions |
| `.gitlab-ci.yml` | This repository's lint, contract tests, integration checks and release jobs |
| `.ci/estate.yml` | Non-secret estate configuration and verified capability profile |
| `.ci/catalog.yml` | Generated inventory of actual components, inputs, outputs, prerequisites and validation status |
| `.ci/compatibility.yml` | Tested GitLab/Runner/tool combinations and supported report modes |
| `.ci/exceptions.yml` | Reviewed exceptions, scope, owner, expiry and compensating controls; records, not automatic enforcement |
| `templates/<name>/template.yml` | Public component interface and job configuration |
| `templates/<name>/contract.yml` | Machine-readable output, runtime and security contract supplementing `spec:inputs` |
| `pipelines/<workload>.yml` | Public complete compositions with typed inputs |
| `runtime/<domain>/` | Shell/Python helpers executed by jobs; packaged into execution images |
| `images/<runtime>/` | Dockerfiles and dependency locks for CI execution images |
| `tests/contracts/` | Catalogue consistency, names, schema and compatibility checks |
| `tests/pipelines/` | Pipeline compilation and expected job-graph fixtures |
| `tests/integration/` | Real build, scan, sign and deployment integration fixtures |
| `tests/runtime/` | Unit and behaviour tests for runtime helpers, including wiki tools |
| `examples/<workload>/.gitlab-ci.yml` | Complete consumer examples tested against the current shared-CI revision |
| `docs/gitlab-ci-agent-standard.md` | This standard when adopted into the repository |
| `docs/integrations/` | Vault, registry, runner, SonarQube and Dependency-Track setup |
| `docs/migrations/` | Compatibility and major-version migration guides |
| `renovate.json` | Dependency update rules, including custom managers where required |

Do not introduce `common/`, `base/`, `shared/`, `generic/` or a catch-all `jobs/` directory. A domain-specific internal helper is acceptable when its responsibility is explicit, such as `runtime/registry/auth.py`.

The following are metadata conventions defined by this standard, **not GitLab-native configuration files**: `.ci/estate.yml`, `.ci/project.yml`, `.ci/catalog.yml`, `.ci/compatibility.yml`, `.ci/exceptions.yml` and `contract.yml`. GitLab will not automatically read them. Repository tooling must validate them; an agent uses them to write explicit GitLab YAML. Do not assume they magically inject inputs or secrets.

## 4. Estate profile: establish once, reuse consistently

Record non-secret facts in `.ci/estate.yml`. Record secret variable names and identity references, never secret values.

| Field | Required meaning |
| --- | --- |
| `schema_version` | Profile schema version, initially `1` |
| `gitlab.host` | Actual GitLab FQDN, including port if relevant |
| `gitlab.version` / `gitlab.tier` | Verified server version and licence tier |
| `gitlab.verified_at` | Last capability verification date in `yyyy/mm/dd` |
| `gitlab.capabilities` | Explicit flags for components, inputs, native SARIF, policy enforcement and deployment approvals |
| `shared_ci.project` | Actual GitLab project path; GitHub source URL is not a usable GitLab component host |
| `shared_ci.approved_ref` | Full commit SHA or protected immutable release tag |
| `runners.<profile>` | Tags, executor, architecture, trust level, network access and privilege requirements |
| `images.<runtime>` | Approved image reference by digest, tool versions and runtime-contract version |
| `registries.candidate` / `registries.release` | Registry endpoints and projects for candidate and approved artifacts |
| `auth.<service>` | OIDC audience, role and auth mount or supported CI secret variable names |
| `terraform.engine` | `terraform` or `opentofu`; do not switch implicitly |
| `terraform.backend` | Backend type, state naming and locking prerequisites |
| `kubernetes.deployment_mode` | `gitops` or `direct`, with the responsible controller/process |
| `security.report_mode` | `gitlab-native`, `sarif`, or `artifact-only`, verified for the estate |
| `security.defaults` | Required scanners, blocking severity, unfixed-vulnerability treatment and exception policy |
| `retention` | Plan, evidence and release-record retention plus maximum deployable plan age |
| `environments` | Approved environment names, targets, protected-environment settings and approver references |

Profile values must be obtained from the actual estate or user. Missing values remain explicitly unresolved. Never copy illustrative `example.com` endpoints into a runnable deployment configuration.

Use a project-local `.ci/project.yml` to record the selected workload, component instance IDs, source directories, image repositories, environment targets, state IDs, inventory paths and deployment mode. It must reference an approved estate profile and shared-CI revision. Resolve those values into the committed CI YAML; enforce consistency in tests if both files are maintained.

## 5. Naming rules

### 5.1 Component names

Use lowercase kebab-case: `<domain>-<capability>[-<tool>]`.

- The domain describes the object or responsibility: `container`, `terraform`, `ansible`, `kubernetes`, `helm`, `security`, `quality`, `release`, `docs`, `artifact`.
- The capability describes the operation: `build`, `fmt`, `validate`, `plan`, `apply`, `scan`, `publish`, `promote`, `sign`, `sync`.
- Include a tool suffix when it distinguishes supported implementations.
- Do not include a team, application, environment or version in a component name.
- Do not conceal important tool differences behind one universal scanner switch.
- Names are stable API identifiers. Renaming one requires compatibility or a major release.

### 5.2 Job names and component instances

Every public component takes a required `instance` input matching `^[a-z][a-z0-9-]{0,47}$`.

The visible job name is exactly `<instance>:<component-name>`. A component with several inseparable jobs adds `:<operation>`; list every emitted name in its contract.

| Example | Meaning |
| --- | --- |
| `api:container-build-buildkit` | Build API image |
| `api:security-image-trivy` | Scan that API image |
| `frontend:security-image-trivy` | Separate frontend scan |
| `network-prod:terraform-plan` | Plan one identified Terraform root/state |
| `network-prod:terraform-apply` | Apply its matching plan |

Two instances of the same component must never emit the same job name. The same workload instance may be reused across different components to associate its build, scan and release chain.

Private hidden jobs use `.ci:<instance>:<component-name>:<purpose>`. Namespace private jobs too; they participate in GitLab's merged configuration. Consumers must not depend on private names. Legacy hidden-job names are supported only where explicitly listed as compatibility interfaces.

### 5.3 Paths, inputs and environment variables

- Inputs: lowercase kebab-case, for example `working-directory`, `plan-job`, `execution-image`.
- Runtime variables: uppercase snake case. Internal variables use `CI_TPL_`; consumers must not override reserved internal variables.
- Artifact roots: `.ci-artifacts/<instance>/<component-name>/` relative to `CI_PROJECT_DIR`.
- Environment names: use the estate's canonical names, initially `development`, `test`, `staging`, `production`; add other names centrally, not ad hoc.
- State/target identifiers must be explicit. Environment name alone is insufficient for several states or clusters in the same environment.
- Validate relative paths: no absolute paths, traversal outside the checkout, or symlink escape. Quoting is still required.

## 6. Component and composition contracts

### 6.1 What a public component must declare

| Contract item | Requirement |
| --- | --- |
| Purpose | One cohesive outcome; it may require multiple commands or inseparable jobs |
| Inputs | `spec:inputs`, descriptions, explicit types and meaningful validation |
| Identity | Required `instance`; deterministic job names |
| Stage | Configurable `stage`, default `verify`; compositions override where appropriate |
| Runner | `runner-tags` input when configurable; actual privilege and architecture requirements documented |
| Execution image | Verified digest-pinned default supplied by the released component, or required `execution-image` |
| Source | `working-directory` when applicable; default `.` only when sensible |
| Producers | Required upstream job inputs for consumed artifacts, such as `build-job` or `plan-job` |
| Gate prerequisites | `gate-jobs` array when the component is allowed to use DAG scheduling across earlier stages |
| Rules | `run-rules` array with safe source-specific defaults; never default a production mutation to unconditional execution |
| Outputs | Exact files, schemas, subject identities, retention and producer job names |
| Authentication | Secret names/OIDC contract, scopes, network and protected-ref requirements |
| Failure behaviour | Findings, execution failure, missing evidence and disabled operation handled distinctly |
| Compatibility | Minimum GitLab/Runner versions, tested tool/runtime versions and report modes |

Inputs configure pipeline creation; runtime outputs belong in files or dotenv reports. Do not put tokens, passwords or private keys into inputs. Input types are not a general shell-safety mechanism. Pass values through quoted arguments or validated runtime variables; never use `eval` to construct commands. See [GitLab inputs](https://docs.gitlab.com/ci/inputs/).

Do not introduce both `stage` and `job-stage`, or both `directory` and `working-directory`. Reuse the defined name where meanings match. Tool-specific inputs are allowed when their semantics differ.

Atomic components must not set top-level `stages`, `workflow`, `default`, `variables`, `image`, `cache` or `before_script`. Put job configuration inside jobs. Avoid deep `extends` chains: one private implementation parent is the normal limit.

Do not place a job-level empty default over a value documented as consumer top-level configuration. Inputs are preferred for configuration; protected project/group variables remain appropriate for secrets. Reserved runtime-variable names also need runtime validation because higher-precedence CI variables can override job variables.

### 6.2 What a composition owns

A composition owns source eligibility, stages, selected capabilities, instance names, graph edges, environment mapping and the required evidence for a release. It explicitly forwards inputs into components. Inputs do not automatically flow through nested includes.

Use `include:local` for components from the same shared-CI revision. Use pinned external includes only for independently versioned dependencies. Do not use the consumer's `CI_COMMIT_SHA` to locate helper files in the shared-CI repository.

One supported composition chooses one builder and one authoritative SBOM producer per image. Optional integrations are enabled deliberately and have documented prerequisites. Do not include every builder and then tell consumers to disable jobs.

Two complete compositions must not be blindly merged into one pipeline: their global stage and workflow arrays can conflict. Build a combined root composition or use child pipelines. Child pipelines need their own validated artifact and identity handoff; variables are not a substitute for immutable evidence.

### 6.3 Catalogue and availability

Generate `.ci/catalog.yml` from template headers plus `contract.yml` metadata. Generation and drift checks are repository implementation requirements; this guide does not provide a generator.

Each entry records `name`, `kind` (`component` or `pipeline`), `path`, `status`, `introduced_in`, `minimum_gitlab`, input declarations, emitted job patterns, outputs, required secrets, runtime image, trust requirements and validation evidence. `status` is one of `proposed`, `experimental`, `released`, `deprecated`.

Only `released` entries are normal consumer choices. Experimental adoption requires an explicit project decision. Deprecated entries include a replacement and removal release. The catalogue and template contracts at the pinned revision are authoritative; an agent must not invent an input found only in a future proposal.

## 7. Stages and pipeline sources

### 7.1 Stage vocabulary

Use an ordered subset of this vocabulary; do not add synonyms without a documented architecture change.

| Stage | Responsibility |
| --- | --- |
| `verify` | Formatting, syntax, source tests, secret/SAST/IaC checks and dependency reproducibility |
| `build` | Produce candidate application packages, images or machine images |
| `test` | Test built outputs and integration behaviour |
| `scan` | Generate authoritative SBOM and scan built artifacts |
| `plan` | Produce environment-specific proposed infrastructure changes |
| `attest` | Sign exact subjects and attach verified evidence |
| `publish` | Promote or publish approved immutable artifacts |
| `deploy` | Apply a saved plan, configure hosts or deploy an approved release |
| `verify-deploy` | Check deployed health and record deployment outcome |

Examples: a container release uses `verify, build, test, scan, attest, publish`; Terraform deployment uses `verify, plan, deploy, verify-deploy`; a module release uses `verify, test, publish`.

An approval is a control on the relevant job/environment, not an otherwise empty `approval` stage. Wiki sync and audits normally use `verify` unless their composition has a justified different stage.

### 7.2 Source rules

| Source | Normal behaviour |
| --- | --- |
| Merge request | Source verification; candidate builds/tests if the runner and credentials are suitable; speculative infrastructure plans only through the approved trust model |
| Default branch push | Verification and candidate integration; environment-specific deployment plans according to the chosen composition |
| Protected release tag | Release checks, attestations and publication; deployment only when separately enabled and authorised |
| Schedule | Explicit maintenance, drift, rescan or audit composition |
| Web/manual pipeline | Explicit operation and target; same controls as automated equivalents |
| Parent pipeline | Only admitted by a composition designed and tested for child pipelines |
| API/trigger pipeline | Explicitly allowed use case with authenticated inputs; not blanket permission to deploy |

Release tag grammar for new shared-CI releases is `MAJOR.MINOR.PATCH`, optionally with a prerelease suffix. No `v` prefix for new shared-CI tags. Existing consumer tag conventions remain supported during migration; module publication normalises an optional `v` prefix where needed. A matching tag string does not prove the tag is protected or authorised.

Compositions must prevent unintended duplicate branch/MR pipelines. Test workflow and job rules together, including missing producers. Do not use dotenv values in rules: those values are created after pipeline construction.

## 8. Dependency graph rules

1. A job consuming an artifact declares its exact producer with `needs: job` and `artifacts: true`.
2. A job waiting only for a gate declares `artifacts: false`.
3. Required producers and mandatory gates are never `optional: true`.
4. If a feature is disabled, its composition removes both that producer and dependent jobs, or chooses a complete alternate graph. It does not leave consumers guessing.
5. Do not combine `dependencies` and `needs` in the same job. Components using `needs` receive artifacts only through their declared needs.
6. A source-only job with no `needs` uses `dependencies: []` to avoid incidental artifact downloads.
7. Do not put `needs: []` into every atomic template. It enables early execution and can bypass stage barriers.
8. Every DAG-scheduled publish/deploy/attest job must have all required earlier gates in its transitive dependency chain. Stage names alone do not establish that chain.
9. Lists such as `needs`, `rules`, `script` and `stages` must be treated as replacement interfaces, not assumed to append through merges.
10. Validate repeat inclusion and missing-producer cases before releasing a component.

Where a component combines a required producer and `gate-jobs`, its wrapper constructs one list deliberately, using syntax supported by the estate. The assembled graph is part of the tested contract. Never silently drop artifact producers when overriding gate dependencies. See [GitLab needs](https://docs.gitlab.com/ci/yaml/needs/) and [artifact fetching](https://docs.gitlab.com/ci/jobs/job_artifacts/#fetching-artifacts).

## 9. Artifact contracts

These are organisation-defined file contracts to implement and test. Files must contain real values from the current operation. A filename alone is not evidence of validity.

### 9.1 Container build outputs

Every supported image builder emits the following beneath its artifact root:

| File/field | Contract |
| --- | --- |
| `image.json` | Authoritative image identity record; schema version `1` |
| `image.json.repository` | Fully qualified image repository without a tag or digest |
| `image.json.digest` | `sha256:` followed by 64 hexadecimal characters |
| `image.json.reference` | Exact `repository@sha256:...` reference |
| `image.json.source_commit` | Full source commit SHA |
| `image.json.pipeline_id` / `job_id` | Producing GitLab pipeline/job IDs, serialised consistently as strings |
| `image.json.platforms` | Actual platform list; distinguish an image manifest from a multi-platform index |
| `image.json.subject_kind` | `manifest` or `index` |
| `image.json.created_at` | UTC RFC 3339 timestamp |
| `build.env` | Optional convenience dotenv; never the sole authoritative identity |

For a single-subject downstream job, dotenv may expose `CI_TPL_IMAGE_REFERENCE`. That job must compare it to its selected `image.json`. A multi-image aggregator must read each namespaced identity file explicitly and must not rely on several producers exporting the same dotenv key.

BuildKit uses its exported image digest metadata; Jib and ko use verified output metadata or an authenticated digest resolver. Validate the result and fail if absent. Do not write a tag into a variable or field named `digest`. BuildKit distinguishes configuration and exported image digests in its metadata. See [BuildKit metadata](https://github.com/moby/buildkit/blob/v0.18.2/README.md#metadata).

Multi-platform releases record both the index identity and the platform manifests needed for scanning. Scanning one platform does not establish that every platform in the index passed.

### 9.2 SBOM and scanning outputs

| Producer | Required outputs |
| --- | --- |
| Authoritative SBOM job | `sbom.cdx.json` plus `subject.json` containing the exact image reference and source identity |
| Image/source scanner | Native scanner JSON, selected integration report, and `scan-result.json` |
| Gate evaluator, if separate | `gate-result.json` recording evaluated reports, subjects, policy version and pass/fail |
| Signing/attestation job | `attestation-result.json` identifying subjects, signer identity and successfully attached evidence |

SBOM default is CycloneDX JSON. SPDX is a separately declared contract, not a value that quietly changes the format behind `sbom.cdx.json`. Prefer Syft for the normal authoritative container SBOM. Trivy may produce it in an explicitly selected composition; do not download both producers' files into the same path or silently fall back.

`scan-result.json` must record schema version, subject identity, scanner/version, database snapshot or update timestamp where available, scan completion status, finding counts by severity, policy mode, threshold, unfixed-vulnerability treatment, exceptions applied and report file hashes. A failure to generate this record is not a clean scan.

A job scanning a supplied SBOM must validate its declared subject against the intended image. It must not switch targets merely because a file named `sbom.cdx.json` happens to exist.

Treat scanners as distinct capabilities. A filesystem scanner may find lockfile vulnerabilities, secrets and misconfiguration; scanning its output does not automatically replace image scanning or complete SAST coverage.

### 9.3 Terraform plan outputs

The plan producer emits `tfplan` and `plan-metadata.json` beneath its artifact root. Any JSON plan export is also sensitive and gets equivalent protection.

Metadata records schema version, engine/version, source commit, pipeline/job IDs, working directory, environment, state identifier, backend identity excluding secrets, dependency-lock-file hash, plan-file SHA-256, creation time and maximum deployable age.

A human-readable summary is supplementary review material. It does not replace the binary plan consumed by apply. Do not attach unreviewed raw plan output to broadly visible MR comments.

Recommended starting retention for plans is 7 days and maximum deployable age is 24 hours; these are **organisation defaults to confirm in the estate profile**, not Terraform or GitLab requirements. Set explicit access restrictions and account for instance-level retention of the latest successful artifacts. Retention is not a guarantee of deletion or permission control. See [artifact retention and access](https://docs.gitlab.com/ci/jobs/job_artifacts/).

### 9.4 Artifact isolation and trust

- Artifact paths remain under `.ci-artifacts/`; do not upload `.terraform/`, state files, credentials, complete cloned repositories or an entire checkout by default.
- Include instance/component identifiers in archive names as well as paths.
- Cache contains disposable acceleration data only. It must never be the authoritative plan, release identity or signed evidence.
- Scope caches by project, tool/runtime version, architecture and lockfile hash where appropriate. Separate protected and untrusted workloads.
- Producers validate output before declaring success. Consumers validate schema, subject and expected source identity before use.
- Do not fetch the latest artifact by branch name for apply/sign/promote. Select the exact approved producing run.
- A privileged downstream job must not treat untrusted MR-generated scripts, dotenv or evidence as authoritative. Use the configured trust boundary and approved producer identity.

## 10. Security, identity and failure semantics

### 10.1 Mandatory outcome rules

| Situation | Required outcome |
| --- | --- |
| Blocking finding meets the configured threshold | Job fails; release/deploy cannot proceed |
| Finding in explicitly advisory mode | Record it, succeed only if the scan completed correctly |
| Scanner crash, timeout, malformed output or missing required database | Job fails in both blocking and advisory modes |
| Signing enabled but credentials unavailable | Job fails |
| Required SBOM or attestation evidence absent | Job fails |
| Authentication/registry response cannot be validated | Job fails |
| Optional feature disabled before pipeline creation | Omit its jobs and select the matching graph |
| A policy exception applies | Record exception ID, scope, owner and expiry in evidence |

Do not use `|| true`, `allow_failure: true` or unconditional `exit 0` to implement advisory scanning. Capture and distinguish the tool's documented finding code from execution errors. Runtime wrappers own tool-specific exit-code interpretation and must be tested against the pinned tool version.

Production release compositions default to blocking required controls. General consumer examples must not quietly disable Gitleaks or set vulnerability thresholds to non-blocking values. Keep advisory onboarding examples separately named and clearly scoped.

### 10.2 Authentication

Prefer short-lived OIDC-based identities for Vault and cloud services where the estate supports them. Each integration declares audience, provider/auth mount, role, token variable name and required scopes. Do not hardcode `vault.example.com` or a universal `gitlab-ci` role into a reusable component. See [GitLab ID tokens](https://docs.gitlab.com/ci/secrets/id_token_authentication/).

Use separate least-privilege identities for candidate builds, registry reads, signing, promotion and deployment. Untrusted MR jobs do not receive production write or signing credentials. A runner tag chooses a runner; it is not, by itself, a permission boundary.

Use validated TLS and maintained trust bundles. Do not make `--insecure`, host-key checking disablement or privileged execution the default fix for connectivity. Document executor requirements, including Docker-in-Docker or nested-build privileges, in the component contract.

### 10.3 Reports and quality gates

The estate profile chooses the supported report integration once:

| Mode | Behaviour |
| --- | --- |
| `gitlab-native` | Generate the correct GitLab schema for SAST, secret detection, dependencies or container scanning |
| `sarif` | Upload supported SARIF 2.1.0 through `artifacts:reports:sarif`; validate ingestion on the target estate |
| `artifact-only` | Publish readable/raw reports and enforce local exit-code gates; do not claim vulnerability-widget integration |

Do not declare raw SARIF as `reports:sast`. Native SARIF ingestion became GA in GitLab 19.2 and is an Ultimate feature; earlier versions have different availability. SARIF inference also does not guarantee the same categorisation as a native container-scanning report. See [GitLab SARIF support](https://docs.gitlab.com/user/application_security/detect/sarif/).

SonarQube analysis, external-report import and the SonarQube quality gate are distinct outcomes. A successful upload does not establish a passed quality gate. Required quality gates must wait for and evaluate their actual result. Dependency-Track upload acceptance likewise does not establish completed analysis or policy compliance.

When several formats are needed, scan once and convert the captured result where the tool supports it. If multiple scans are necessary, use the same immutable subject and controlled database snapshot and record the relationship.

### 10.4 Enforcement boundary

Reusable components and pipeline includes are editable by consumers. Use linked Pipeline Execution Policies where supported, protected environments/refs, identity permissions, registry controls and deployment-controller admission as appropriate. The security policy definition belongs at `.gitlab/security-policies/policy.yml` in a linked policy project. Pipeline Execution Policies require Ultimate. See [policy enforcement](https://docs.gitlab.com/user/application_security/policies/pipeline_execution_policies/).

The CI repository may host versioned jobs referenced by those policies. Give the policy project separate approval authority when separation of duties is required. Policy jobs are tested for skip directives, variable overrides, disabled features, missing artifacts and graph bypasses. Do not claim enforcement on an estate where the necessary controls are absent.

## 11. Workload wiring recipes

### 11.1 Container applications

Default builder: BuildKit for Dockerfiles. Select ko for a supported Go build contract and Jib for a supported Java contract. Do not enable all three.

The logical sequence is source verification, candidate build, tests, authoritative SBOM/image scanning, signing/attestation, then approved publication or promotion. Candidates can be pushed to a restricted registry before scanning; that push is not approval for release.

| Consumer job | Required producers/gates |
| --- | --- |
| Candidate build | Required source checks, including secret detection |
| Built-image tests | Exact build identity and all required service image identities |
| SBOM generation | Exact build identity |
| Image scan | Exact build identity; authoritative SBOM if scanning it |
| Signing/attestation | Build identity, required test gates, scan gate, authoritative SBOM and required provenance |
| Promotion | Validated signing/attestation result plus all required gates |
| Deployment | Approved immutable release record and applicable environment controls |

Tests and scans may run in parallel after build. Signing waits for both. Multi-service integration tests depend on all relevant builds; each scanner still receives only its own subject. A root aggregator must not merge several `build.env` files and assume the last value represents every service.

Promote the already tested image; do not rebuild per environment. Verify destination digest after registry copying and handle signatures/referrers explicitly because transfer behaviour differs between tools/registries. A tag can be a human-readable alias; deployment consumes the immutable identity.

Do not label hand-written source metadata as a verified SLSA level. Capture builder-produced provenance where supported and describe what was actually verified. Missing requested attestations fail publication.

### 11.2 Terraform infrastructure

Default component family: `terraform-fmt`, `terraform-validate`, `security-filesystem-trivy` or a future explicit IaC component, `terraform-plan`, `terraform-apply`.

1. Validate and check formatting without production mutation credentials. `terraform init -backend=false` still may need provider/module access; it does not imply offline operation.
2. Commit and honour the provider lockfile. Use controlled provider/module mirrors where the estate is air-gapped.
3. Run an MR/speculative plan only with credentials suitable for the source trust level. Terraform planning can execute providers and data-source behaviour; it is not automatically safe merely because it is a plan.
4. Generate the deployment plan for the exact deployment commit, root, state and environment.
5. Require the configured manual/environment approval. Approval relates to that exact plan hash and identity.
6. Apply only the matching saved plan. Never silently re-plan inside apply.
7. Before apply, validate metadata, age, hash, engine version, lockfile, state ID and source identity. If stale or mismatched, create and review a new plan.
8. Use backend state locking and a GitLab resource group per target/state. Resource groups coordinate jobs within their scope; they are not a substitute for backend locks or cross-project state ownership.
9. Keep apply/destroy non-interruptible by routine new-commit cancellation. Design recovery before retrying a partially completed mutation.
10. Use a dedicated reviewed destroy-plan/apply flow. Never make destroy an automatically selected branch of the normal pipeline.

`plan-job` is required on apply; its value is `<instance>:terraform-plan`. The composition passes matching `instance`, directory, environment, state and execution image. `environment:` must be combined with actual protected-environment settings where required. See [Terraform automation](https://developer.hashicorp.com/terraform/tutorials/automation/automate-terraform), [state locking](https://developer.hashicorp.com/terraform/language/state/locking) and [GitLab resource groups](https://docs.gitlab.com/ci/resource_groups/).

For multiple roots, create one instance per root/state/environment. A single blanket `production` resource group unnecessarily serialises unrelated states. Conversely, two projects targeting the same state need shared backend locking and a centrally coordinated deployment ownership model.

### 11.3 Ansible configuration

Target components: `ansible-lint`, `ansible-syntax`, `ansible-check`, `ansible-deploy`.

- Use a pinned execution environment containing Ansible Core, Python libraries and collection versions. Keep collection requirements reproducible.
- Common inputs: `instance`, `working-directory`, `playbook`, `inventory`, `limit`, `environment`, `execution-image`, and explicit non-secret extra-vars file paths.
- Do not add a generic arbitrary shell-command input. Do not place secrets in extra-vars strings or logs.
- Preserve SSH host verification and source credentials through approved identities/secret mechanisms.
- Lint/syntax belong in `verify`. Check mode is optional and must be documented as partial predictive coverage, not a guaranteed deployment plan. Disable diff where sensitive values would be exposed.
- A deployment follows its required lint/syntax/check gates and target approval. Use resource-group scope covering the actual overlapping host set; inventory filename alone does not prove disjoint targets.
- If Terraform produces inventory, export only approved non-secret outputs into a schema-validated inventory artifact. Ansible depends on that exact producer; do not fetch arbitrary state or the latest branch artifact.
- Verify changed Ansible module FQCNs and parameters against the pinned collection's documentation.

Ansible task files use the order: `name`, fully qualified module with parameters, `vars`, `loop`, `loop_control` (including `loop_var` and `label`), `when`, `register`. Add other controls deliberately without changing the meaning of the task.

### 11.4 Kubernetes and Helm

Target components: `kubernetes-validate`, `kubernetes-deploy`, `kubernetes-verify`, `kubernetes-rollback`, `helm-validate`, `helm-package-publish`, and `kubernetes-gitops-update` when required.

- The estate/project profile declares `gitops` or `direct` deployment ownership. Do not mix direct apply with a GitOps controller managing the same objects.
- Validate rendered resources against the target Kubernetes/API/CRD versions. Helm lint alone is not full schema validation.
- Pin chart dependencies, deployment chart versions and workload image identities. Build/package failure must not be hidden with `|| true`.
- In GitOps mode, CI updates the desired-state repository using its approved review/promotion process. The controller reconciles; CI does not also run a competing direct deployment.
- In direct mode, inputs identify context, cluster, namespace and release explicitly. Use least-privilege credentials, an environment record, a scoped resource group and rollout verification.
- Helm publication selects the chart archive it just produced, never the first arbitrary `*.tgz` in the workspace.
- Rollback targets a recorded previous release. Treat destructive hooks, database changes and CRD changes separately; do not promise that a chart rollback reverses all side effects.

### 11.5 Terraform module publishing

Verify module source, examples and supported versions before packaging. Package only the intended module content and explicitly exclude state, `.terraform`, credentials and generated plans. Publish a normalised semantic version and reject an existing immutable release rather than overwrite it. `terraform-module-publish` does not perform infrastructure apply.

### 11.6 Repository audits and wiki sync

The former Terraform secret audit is a composition: obtain an explicit repository set and scan it. Make the executor project's schedule, scope and credentials operational configuration. The reusable scanning implementation may remain in the shared repository. A failure to clone an in-scope repository must report incomplete coverage and fail the required audit; scanning zero repositories is not success.

Wiki sync is a distinct runtime tool. Pin its package/image to a tested version. Do not let a pinned YAML include fetch moving `main` scripts. Use explicit pipeline intent for wiki events/schedules; do not suppress every scheduled job merely because a repository also has wiki sync. Preserve and run its existing behaviour/race tests when moving it. Extract it to a separate project only with an explicit release/ownership decision and a documented migration.

### 11.7 Chaining infrastructure operations

Terraform, Ansible, image builds and Kubernetes deployment do not have one universal order. Declare the actual dependencies:

| Intended outcome | Required relationship |
| --- | --- |
| Configure newly provisioned VMs | Terraform apply produces approved inventory; Ansible consumes it |
| Build an application image | Source build does not automatically depend on infrastructure apply |
| Deploy to an existing cluster | Approved image/chart plus target credentials; provisioning need not run |
| Provision a new cluster then deploy | Provisioning/cluster readiness precedes deployment; image build may run independently |
| Promote an existing release | Reuse identity and evidence; no build or infrastructure change unless requested |

Use child/downstream pipelines for distinct lifecycle or trust boundaries, with immutable cross-pipeline references and explicit validation. Do not turn a complete estate workflow into one compulsory mega-pipeline.

## 12. Runtime packaging and reproducibility

CI YAML connects operations; maintained execution images contain substantial implementation code. Small shell sequences are fine inline. Move repeated, complex, multi-format or security-sensitive logic into `runtime/<domain>/` with tests and package it into `images/<runtime>/`.

Do not call adjacent `scripts/...` assuming a component include checked out the shared repository. It imports YAML; the job normally operates on the consumer checkout.

- Pin execution images by digest where the estate can serve one, and record architecture and tool versions in the catalogue. A component may accept `name:tag`, or no image at all for a shell executor, when its contract declares which executor each form is for; see the 1.0.5 entry in section 16.
- Pin runtime tools, collections, rule packs and dependency sources. A pinned scanner image with moving remote rules is not a fully pinned scan configuration.
- Avoid `go install ...@latest`, unverified `curl` executables and unpinned package installation in consumer jobs.
- When runtime download is unavoidable, use a pinned immutable source and independently verified checksum/signature; fail validation errors.
- Record vulnerability-database freshness separately from scanner version. A frozen database is reproducible but may miss newly disclosed vulnerabilities; the profile defines acceptable age and refresh procedure.
- Mirror images, packages, Terraform providers/modules and scanner data for restricted networks; test the declared offline mode without outbound access.
- Keep registry authentication portable across supported registries. Do not assume HTTP Basic authentication alone implements every registry's token challenge.
- Renovate must actually detect variable-based pins and runtime locks. Test extraction or configure custom managers; merely having `renovate.json` is not evidence of coverage.

## 13. Reference YAML: consistent component-to-composition wiring

The following files are implementation examples, not existing released files. They demonstrate the exact agreed conventions without pretending that a complete Terraform deployment component already exists. The runtime image must contain the approved Terraform binary and a POSIX shell. The validation example expects the project's reviewed provider lockfile and available providers/modules.

The consumer example has two explicit placeholders: replace them with real approved values before CI Lint. No illustrative release tag or image digest in this document should be treated as published.

### 13.1 Formatting component

```yaml
# templates/terraform-fmt/template.yml

#### Public interface ####
spec:
  inputs:
    instance:
      type: string
      description: Unique workload instance used in the job name.
      regex: '^[a-z][a-z0-9-]{0,47}$'
    stage:
      type: string
      description: Stage supplied by the owning composition.
      default: verify
    working-directory:
      type: string
      description: Terraform root relative to the consumer checkout.
      default: '.'
    execution-image:
      type: string
      description: Approved Terraform execution image pinned by digest.
      regex: '^.+@sha256:[0-9a-f]{64}$'
    runner-tags:
      type: array
      description: Runner selection from the estate profile.
      default: []
    run-rules:
      type: array
      description: Job eligibility set by the composition.
      default:
        - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
        - if: '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
        - if: '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_TAG'
---

#### Executable capability ####
"$[[ inputs.instance ]]:terraform-fmt":
  stage: $[[ inputs.stage ]]
  image:
    name: $[[ inputs.execution-image ]]
    entrypoint: ['']
  tags: $[[ inputs.runner-tags ]]
  variables:
    CI_TPL_WORKING_DIRECTORY: $[[ inputs.working-directory ]]
    TF_IN_AUTOMATION: 'true'
  dependencies: []
  before_script:
    - |
      project_root=$(cd "$CI_PROJECT_DIR" && pwd -P)
      cd "$CI_PROJECT_DIR/$CI_TPL_WORKING_DIRECTORY"
      case "$(pwd -P)" in
        "$project_root"|"$project_root"/*) ;;
        *) echo 'ERROR: working-directory escapes the checkout'; exit 1 ;;
      esac
  script:
    - terraform fmt -check -recursive .
  rules: $[[ inputs.run-rules ]]
  allow_failure: false
  interruptible: true
```

`instance` determines the public job name; `stage` and `runner-tags` integrate with the composition and estate. `execution-image` is digest constrained. `CI_TPL_WORKING_DIRECTORY` carries the input as runtime data; the shell validates its resolved location. `project_root` is a local shell variable containing the physical checkout path. `dependencies: []` prevents unrelated artifact downloads without enabling early DAG execution. Formatting is non-mutating because `-check` is used. Every component job also declares `inherit: default: [tags, timeout, interruptible, retry, id_tokens]`, so a consumer's `default:` image, `before_script`, `after_script`, `cache`, `services`, `artifacts` or `hooks` cannot reach it: those change what the job runs or what is on disk when it starts, and none of them is covered by the component's contract, tests or evidence, while runner selection and timeouts remain the consumer's to set. Every component job also sets `FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR: 'true'`, because an execution image that runs as a non-root uid cannot depend on the ownership of a build directory that the runner helper, a cache restore or a previous job in the same concurrency slot wrote as root: the flag makes the runner read the image's uid and gid and change the build directory's ownership to them instead of relying on `umask 0000`.

### 13.2 Validation component

```yaml
# templates/terraform-validate/template.yml

#### Public interface ####
spec:
  inputs:
    instance:
      type: string
      description: Unique workload instance used in the job name.
      regex: '^[a-z][a-z0-9-]{0,47}$'
    stage:
      type: string
      description: Stage supplied by the owning composition.
      default: verify
    working-directory:
      type: string
      description: Terraform root relative to the consumer checkout.
      default: '.'
    execution-image:
      type: string
      description: Approved Terraform execution image pinned by digest.
      regex: '^.+@sha256:[0-9a-f]{64}$'
    runner-tags:
      type: array
      description: Runner selection from the estate profile.
      default: []
    run-rules:
      type: array
      description: Job eligibility set by the composition.
      default:
        - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
        - if: '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
        - if: '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_TAG'
---

#### Executable capability ####
"$[[ inputs.instance ]]:terraform-validate":
  stage: $[[ inputs.stage ]]
  image:
    name: $[[ inputs.execution-image ]]
    entrypoint: ['']
  tags: $[[ inputs.runner-tags ]]
  variables:
    CI_TPL_WORKING_DIRECTORY: $[[ inputs.working-directory ]]
    TF_IN_AUTOMATION: 'true'
  dependencies: []
  before_script:
    - |
      project_root=$(cd "$CI_PROJECT_DIR" && pwd -P)
      cd "$CI_PROJECT_DIR/$CI_TPL_WORKING_DIRECTORY"
      case "$(pwd -P)" in
        "$project_root"|"$project_root"/*) ;;
        *) echo 'ERROR: working-directory escapes the checkout'; exit 1 ;;
      esac
    - terraform init -backend=false -input=false -lockfile=readonly
  script:
    - terraform validate -no-color
  rules: $[[ inputs.run-rules ]]
  allow_failure: false
  interruptible: true
```

The shared inputs have the same meanings as the formatting component. `TF_IN_AUTOMATION` selects automation-friendly output. Initialization disables the backend, interactive prompts and lockfile updates; it still installs the required provider/module dependencies. Validation checks the initialized configuration. Small duplication here is intentional: consumers do not need a second hidden global base include.

### 13.3 Root verification composition

```yaml
# pipelines/terraform-verify.yml

#### Consumer interface ####
spec:
  inputs:
    instance:
      type: string
      description: Terraform workload identifier.
      regex: '^[a-z][a-z0-9-]{0,47}$'
    working-directory:
      type: string
      description: Terraform root in the consumer checkout.
      default: '.'
    terraform-image:
      type: string
      description: Approved Terraform execution image pinned by digest.
      regex: '^.+@sha256:[0-9a-f]{64}$'
    runner-tags:
      type: array
      description: Approved runner selection.
      default: []
---

#### Pipeline ownership ####
workflow:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
    - if: '$CI_PIPELINE_SOURCE == "push" && $CI_COMMIT_TAG'
    - when: never

stages:
  - verify

#### Components at the same shared-CI revision ####
include:
  - local: '/templates/terraform-fmt/template.yml'
    inputs:
      instance: $[[ inputs.instance ]]
      stage: verify
      working-directory: $[[ inputs.working-directory ]]
      execution-image: $[[ inputs.terraform-image ]]
      runner-tags: $[[ inputs.runner-tags ]]
  - local: '/templates/terraform-validate/template.yml'
    inputs:
      instance: $[[ inputs.instance ]]
      stage: verify
      working-directory: $[[ inputs.working-directory ]]
      execution-image: $[[ inputs.terraform-image ]]
      runner-tags: $[[ inputs.runner-tags ]]
```

The composition creates two parallel verification jobs and owns their stage/workflow. Each local include receives explicit inputs. This composition deliberately performs formatting and validation only; it must not be advertised as full infrastructure security or deployment. A production `terraform-deploy.yml` adds its required source-security checks, plan and deployment controls according to sections 8–11.

### 13.4 Consumer configuration

```yaml
# examples/terraform-verify/.gitlab-ci.yml

#### Select the approved central composition ####
include:
  - project: 'platform/ci-templates'
    ref: 'REPLACE_WITH_APPROVED_SHARED_CI_REF'
    file: '/pipelines/terraform-verify.yml'
    inputs:
      instance: network
      working-directory: infrastructure/network
      terraform-image: 'REPLACE_WITH_APPROVED_TERRAFORM_IMAGE_AT_DIGEST'
      runner-tags:
        - linux-unprivileged
```

Replace the project path, ref, runtime image, source directory and runner tag using actual estate/project values. `network` produces `network:terraform-fmt` and `network:terraform-validate`. The consumer does not redeclare stages, reimplement scripts or disable unrelated builders.

The syntax above uses documented [input interpolation](https://docs.gitlab.com/ci/inputs/), [project/local includes](https://docs.gitlab.com/ci/yaml/includes/), [Terraform initialization](https://developer.hashicorp.com/terraform/cli/commands/init) and [validation](https://developer.hashicorp.com/terraform/cli/commands/validate). YAML parsing alone cannot prove GitLab compilation, image availability or successful Terraform execution.

## 14. Verification and release gates

### 14.1 Required repository checks

| Check | What it must prove |
| --- | --- |
| YAML/schema validation | Parse configuration; verify component headers, input names/types and metadata schemas |
| Catalogue drift | Generated catalogue matches actual files and declared contracts |
| Same-revision compilation | Test current components at the current shared-CI SHA, not the last released version |
| Pipeline-source fixtures | Expected jobs for MR, branch, tag, schedule, manual and supported downstream sources |
| Repeat inclusion | Two instances have distinct jobs, paths and correct producer selection |
| Dependency graph | Every mutation/publication has required gates in its dependency chain; no absent required producers |
| Runtime unit tests | Exit-code handling, argument safety, identity parsing and report conversions |
| Negative integration cases | Missing Vault, missing SBOM, registry auth failure, scanner error, digest mismatch and expired/mismatched plan fail appropriately |
| Positive integration | Selected build/scan/sign/publish or deployment path works against disposable approved targets |
| Report ingestion | Validate schema and, when integrating GitLab security UI, confirm real findings reach the expected destination |
| Compatibility matrix | Run the supported server/runner/runtime combinations relevant to the change |

Use the GitLab CI Lint API or equivalent supported project pipeline validation for merged configuration. Tests must create executable probes for legacy hidden-only templates. Do not run production mutations as smoke tests. Credentials and external systems used in integration tests must be scoped to test resources.

Do not expand tests indefinitely. Run the established gates plus focused cases for changed behaviour; stop when the concrete risks and required gates are covered. Formatting-only documentation changes do not require registry or deployment integration tests.

Record verification accurately as `source-reviewed`, `yaml-parsed`, `gitlab-linted`, `runtime-tested`, `integration-tested` and `report-ingestion-tested`, as applicable. These labels are cumulative evidence, not interchangeable claims. Each record includes revision and environment.

### 14.2 Versioning and compatibility

- Pin consumers to a full commit SHA or an exact protected release tag; use the estate's approved reference policy. GitLab prefers commit SHAs for integrity. Never use moving `main`, `latest` or partial-version selection for production consumers under this standard.
- Version all components and compositions in the repository together initially.
- Patch: compatible bug fix. Minor: compatible capability/input addition. Major: removed/renamed interface, incompatible output schema or intentionally changed default behaviour affecting consumers.
- Security emergencies follow the estate's documented expedited rollout procedure. Do not hide a breaking emergency change behind a claim of compatibility.
- Publish stable `1.0.0` after the relevant execution and contract gates pass. A pinned pre-release or baseline SHA can provide immediate reproducibility while fixes are underway.
- Include release notes, migrations, known limitations and tested compatibility in every release.
- Catalogue publication requires the GitLab project's catalogue configuration and the CI `release:` mechanism. Component consumption is possible without catalogue publication. See [component releases](https://docs.gitlab.com/ci/components/#publish-a-new-release).
- Self-tests reference the current component SHA; consumer examples reference a real released ref. A test fixture may substitute the current SHA explicitly.

### 14.3 Deprecation

Maintain old include paths and public hidden-job names through a stated migration window. A compatibility wrapper must preserve its documented jobs, inputs/defaults, rules and artifacts; a bare include of a differently named component is not sufficient.

Remove deprecated interfaces in the announced major release after checking known consumers. Do not delete `trivy-scan.yml` simply because its comment says deprecated. Keep unsupported experimental capabilities out of normal examples. For the whole migration window a deprecated component prints a banner as the first lines of its job output, naming itself, the component that replaces it and the release that removes it, because a `CHANGELOG.md` entry reaches whoever reads release notes while the banner reaches the person whose pipeline is running the thing.

## 15. Migration map for the reviewed repository

This map reserves target names; it is not a claim that a migration has occurred.

| Current path | Target | Required action beyond moving |
| --- | --- | --- |
| `build/buildkit.yml` | `templates/container-build-buildkit/template.yml` | Use exported immutable digest; remove tag fallback; declare runner requirements |
| `build/ko.yml` | `templates/container-build-ko/template.yml` | Pin ko/runtime; capture structured image identity; control release tags |
| `build/jib.yml` | `templates/container-build-jib/template.yml` | Emit actual digest and enforce a reproducible runtime/plugin contract |
| `build/helm-oci.yml` | `templates/helm-package-publish/template.yml` | Fix input precedence; deterministic archive selection; fail dependency errors |
| `test/smoke-test.yml` | `templates/container-smoke-test/template.yml` | Declare Compose/Makefile contract, image identity inputs and runner privileges |
| `terraform/lint.yml` | `templates/terraform-fmt/template.yml` | Rename according to actual fmt behaviour |
| `terraform/validate.yml` | `templates/terraform-validate/template.yml` | Standard inputs, engine/runtime and directory validation |
| `terraform/plan-apply.yml` | `templates/terraform-plan/template.yml` and `templates/terraform-apply/template.yml` | Matching plan contract, controls, retention and explicit producer |
| `terraform/module-publish.yml` | `templates/terraform-module-publish/template.yml` | Scoped package contents and immutable version publication |
| `security/gitleaks.yml` | `templates/security-secrets-gitleaks/template.yml` | Supported report integration and defined history coverage |
| `security/semgrep.yml` | `templates/security-sast-semgrep/template.yml` | Preserve scanner failures; pin rules; correct report integration |
| `security/trivy-fs.yml` | `templates/security-filesystem-trivy/template.yml` | Scope, report and gate contracts |
| `security/grype-fs.yml` | `templates/security-filesystem-grype/template.yml` | Same contract discipline; retain only with supported purpose |
| `security/trivy-image.yml` | `templates/security-image-trivy/template.yml` | Scan immutable subject; remove implicit authoritative-SBOM duplication |
| `security/grype-image.yml` | `templates/security-image-grype/template.yml` | Validate supplied SBOM subject; no arbitrary local-file fallback |
| `security/syft-sbom.yml` | `templates/security-sbom-syft/template.yml` | Authoritative namespaced SBOM and subject record |
| `security/cosign-sign.yml` | `templates/container-sign-attest-cosign/template.yml` | Fail missing credentials/evidence; package runtime; explicit Vault identity |
| `security/dtrack-upload.yml` | `templates/security-sbom-upload-dtrack/template.yml` | Exact producer; distinguish accepted upload from completed analysis |
| `security/sonarqube.yml` | `templates/quality-sonarqube/template.yml` | Explicit report producers and required/advisory gate behaviour |
| `security/lockfile-check.yml` | `templates/quality-dependency-lockfiles/template.yml` | Validate intended languages/roots; do not treat one pinned line as complete reproducibility |
| `security/terraform-audit.yml` | `pipelines/repository-secret-audit.yml` plus reusable scanning capability | Explicit repository scope and incomplete-coverage failure |
| `security/trivy-scan.yml` | Retained compatibility path until announced removal | Preserve old behaviour during deprecation; direct new users to image-scanning contract |
| `docs/wiki-sync.yml` | `templates/docs-wiki-sync/template.yml` | Version runtime with YAML and declare explicit pipeline intent |
| `scripts/wiki/` implementation | `runtime/wiki/` | Package/version it; preserve behaviour |
| `scripts/wiki/test_*.py` | `tests/runtime/wiki/` | Update imports and run the existing tests |
| `pipelines/devsecops.yml` | Compatibility entry point plus focused new compositions | Stop new consumers depending on include-everything behaviour |
| Existing flat examples | `examples/<workload>/.gitlab-ci.yml` | Tested, minimal and blocking by default where release controls are required |

Initial new compositions are `container-buildkit.yml`, `container-ko.yml`, `container-jib.yml`, `helm-chart.yml`, `terraform-verify.yml`, `terraform-deploy.yml`, `terraform-module.yml` and `repository-secret-audit.yml`. Add `ansible-deploy.yml`, `kubernetes-direct.yml` and `kubernetes-gitops.yml` only when their components and estate contracts are implemented.

## 16. Agent task recipes

### Create a consumer pipeline

1. Resolve profile, approved shared-CI revision and actual catalogue entry.
2. Identify workload instances, source roots, target identities and required gates.
3. Select one composition. If none fits, propose and implement the smallest justified new composition using released atoms; do not edit every consumer to accommodate central shortcomings.
4. Supply declared inputs and approved secret names. Do not duplicate secrets in repository metadata.
5. Validate source scenarios and the expected graph.
6. Confirm relevant runtime/integration evidence before claiming operational readiness.

### Add a component

1. Confirm no released component already meets the requirement.
2. Reserve its canonical name and define `contract.yml` outputs/failure semantics before implementation.
3. Implement `spec:inputs` and namespaced executable jobs, packaging runtime code where appropriate.
4. Add fixtures for defaults, invalid inputs, two instances and missing dependencies/evidence.
5. Add one focused consumer example and catalogue metadata; mark experimental until release gates pass.
6. Release at the appropriate semantic version and update consumers through reviewable changes.

### Change a shared component

Identify affected consumers and contract impact. Preserve unrelated inputs, names and outputs. Fix the regression with a focused test. Update catalogue/compatibility/release notes. For a breaking change, provide migration and retain the old contract during its supported window.

### Handle an unknown estate value

Complete all useful local design and validation first. Ask only for values that materially determine behaviour or access, such as deployment mode or actual backend identity. Reuse answers already present in the project/profile/session. Do not repeatedly ask users to choose naming conventions already fixed here.

### Change the standard itself

Record the decision, reason, affected contracts and migration. Increment this document version. Update the catalogue schema/tooling and affected examples together. Do not introduce a second competing layout in one component as an undocumented exception.

### Recorded changes to this standard

#### 1.0.1 — 2026/09/15, adopting the standard in `platform/gitlab-ci-templates`

Three decisions taken while implementing sections 3 to 15 in this repository. Each is a change to the standard, not an exception to it.

**Capability `trigger` added to the section 5.1 vocabulary.**
Two of the capabilities this estate already runs start a build on another system and wait for nothing: a Jenkins seed job and a Semaphore template run. None of `build`, `deploy`, `publish`, `promote`, `sign` or `sync` describes that. Calling it `deploy` would claim an ownership the job does not have, and calling it `sync` would claim a reconciliation it does not perform. `trigger` names it: start a named operation on an external system and report whether it was accepted. Affected contracts: the component names `release-trigger-jenkins` and `release-trigger-semaphore`. Migration: none; no released component used another word for this.

**The Vigil readiness gate is named `security-verify-vigil`, with no new capability word.**
The proposed name used a `gate` capability, which section 5.1 does not define and which describes the job's position in the graph rather than what it does. The job verifies a supply-chain readiness verdict, so `verify` covers it, and the existing `sync` covers the record-publishing half. Affected contracts: `security-sync-vigil` and `security-verify-vigil`. Migration: none. `gate` remains outside the vocabulary; a job that gates is described by what it checks.

**A list-typed input cannot be interpolated into a job variable, so list-shaped inputs are single validated strings.**
Measured on gitlab.example.com 18.9.1-ee: interpolating an `array` input into a `variables:` value is rejected with `must be a string or a hash`. An `array` input is therefore usable in `tags:`, `rules:` and `needs:` and nowhere else. Section 11.3 asks for a list of extra-variable files; a component that needs such a list in shell takes one regex-constrained string instead, and a component that needs several takes several instances. Affected contracts: `ansible-syntax` and `ansible-check` (`extra-vars-file`), and every component whose file-list or path-list input is a string. This is a GitLab limitation being recorded, not a preference: a future server version that allows the interpolation would let these inputs become arrays, and that would be a breaking input change requiring a major release.

Sections not changed by any of the above: 3, 6, 7.1 stage vocabulary, 8, 9, 10, 12, 13, 14. The catalogue schema needed no change for any of the three: it constrains contract structure, not the capability word or the input type. The components and examples implementing all three ship in the same release, 1.0.0-rc.1.

#### 1.0.2 — 2026/09/16, a deprecation signal the consumer actually sees

**Decision.** Section 14.3 now requires a deprecated component to print a banner at the top of its job output for the whole migration window, naming itself, its replacement and the removal release.

**Reason.** A review of two external GitLab CI component repositories (`pipelines-main`, `pipelines-contrib-main`) found `build-buildkit` printing exactly that, and it answered a gap in 14.3: the section required a migration window but named no signal during it. Everything this repository had — the `CHANGELOG.md` entry, the `deprecated` block in `contract.yml`, the catalogue status — reaches somebody reading release notes. None of it reaches the person whose pipeline is running the deprecated component, who is the only person who can act. The banner costs three lines of shell and no input.

**Affected contracts.** None today: no component in `.ci/catalog.yml` has `status: deprecated`, so nothing has to change to comply. The requirement binds the next component this repository deprecates, and its `contract.yml` `deprecated.replacement` and `deprecated.removed_in` are what the banner must quote.

**Migration.** None. Adding a banner changes no job name, input, rule or artifact, so it is a patch-level change to a component and never a reason to hold one back.

Sections not changed: everything but 14.3. The catalogue schema needed no change — `deprecated.replacement` and `deprecated.removed_in` already carry what the banner prints.

Three further practices from the same review were absorbed as repository changes rather than standard changes, because the standard already permitted each: `retry: when: [runner_system_failure]` behind a typed `max-retries` input on every component, `GIT_STRATEGY: none` on the seven jobs that read no checkout, and a `release` job so a version tag creates the GitLab Release that section 14.2 already assumed. All three ship in 1.0.0.

#### 1.0.3 — 2026/09/16, a component job does not inherit a consumer's defaults

**Decision.** Section 13.1's component file shape now requires every job a component emits, hidden jobs included, to declare `inherit: default: [tags, timeout, interruptible, retry, id_tokens]`.

**Reason.** A `default:` block is a consumer's convenience for its own jobs, and GitLab applies it to every job in the merged configuration, including one that came from an include. Measured on a configuration-management consumer: `quality-sonarqube` failed at `mkdir $CI_PROJECT_DIR/.ci-tpl: Permission denied` in a merge-request pipeline because that project's `default: cache:` was restored into a job whose image runs as uid 1000. The same component, image and runner pass in every consumer with no global cache, and a consumer-side `inherit:` override on that one job made it green. An inherited `image`, `before_script`, `after_script`, `cache`, `services`, `artifacts` or `hooks` changes what the job runs or what is on disk before it starts, and none of it is covered by the component's contract, its tests or its recorded evidence. A component whose behaviour depends on a file it never reads has no contract.

**Affected contracts.** Every component in `templates/`. The public interface is unchanged: no input, job name, artifact path or rule moves, so this is not a breaking input change.

**Migration.** A consumer that relied on a global `before_script` or `image` to prepare a component job must move that setup into the component's inputs, or into a bespoke job of its own. A consumer's `default: tags:` and `default: timeout:` keep working, which is what the retained list is for.

Sections not changed: everything but 13.1.

#### 1.0.4 — 2026/09/16, a non-root execution image owns its build directory

**Decision.** Section 13.1's component file shape now requires every job a component emits, hidden jobs included, to set `FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR: 'true'`. The rule is unconditional and does not depend on the image the component pins.

**Reason.** The docker executor's default is `umask 0000`, which makes what the runner helper writes group-writable and lets a job running as any uid use it. What umask cannot do is change the ownership of something a root process already created. Measured on a documentation consumer, across four jobs in two main pipelines: `quality-sonarqube` failed with `java.nio.file.AccessDeniedException: /builds/<project>/.git/objects/4c` whenever it ran in the same runner concurrency slot immediately after `docs-wiki-sync`, which runs as root in the same build directory and writes git objects into it. In a different slot the same configuration is green, which is why 1.0.0-rc.2 looked clean. The same class had already broken the same component in another consumer, there through a cache a root helper restored rather than through a root sibling job, and section 13.1's inheritance rule closed only the cache half of it. A third consumer then failed at `mkdir /builds/<project>/.ci-tpl: Permission denied`, in slot `concurrent-0` after a root job, with the inheritance rule in place and no cache restored: the unwritable thing is the project root itself, not one directory inside it.

GitLab Runner's documented behaviour under this flag is to discover the image's uid and gid and change the ownership of the build directory to them after updating sources, restoring cache and downloading artifacts. Read in the source rather than inferred from that sentence: `executors/docker/docker_command.go` calls `changeFilesOwnership` from `requestBuildContainer`, so the ownership passes once, when the build container is created and therefore after every predefined stage that can write the directory, and before the job's own script. It runs `chown -RP` over `FullProjectDir()` and `TmpProjectDir()`, which is the project root and everything under it, and it returns early without doing anything when the image's user is root. So the flag is not scoped to a clone or a fetch, and on a root image it is a no-op rather than a change worth reasoning about.

The rule is unconditional because the alternative cannot be enforced. `execution-image` is a consumer input on every component, so a component's default image being root says nothing about the image its job runs in, and deciding per component would mean resolving an image against a registry inside a test suite that otherwise runs offline. The flag costs nothing on a root image: the ownership it sets is the ownership the directory already had. Its one precondition is that the execution image carries the POSIX `id` utility, which the runner calls with `-u` and `-g`.

**Affected contracts.** Every component in `templates/`. No input, job name, artifact path or rule moves, so this is not a breaking input change. A consumer supplying an `execution-image` with no `id` utility is the one new failure mode, and no image in the catalogue is such an image.

**Migration.** None.

Sections not changed: everything but 13.1.

#### 1.0.5 — 2026/09/16, an execution image is digest-preferred, not digest-required

**Decision.** Section 12's first bullet becomes: pin an execution image by digest where the estate can serve one, and record architecture and tool versions in the catalogue. A component may accept `name:tag`, and may accept no image at all, when it declares which executor each form is for and the catalogue records the pinning it actually has. Section 1's "never silently substitute a tag for a digest" is unchanged: substituting one silently is still forbidden, and a component whose contract says the image is an input the consumer chooses is not substituting anything.

**Reason.** The digest-only rule assumed every runner is a docker executor with a reachable registry. Work runners in the sites this repository now has to serve are shell executors: there is no container registry they can reach, docker.io is unreachable, and a shell executor ignores `image:` entirely. Requiring a digest there does not make anything reproducible; it makes a pipeline that cannot be written at all, because the `execution-image` regex rejects every value such a consumer could truthfully supply. The three forms and what each is for:

- a digest, for a docker executor pulling through the estate's mirror. Still the default on every component, and still what Renovate tracks.
- `name:tag`, for a docker executor at a site whose internal mirror cannot serve a digest. Weaker, and the contract says so.
- empty, for a shell executor. The job runs on the host's own interpreter and tools and pulls nothing.

GitLab cannot omit a key conditionally, and an empty value is not absence: `image: ''` and `image: {name: ''}` are both rejected with `image name can't be blank`, measured against `the CI Lint API` on 18.9.1-ee. A component offering the third form therefore declares an `executor` input over `[docker, shell]` and extends one of two hidden bases, one carrying `image:` and one carrying none, which is the smallest shape that renders a job with no `image:` key.

The same reasoning removes the hash pin from `runtime/wiki/requirements.txt`. Its hashes covered the source distribution and the two CPython 3.12 wheels, which is every artefact the pinned execution image could resolve. On a shell executor the interpreter is the host's -- EL9 ships 3.9 -- pip resolves the wheel built for that interpreter, and `--require-hashes` fails on precisely the air-gapped hosts it was meant to protect. A version pin holds on every interpreter, and the job installs nothing at all when `import yaml` already works.

**Affected contracts.** `docs-wiki-sync` and the `docs-wiki` composition, which gain the `executor` input and a widened `execution-image` regex. No other component changes: the widened form is opt-in per component, and a component that has not been exercised on a shell executor must keep the digest-only regex rather than claim a capability nobody measured. `.ci/estate.yml` gains no new runner profile, because this estate still has only docker executors; the shell-executor evidence is recorded in `.ci/compatibility.yml` under `runner_behaviour`.

**Migration.** None. `executor` defaults to `docker` and the image defaults stay digest-pinned, so a consumer that changes nothing renders the job it rendered before.

Sections not changed: everything but 12 and the section 1 bullet it qualifies.

## 17. Definition of done

A pipeline is ready for its declared use only when:

- Its composition and all component references resolve at the selected revision.
- Inputs, names, paths and source rules follow this standard.
- Every emitted job belongs to an intentional capability; consumers do not disable unrelated default builders.
- Required artifacts have exactly identified producers and immutable subject identities.
- Required gates cannot be skipped through the graph or hidden by successful error paths.
- Deployment credentials, targets, locks and approvals are explicitly configured for the estate.
- Runtime image/tool dependencies and remote helper code are pinned and accessible.
- Relevant validation gates pass and their limits are reported accurately.
- Existing consumer compatibility is preserved or a reviewed migration is supplied.
- No placeholder endpoints, fabricated digests, unreleased component references or secret values remain in runnable configuration.

## 18. How to make agents use this document

Adopt this file as `docs/gitlab-ci-agent-standard.md` in the shared repository. Add a short instruction to the existing root `AGENTS.md`, preserving unrelated instructions:

> For GitLab pipeline or shared-template work, read `docs/gitlab-ci-agent-standard.md`, the approved `.ci/estate.yml` and `.ci/catalog.yml` at the selected revision before editing. Use the defined layout, component inputs, job names and artifact contracts. Prefer released compositions. Do not invent unavailable components or weaken gates. Validate the changed graph and report execution evidence precisely.

For consumer repositories, provide a pinned local copy/reference of the guide and approved catalogue/profile through your normal repository bootstrap process. A plain Markdown file is not automatically loaded by every agent product; configure that product's repository instruction mechanism once. Do not rely on a remote web link alone being read on every task.

This document is intentionally a reusable repository standard rather than an installed product-specific skill. It can become a skill reference later without changing its contracts.

## 19. Evidence, scope and maintenance

This standard combines the reviewed repository's concrete issues with explicit organisation design decisions. Proposed directory names, metadata files, artifact schemas, default retention windows and task recipes are decisions to adopt, not claims that GitLab provides those features automatically.

The reference YAML was checked against the official documentation and locally parsed during document preparation. Input substitution and composition wiring were checked locally with representative values; this is not GitLab CI Lint. No pipeline was executed against the user's GitLab and no component was published as part of writing this guide.

Useful authoritative references for new capability work:

- [GitLab Components](https://docs.gitlab.com/ci/components/) — layout, reuse, job naming, compatibility and publication.
- [GitLab inputs](https://docs.gitlab.com/ci/inputs/) — typed configuration and forwarding.
- [GitLab includes](https://docs.gitlab.com/ci/yaml/includes/) — repository/ref resolution and merge behaviour.
- [GitLab YAML reference](https://docs.gitlab.com/ci/yaml/) — `needs`, `rules`, `artifacts`, `environment` and other supported keywords.
- [GitLab variables](https://docs.gitlab.com/ci/variables/) — precedence and runtime configuration.
- [GitLab job artifacts](https://docs.gitlab.com/ci/jobs/job_artifacts/) — selection, retention and access.
- [GitLab SARIF](https://docs.gitlab.com/user/application_security/detect/sarif/) — format support, versions and ingestion limitations.
- [GitLab deployment approvals](https://docs.gitlab.com/ci/environments/deployment_approvals/) — actual environment approval configuration.
- [Terraform plan](https://developer.hashicorp.com/terraform/cli/commands/plan) — saved plans and sensitive data.
- [Terraform automation](https://developer.hashicorp.com/terraform/tutorials/automation/automate-terraform) — plan/apply automation.

Reverify the affected reference when changing a compatibility boundary; record the result in `.ci/compatibility.yml` so subsequent agents reuse evidence instead of repeating discovery.
