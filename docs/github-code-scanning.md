# GitHub code scanning

The GitHub integration is independent of SonarQube. It consists of a reusable
root composite action, a normal custom-rule workflow and a fixture workflow
that verifies GitHub's actual processed analysis and source-mapped alerts.

## Use in a 4D repository

Copy the workflow example from the root README, replacing `@main` with a
reviewed commit SHA. `actions/checkout` must first check out your source at
the default workspace root. The action runs its own analyzer code from the
action installation directory, not from a similarly named package in your
project. It sets up Python 3.12 and needs no pip dependencies or Sonar server.

GitHub code scanning is available for public repositories. Private/internal
repositories require the appropriate GitHub Code Security entitlement and
repository setting. The workflow token needs `security-events: write`,
`contents: read` and, for the uploader in private repositories, `actions: read`.
No personal token, secret or CodeQL extractor is required. GitHub's repository
and pull-request token policies still apply; permission failures fail visibly.

Default analysis selects only `4D-M001`, a physical source-file length rule.
These are maintainability alerts displayed in GitHub's code-scanning UI,
**not detected vulnerabilities or a security certification**. The official
CodeQL action is used only to upload SARIF.

Optional inputs:

| Input | Meaning |
|---|---|
| `config` | JSON configuration path relative to the checked-out repository root |
| `category` | Stable, unique analysis identity; defaults to `sonar-4d` |
| `native-tool` | Optional tool4d executable already installed on the runner |
| `download-tool4d` | `"true"` downloads/verifies the pinned public tool4d on macOS ARM64; defaults to `"false"` |
| `project` | Repository-relative `.4DProject`, required with either native mode |
| `native-timeout` | Native observation deadline, default 15 seconds |
| `leave-unopened` | Optional newline-separated repository-relative source paths for native coverage experiments |

