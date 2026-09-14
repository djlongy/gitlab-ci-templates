# SAST findings in SonarQube on the free tier

GitLab Free runs the SAST analyzers but renders nothing: the security widget,
the vulnerability report and the security dashboard are all Ultimate. The only
in-MR surface Free has is the Code Quality widget, and it is degradation-only —
it compares the branch against the target and shows just what changed, so a
finding that predates the scanner is invisible forever.

SonarQube Community Build fills that gap. Its scanner imports SARIF from any
tool via `sonar.sarifReportPaths`, and imported findings become first-class
SonarQube issues. No plugin, no extra service.

Verified against Community Build **26.4** on **2026/09/09**.

## What the import actually gives you

| | Result |
|---|---|
| Issue identity | rule `external_<tool>:<rule-id>`, e.g. `external_Semgrep OSS:python.lang.security…` |
| Counted in measures | Yes — `vulnerabilities`, `security_rating` |
| Fails a quality gate | Yes — verified `security_rating` and `vulnerabilities` conditions both firing |
| Shows in trends | Yes — `api/measures/search_history`, and the project Activity graph |
| Deduplicated across runs | Yes — external issues go through the same issue tracker as native ones |
| Rules page / quality profiles | **No** — external rules cannot be activated, deactivated or edited |

## Wiring

`security/sonarqube.yml` pulls the SARIF artifacts of `semgrep` and `trivy-fs`
via `needs: [..., optional: true]`, auto-discovers every `*.sarif` in the
workspace, and appends `-Dsonar.sarifReportPaths`. Repos that include
`sonarqube.yml` on its own keep working — the needs are optional, and with no
SARIF present the job runs SonarQube's own analysis exactly as before.

Nothing to change in a consumer repo. Including `pipelines/devsecops.yml`, or
`security/sonarqube.yml` plus either scanner, is enough — **unless the repo
overrides the sonarqube job's `needs:`**. GitLab replaces `needs:` rather than
merging it, so an override drops the scanner artifacts and the job silently
falls back to SonarQube's own analysis. A repo that overrides `needs:` for a
coverage artifact must re-add the scanners:

```yaml
sonarqube:
  needs:
    - job: go-test          # the repo's own addition
      artifacts: true
    - job: semgrep          # keep these two
      artifacts: true
      optional: true
    - job: trivy-fs
      artifacts: true
      optional: true
```

The job prints a hint when it finds no SARIF, so this shows up in the log
rather than as an unexplained empty dashboard.

Opt-in variables:

- `SONAR_SARIF_PATHS` — explicit comma-separated list instead of auto-discovery.
  Files that do not exist are dropped rather than failing the job.
- `SONAR_QUALITYGATE_WAIT=true` — the job blocks on the gate result. Pair it
  with `allow_failure: false` in the consumer repo to make the gate binding.

## Gotchas, each one paid for

**A space anywhere in the path loses the finding, silently.** SonarQube's SARIF
importer cannot resolve a path containing a space. A raw space (what Semgrep
emits) is dropped outright; the spec-correct `%20` is accepted but attaches the
issue to the project root with no file and no line. Neither form logs an error.
This is not theoretical: scanning `genpass` produced 14 Semgrep findings and
SonarQube imported 3, because 11 sat under `genpass Design System/`. The job now
counts this ahead of the scan and prints `N will be dropped: space in path`.
The only real fix is to rename the directory.

**Everything imported from SARIF becomes a Vulnerability.** SonarQube assigns
the `SECURITY` software quality to every SARIF issue; there is no way to make
one a Bug or a Code Smell. So a noisy ruleset drives the Security Rating down —
a single finding took a probe project to rating D. If you want a tool's output
typed properly, use the generic issue import (`sonar.externalIssuesReportPaths`)
or a native importer instead; both are Community Build features:

| Tool | Better route than SARIF |
|---|---|
| gosec | `gosec -fmt=sonarqube` → `sonar.externalIssuesReportPaths` |
| bandit | `sonar.python.bandit.reportPaths` |
| ruff | `sonar.python.ruff.reportPaths` |
| golangci-lint | `--out-format checkstyle` → `sonar.go.golangci-lint.reportPaths` |
| hadolint | `sonar.docker.hadolint.reportPaths` |
| tflint | `sonar.terraform.tflint.reportPaths` |

**Severity must be on the rule, not the result.** In MQR mode SonarQube ignores
`results[].level` and reads `tool.driver.rules[].defaultConfiguration.level`.
Trivy puts the level on the result, which is why its findings all land as MINOR
regardless of the CVE severity.

**A finding on a file SonarQube did not index is dropped.** Keep `SONAR_SOURCES`
wide enough to cover everything the scanners look at. The scanner logs
`External issues ignored for N unknown files` when this happens — that line is
the alarm.

**`find` and `jq` are not in `sonar-scanner-cli:11`.** `python3` is. The
discovery and preflight step is written in Python for that reason; do not
rewrite it with `find`.

## Making it actionable

The default `Sonar way` gate is entirely new-code conditions. Community Build
analyses mainline only, so on a repo that already carries findings, new-code
conditions stay green while the backlog sits there untouched. A gate with
overall-code conditions is what surfaces standing debt.

Create a gate (for example `Strict code hygiene`) with the Sonar way
conditions plus `vulnerabilities > 0` and `security_rating > A`. To adopt it for one project:

```bash
curl -u "$SQ_TOKEN:" -X POST https://sonarqube.example.com/api/qualitygates/select \
  -d 'gateName=Strict code hygiene&projectKey=<namespace>:<name>'
```

Start with one repo. `vulnerabilities > 0` is strict, and every SARIF finding
counts toward it.

## Cross-project view

Community Build has no portfolio feature, but
`https://sonarqube.example.com/projects` lists every project with its gate
status, security rating and vulnerability count, and sorts by any of them.
At a few dozen projects that is enough of a cross-project view.

If that stops being enough — you want triage workflow, finding ownership,
false-positive suppression, or one dashboard across scanners — DefectDojo
Community (BSD-3) ingests both SARIF and `gl-sast-report.json` and has real
trend graphs. It costs 7 containers and about 8 GB. Not worth it until the
SonarQube view demonstrably falls short.
