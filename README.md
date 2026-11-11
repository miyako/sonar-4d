# 4D Community Plugin for SonarQube

A bounded, working foundation for local 4D analysis and SonarQube integration.
This is **not a complete compiler analyzer or a security product**.

| Surface | Implemented status |
|---|---|
| Headless Python CLI | Working; Python 3.11+, no runtime dependencies |
| Custom rule `4D-M001` | Configurable physical source-file length limit; maintainability only |
| Common findings model | Repository-relative paths; one-based lines, zero-based UTF-16 columns |
| SARIF 2.1.0 | Deterministic findings, GitHub-compatible relative URIs, explicit failed invocation on incomplete analysis |
| GitHub code scanning | Reusable action, opt-in public tool4d download, and live-verified custom/native hosted workflows |
| Sonar generic JSON | Current rules/issues format; correct CODE_SMELL/MAINTAINABILITY and BUG/RELIABILITY mappings |
| Native SonarQube Java plugin | Registers `fourd` / `.4dm`; built and unit/packaging tested, not server-deployment tested |
| tool4d backend | Response-bound workspace syntax checking on verified tool4d 21.0 builds 21.100301 and 21.100422, macOS ARM64; other builds/dependencies fail closed |

## Run the working analyzer

```sh
python3 -m pip install .
sonar-4d /path/to/YourRepository --output /path/outside/YourRepository/report-001
# Or, without installation, from this checkout:
python3 -m sonar4d /path/to/YourRepository --output /path/to/new-report-directory
```

The root is the repository/package root that Sonar or GitHub will use for
resolving file paths. All `.4dm` files below it (case-insensitive extension)
are in scope except `.git` contents and explicit exclusions. UTF-8, optional
BOM, LF/CRLF/CR source files are supported. Symlinks in scope are refused.
Inputs are only read; output must be a **fresh directory outside the root**.

Exit codes are `0` for complete selected-backend analysis without findings,
`1` for complete analysis with findings, and `2` for failure or incomplete
analysis. A finding exit code is not an analyzer crash. No source files is an
error, not a clean scan.

Outputs:

* `analysis.json`: versioned common model, exact source scope, enabled configuration,
  backend list, completion state, errors and findings.
* `findings.sarif`: SARIF 2.1.0. Incomplete runs set
  `invocations[].executionSuccessful: false` and include error notifications.
* `sonar-issues.json`: generated **only for complete analysis**.
* `native-observation.json`: additional protocol evidence when tool4d is selected.
  This operational record contains temporary URIs and is not a deterministic artifact.

Default analysis selects **only the custom backend**. Its `complete: true`
does not claim native syntax checking, type checking, dependency resolution,
security coverage or complete 4D language parsing.

### Configure the custom rule

Save an explicit JSON configuration and pass `--config /path/to/config.json`:

```json
{
  "rules": {
    "4D-M001": {"enabled": true, "maxLines": 200}
  },
  "exclude": ["**/Generated/*", "Vendor/*"]
}
```

`4D-M001` reports one maintainability finding at line 1 when a `.4dm` source
file has **more than** `maxLines` physical lines (default 200, valid range
1..100000). Blank lines and comments count; a trailing newline does not create
an extra line. CRLF counts as one terminator. This is a transparent source-size
metric, not AST, function-complexity or deep security analysis. It applies to
class and method source files alike. Set `enabled: false` to disable it.
Unknown options and invalid values are errors. Exclusions use Python
`fnmatchcase` against root-relative POSIX paths (`*` can match `/`).

The positive/negative fixtures use a limit of three lines:

```sh
python3 -m sonar4d tests/fixtures/rules/positive \
  --config tests/fixtures/rules/config.json --output /tmp/sonar4d-positive-001
# Expected: exit 1, one 4D-M001 finding.
python3 -m sonar4d tests/fixtures/rules/negative \
  --config tests/fixtures/rules/config.json --output /tmp/sonar4d-negative-001
# Expected: exit 0, no findings.
```

Use fresh output names on repeat runs. Reports never overwrite existing
directories, so a failed scan cannot silently leave a previously clean report
at the output path requested for that run.

## Native tool4d observation

The CLI accepts an explicitly supplied **tool4d executable**, not an `.app`
folder. tool4d is publicly downloadable **without login or license activation**.
The optional provisioner downloads and verifies **21.0 build 21.100422**;
GitHub Actions can do this automatically on a hosted `macos-15` ARM64 runner.
A self-hosted Mac and manual installation are not required.

Native completion and location mapping are verified for **21.0 builds
21.100301 and 21.100422 on macOS ARM64 only**. Other builds/platforms may be observed, but remain incomplete and
their raw diagnostics are not promoted to common findings.
Public Linux/Windows downloads also exist, but those native backends are not
verified here. Project dependency fetching is not implemented.
Repeated native launches exposed an intermittent **SIGSEGV in tool4d
21.0 build 21.100301's own `--version` command** on this host. That is a real runtime failure,
not a clean scan: it exits `2`, preserves the error and withholds Sonar output.
No automatic retry hides it. The newer downloaded build passed the native
fixtures; that does not establish that runtime instability is fixed.