Outputs are `sarif-file`, `analysis-file` (runner-local absolute paths) and
`sarif-id` (GitHub's upload identifier). Reports use a fresh directory beneath
`RUNNER_TEMP`, outside the source tree. You can save the two report outputs as
workflow artifacts if desired; they contain source-derived information.

Analyze the full repository root, not just `Project/Sources` or a fixture
directory. Exclude unwanted `.4dm` paths through the normal analyzer config.
Otherwise a report path such as `Long.4dm` would not identify
`tests/fixtures/rules/positive/Long.4dm` in GitHub. Paths containing spaces,
`#` or non-ASCII characters are percent-encoded in SARIF and decoded by GitHub.
The uploader calculates source fingerprints from the checkout.

Keep categories stable between commits so GitHub can track and resolve alerts.
Give independent custom/native or monorepo scopes different categories;
reusing one category for different scopes can replace another scope's results.

## Completion and findings policy

The action invokes `python -m sonar4d.ci`, a thin adapter around the unchanged
normal CLI:

| Analyzer result | Normal CLI | CI adapter | Upload |
|---|---|---|---|
| Complete, no findings | 0 | 0 | Yes, including empty results to resolve old alerts |
| Complete, findings | 1 | 0 | Yes; findings must not suppress the report |
| Failure or incomplete analysis | 2 | 2 | No; the analysis step fails |

Only the completed-findings status `1` is normalized. Other failure statuses
are propagated. There is no blanket `|| true`, `continue-on-error` or
always-upload fallback in the action. Incomplete native results cannot clear
previous GitHub alerts. The uploader waits for GitHub processing; rejected
uploads fail the job. A green workflow means completed analysis and accepted
upload, not an absence of findings. Configure your alert/review policy separately.

## Repository workflows and live verification

`.github/workflows/code-scanning.yml` scans this repository on pushes to
`main`, pull requests and manual dispatch, using the default custom rule.
Its `.4dm` files are short fixtures, so this scope normally has no alerts.

`.github/workflows/code-scanning-fixture.yml` runs for relevant feature-branch
pushes, or by manual dispatch once the workflow is on the default branch.
It uses `.github/sonar4d-fixture.json` with `maxLines: 3` and the distinct
`sonar-4d-fixture` category. Two intentionally four-line files must create
`4D-M001` alerts on physical line 1:

* `tests/fixtures/rules/positive/Long.4dm`
* `tests/fixtures/github/space #é.4dm`

The fixture job is not an automatic default-branch quality policy. Its
intentional findings are isolated from the normal category. Manually running
it on `main` deliberately creates those demonstration alerts on `main`.

After upload, `scripts/verify_code_scanning.py` checks GitHub's SARIF processing
status, the exact upload identifier, analysis commit/ref, result count and
actual open alerts with the expected rule, category, paths and source line.
The job fails if uploads are merely queued, alerts are absent/mislocated, or
the API returns another commit's analysis. Verification evidence is retained
in the `sonar4d-github-evidence` workflow artifact and alert links are written
to the job summary. The verifier is fixture-specific, not required in consumer
workflows. Unit contracts also cover negative verification cases.

The live upload was verified on 2026-11-11 in
[fixture workflow run 43032734486](https://github.com/miyako/sonar-4d/actions/runs/43032734486).
GitHub completed analysis `2144284763` for commit
`a65320a1f1b85579d91bf4aa916f7cb9f9c15cfa`, with exactly two open alerts:
[the ordinary path](https://github.com/miyako/sonar-4d/security/code-scanning/2)
and [the space/Unicode/fragment-character path](https://github.com/miyako/sonar-4d/security/code-scanning/1),
both mapped to line 1. This is a feature-branch custom fixture verification,
not a claim that the default branch contains vulnerabilities. The independent
native Mac verification is recorded below.

## Native checks in GitHub Actions

Custom analysis works on GitHub's Ubuntu runners. Native syntax checking uses
the verified macOS ARM64 runtime. **tool4d can be downloaded publicly without
login, secrets or license activation**; neither manual installation nor a
self-hosted Mac is required. GitHub's explicit `macos-15` label supplies an
Apple Silicon hosted runner; `macos-15-intel` is a different architecture.

For a trusted project, use this native job instead of the Ubuntu-only example:

```yaml
name: Native 4D code scanning
on: [push, workflow_dispatch]
jobs:
  analyze:
    runs-on: macos-15
    permissions:
      contents: read
      actions: read
      security-events: write
    steps:
      - uses: actions/checkout@v6
        with:
          persist-credentials: false
      - uses: miyako/sonar-4d@main # Pin a reviewed commit SHA for production.
        with:
          download-tool4d: "true"
          project: Project/YourProject.4DProject
          # config: .github/sonar4d.json
          native-timeout: "60"
          category: sonar-4d-native
```

Alternatively, supply `native-tool` for an already installed, verified
executable. `native-tool` and `download-tool4d: "true"` are mutually exclusive.
Both require `project`. Keep the repository-root source scope consistent with
that project: exclude unrelated projects through config, or full-response
coverage will correctly fail incomplete. Native diagnostics and enabled custom
rules are both included. Download mode remains opt-in.

### Public download verification

The separate `python -m sonar4d.provision --destination NEW_DIRECTORY` helper
uses the public [tool4d download convention](https://github.com/miyako/skills/blob/main/4d-skills/skills/4dcli/SKILL.md#obtaining-tool4d).
It downloads only this reviewed runtime:

| Pin | Value |
|---|---|
| URL | `https://resources-download.4d.com/release/21.x/21.0/latest/mac/tool4d_arm64.tar.xz` |
| SHA-256 | `4a7ef4b26091bf77761956f9b939c83f441902ce77c25952e0eda18a36497b07` |
| CLI version | `21.0 build 21.100422` |
| Apple signing team / identifier | `37UG5W39Z2` / `com.4D.tool` |

This is a repository-reviewed digest of the officially signed download, **not
a claim that 4D publishes that checksum**. Because the URL contains `latest`,
an upstream replacement intentionally fails the digest check. Review its
signature, build and real fixture behavior before updating pins; never simply
accept whatever `latest` returns.

Downloads are HTTPS-only, bounded and do not load curl configuration or send
authentication. HTTP success alone is insufficient: this service can return
an HTML redirect stub instead of an archive. Digest checking precedes safe,
bounded extraction, deep/strict Apple signature verification with the expected
4D identity, and an exact version check. Unsupported hosts, drift, malformed
archives, signature/version failures and timeouts fail visibly before scanning.
There is no retry, quarantine removal, re-signing or success-shaped fallback.
The helper installs only into a fresh requested directory and records
`provision.json`; the action uses `RUNNER_TEMP`, not global application folders.
An optional `--archive FILE` accepts a cached archive under the same checks.
The helper requires macOS ARM64, Python with safe tar data filters
(the action supplies Python 3.12), `curl` and `codesign`.

Public Linux/Windows archives exist too; this implementation does not
automatically install them because their native completion/location contract
has not been verified. This is a validation limit, not a login requirement.

### Hosted native fixture workflow

`.github/workflows/native-code-scanning.yml` runs relevant feature-branch
pushes and manual dispatch, not a default-branch quality policy. Fresh hosted
Mac jobs download the public runtime independently. The error scope requires
five `4D-N001` findings, including the cross-file type mismatch and syntax
errors in a file deliberately never opened with LSP `didOpen`. The clean
scope requires an explicit complete empty report. Separate categories
`sonar-4d-native-errors` and `sonar-4d-native-clean` prevent scope replacement.

After uploading, the verifier compares every alert's rule, path, physical
line and message with the complete report, including different diagnostics on
the same line. It verifies the exact processed upload/commit/ref and known
native fixture expectations. A third job requires the actual missing-component
diagnostic, exit `2`, incomplete status and no Sonar report; it never uploads
that partial SARIF. Evidence is retained as workflow artifacts.

The complete hosted path was verified on 2026-11-11 in
[native workflow run 43036548494](https://github.com/miyako/sonar-4d/actions/runs/43036548494),
at commit `b21c128f5ae7807bc570fc52778ec0c16f9210b4`. All three jobs passed
after downloading and verifying the pinned public runtime on `macos-15`:

| Scope | Verified outcome | GitHub analysis |
|---|---|---|
| Native errors | Complete, five source-mapped `4D-N001` alerts | `2144457239` |
| Native clean | Complete, zero findings, processed empty SARIF | `2144457696` |
| Missing dependency | Incomplete, real unknown-component finding at line 2, exit `2`, no Sonar report or upload | None by design |

GitHub preserved the distinct messages sharing a physical source line:
[Caller line 2](https://github.com/miyako/sonar-4d/security/code-scanning/3),
[Opened line 3, missing expression](https://github.com/miyako/sonar-4d/security/code-scanning/4),
[Opened line 3, unmatched If](https://github.com/miyako/sonar-4d/security/code-scanning/5),
[Unopened line 2, missing expression](https://github.com/miyako/sonar-4d/security/code-scanning/6)
and [Unopened line 2, unmatched If](https://github.com/miyako/sonar-4d/security/code-scanning/7).
These are intentional reliability fixtures on the feature branch, not security
vulnerabilities. The native evidence artifacts retain exact SARIF IDs,
analysis IDs, commit/ref and source locations; the job also verifies that the
unopened methods were not sent `didOpen`.

That run predates dependency installation; the missing-dependency job now
expects the `local dependencies are not installed` reason, and a
`github-dependencies` job checks online and offline GitHub installation.

Native build/platform restrictions, dependency blockers and R3 coordinate
limits still apply. The older `21.100301` version-command crash remains
documented; successful newer-build fixtures do not prove it cannot recur.
Never expose a persistent self-hosted Mac or credentials to untrusted fork
pull requests. Disposable hosted runners reduce persistence risks, but
dataless/skip-startup and copying are **not a security sandbox**.

## Official reference

[Uploading third-party SARIF to GitHub](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/integrate-with-existing-tools/upload-sarif-file)

[GitHub-hosted runner labels and architectures](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
