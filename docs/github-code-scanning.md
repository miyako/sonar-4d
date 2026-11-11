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
| `project` | Repository-relative `.4DProject`, required with `native-tool` |
| `native-timeout` | Native observation deadline, default 15 seconds |

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

## Native checks in GitHub Actions

Custom analysis works on GitHub's Ubuntu runners. Native analysis still
requires the supported tool4d executable on a compatible Apple Silicon Mac
runner. This action does **not** download tool4d, install a runner service or
provision licenses. Hosted Ubuntu does not become a native 4D runner simply
because it can upload SARIF.

On an appropriately provisioned **trusted** runner, add `native-tool` and
`project`, and use a separate category such as `sonar-4d-native`. Keep the
repository-root source scope consistent with the chosen project; exclude
unrelated projects through config or the full-response coverage check will
correctly fail incomplete.

Native build/platform restrictions, dependency blockers, R3 coordinate limits
and the intermittent tool4d version-command crash still apply. The live
GitHub fixture workflow exercises custom analysis, not a native Mac runner.
Native job provisioning remains separate from the verified upload mechanism.
Never expose a persistent self-hosted Mac or credentials to untrusted fork
pull requests. Dataless/skip-startup and copying are not a security sandbox.

## Official reference

[Uploading third-party SARIF to GitHub](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/integrate-with-existing-tools/upload-sarif-file)