```sh
# From this checkout on macOS ARM64; destination must be new.
python3 -m sonar4d.provision --destination /tmp/sonar4d-tool-001
# Executable: /tmp/sonar4d-tool-001/tool4d.app/Contents/MacOS/tool4d
```

The provisioner verifies a repository-pinned SHA-256, Apple's code-signature
chain/4D team identity and the exact runtime version before installation.
It never removes quarantine, re-signs binaries or installs globally.
See [download pins and native Actions setup](docs/github-code-scanning.md#native-checks-in-github-actions).

```sh
python3 -m sonar4d tests/fixtures/native \
  --project Project/Probe.4DProject \
  --native-tool /tmp/sonar4d-tool-001/tool4d.app/Contents/MacOS/tool4d \
  --native-timeout 15 \
  --leave-unopened Project/Sources/Methods/Unopened.4dm \
  --leave-unopened Project/Sources/Methods/Callee.4dm \
  --output /tmp/sonar4d-native-001
# Expected on the pinned build: exit 1, complete:true, five native findings.
```

The project path is relative to the selected root. The CLI normally sends
`didOpen` for each selected source; `--leave-unopened` allows coverage experiments.
The official client's `experimental/checkSyntax` operation provides the
batch response. A native run is complete only when that response contains a
valid **full document report for every selected source file**, on the verified
build, with no unvalidated declared/component/plugin dependencies. A quiet
period, component-ready notification, per-document response, or null result
never establishes completion. Missing files, malformed ranges, unsupported
builds, dependency uncertainty and timeouts exit `2` and withhold Sonar output.
This is completeness of the selected **syntax-checking scope**, not all possible
compiler/semantic/security checks.

The actual fixture verifies opened errors, an **unopened-file error**, a
**cross-file Text-to-Integer mismatch**, and a clean file. The separate
`tests/fixtures/native-clean` project yields a complete zero-finding result.
Canonicalizing temporary workspace paths is essential on macOS (`/var` versus
`/private/var`); without it, tool4d returned nulls instead of indexing the files.

tool4d **21 R3 build 21R3.100164** also produces workspace diagnostics, but its
opened-document ranges were shifted in this fixture, so this build is
deliberately **not accepted for complete, location-correct reports**. Raw
observations remain available. Native whole-line sentinel columns are exported
as explicitly line-only locations, not invented/clamped columns. See
[exact requests, fixture results and limits](docs/native-analysis.md).

The runner copies the package to a temporary directory, rejects symlinks,
omits common data/cache directories and data-file extensions, and launches the
copy with `--dataless --skip-onstartup`. It binds TCP only on loopback, uses a
bounded deadline (0 < timeout <= 300 seconds), and terminates/reaps its owned
tool4d process. **These flags and a copy are not a security sandbox.** Native
plugins/components, OS preferences and the process environment remain risks.
Use trusted projects and dependencies; analyze untrusted projects only in an
OS-isolated disposable account/container/VM without credentials or network
access. The analyzer itself has no upload or dependency-download path.
The separate optional provisioner retrieves only the public pinned runtime;
it does not transmit project source. Protect reports as source-derived data.

## SonarQube Server integration

The native Java registration plugin targets **SonarQube Server 2025.1 LTA**,
Java 17 bytecode and **Sonar Plugin API 11.1.0.2693**. This pins a foundation
baseline, not a claim that every newer server is supported. Server 2025.1 is
now unpatched according to its documentation; evaluate a supported server and
retest deployment before production use.

Build/install instructions and the compatibility sources are in
[`sonar-plugin/README.md`](sonar-plugin/README.md). Install the built JAR into
the server's `extensions/plugins` directory and restart it. Then generate a
**complete custom-backend report** and invoke your scanner, for example:

```sh
sonar-scanner \
  -Dsonar.projectKey=your-project \
  -Dsonar.projectBaseDir=/path/to/YourRepository \
  -Dsonar.sources=Project/Sources \
  -Dsonar.externalIssuesReportPaths=/path/outside/YourRepository/report-001/sonar-issues.json
```

Use your deployment's normal secure token configuration, not tokens in source
files or command examples. This scanner command is an **unverified deployment
recipe**: no running SonarQube server/scanner was available for an end-to-end
import. Unit and packaged-JAR tests establish language registration, not
successful production ingestion. The plugin registers the language; the
built-in generic importer handles reports. There is **no custom Sonar sensor,
native rule repository or quality-profile support** in this release.

Use generic JSON for native/maintainability findings. Sonar's SARIF importer
classifies imported results as security issues, which would misclassify these
rules. The generic exporter includes both Standard Experience
`type`/`severity` and MQR `impacts`; security mapping exists in the common
exporter but **no security-detection rule is implemented**.

SonarQube Cloud external report import requires **CI-based analysis**, not
automatic analysis. Cloud does not install this server plugin; `.4dm` indexing
and report ingestion there are **not verified or promised**. Do not relabel 4D
files as another language to imply support.

## GitHub code scanning

No SonarQube server is needed. The reusable action analyzes the checked-out
repository and uploads complete SARIF through GitHub's official uploader:

```yaml
name: 4D code scanning
on: [push, pull_request]
jobs:
  analyze:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: read
      security-events: write
    steps:
      - uses: actions/checkout@v6
        with:
          persist-credentials: false
      - uses: miyako/sonar-4d@main
        with:
          category: sonar-4d
          # config: .github/sonar4d.json
```

Pin the action to a reviewed commit SHA for production. The default runs
only the configurable maintainability rule; it does not install tool4d or
perform security detection. For native checks use `runs-on: macos-15`,
`download-tool4d: "true"` and a repository-relative `project` input.
See [GitHub setup, native download requirements,
exit behavior and the live fixture check](docs/github-code-scanning.md).

`findings.sarif` uses repository-relative percent-encoded artifact URIs,
UTF-16 column semantics and one-based SARIF columns. The action always analyzes
the **repository root**, so nested project paths map to the files GitHub knows.
It accepts complete analyses with findings (normal CLI exit `1`) but fails on
incomplete analysis (`2`) **before upload**, preserving previous alerts instead
of replacing them with a false clean result. A successful upload is not a
zero-findings assertion. The standalone analyzer still has no upload behavior;
using the action deliberately shares the source-derived report with GitHub.
`github/codeql-action/upload-sarif` is only an uploader here, not a CodeQL backend.

## Architecture and verification

```text
read-only .4dm files ---> custom source-size rule ---+
                                                   |
isolated tool4d copy ---> TCP LSP observation -------+--> common findings
                                                        |-- SARIF 2.1.0
                                                        |-- Sonar generic JSON (complete only)

SonarQube Server: Java language registration + built-in generic report importer
```

```sh
python3 -m unittest discover -s tests -v
TOOL4D_NATIVE_INTEGRATION=/path/to/tool4d python3 -m unittest discover -s tests -v
mvn --batch-mode --no-transfer-progress -f sonar-plugin/pom.xml clean verify
```

Contracts cover thresholds/configuration, positive and negative fixtures,
determinism, classification, UTF-16/URI locations, empty ranges, TCP framing,
timeouts, process failure, old/missing tool4d, dependency-fetch refusal,
invalid/null diagnostic responses, incomplete exports and read-only input.
The opt-in integration tests exercise actual initialization, isolation,
cross-file/unopened diagnostics, source locations and clean response coverage.
SARIF complete/incomplete and
empty/nonempty variants have also been validated against the published
SARIF 2.1.0 JSON schema. CI runs Python contracts and Java verification without
requiring tool4d. The separate hosted-native workflow downloads the public
runtime without credentials and checks errors, a genuinely clean response,
and fail-closed missing-dependency behavior.

## Scope and next direction

The next substantive gap is exercising the packaged plugin and reports against
a pinned SonarQube server/scanner. Native dependency validation, richer
fixtures and R3 coordinate correctness are additional bounded follow-ups.
A future native
sensor/rule repository would be a separate feature, not a rename of external
issues.

The MIT-licensed [Zed adapter](https://github.com/miyako/language-4dm-zed)
and [Nova Tree-sitter grammar](https://github.com/miyako/language-4dm-nova)
were inspected. The runner follows the established reverse-TCP launch pattern,
but intentionally does not reuse the adapter's `null`-to-empty diagnostic
repair. The Nova grammar identifies itself as a structural skeleton; no
grammar code is vendored and no full parsing claim is made. A future parser
backend can reuse it once its syntax/error coverage is validated.

A separate, optional CodeQL backend could later consume the common findings
model after an appropriate 4D extractor/database design; none exists here.
A SystemWorker-based 4D component is another optional future integration.
No component, native 4D plugin, coverage instrumentation, certification or
compliance guarantee is included.

### References

* [Sonar generic import format, Server 2025.1](https://docs.sonarsource.com/sonarqube-server/2025.1/analyzing-source-code/importing-external-issues/generic-issue-import-format)
* [Sonar SARIF import behavior](https://docs.sonarsource.com/sonarqube-server/2025.1/analyzing-source-code/importing-external-issues/importing-issues-from-sarif-reports)
* [SonarQube Cloud external report limitations](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/importing-external-issues/external-analyzer-reports)
* [4D command-line options](https://developer.4d.com/docs/Admin/cli)
* [4D project layout](https://developer.4d.com/docs/21/Project/architecture)
