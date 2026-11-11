# Native diagnostic investigation

## Current supported contract

The working native syntax backend is pinned to **tool4d 21.0 build
21.100301 on macOS ARM64**. It uses the official custom `experimental/checkSyntax` workspace
response, not standard `workspace/diagnostic` or an LSP quiet period.
Completion requires a valid full document report for **every selected file**,
valid locations, no duplicate reports, and no unvalidated declared,
component or native-plugin dependencies. A missing file, null workspace
response, unsupported build or deadline is incomplete, exits `2`, and cannot
produce a Sonar generic report.

The final fixture has been exercised against actual tool4d. On the pinned
21.0 build, it returns five diagnostics: two syntax errors each in the opened
and unopened malformed methods, and a cross-file Text-to-Integer mismatch in
`Caller.4dm`. `Callee.4dm` is explicitly present with zero diagnostics.
`Caller` and `Unopened` are correctly located on physical line 2. A separate
`native-clean` fixture returns an explicit full empty document report and
therefore establishes a genuine complete zero-finding result.

**Root cause of the initial null responses:** macOS temporary paths were
spelled `/var/...`, while tool4d resolves them to `/private/var/...`. Resolving
the temporary workspace before constructing project/file URIs makes the
files indexable. The historical null-only observations below were made before
this fix, not evidence that native checking is unavailable.

R3 also produces the full workspace response after this fix, including
unopened and cross-file errors. However, the opened-document locations in
this fixture were shifted: the type mismatch was reported on physical line
1 rather than line 2. **R3 is therefore not a verified reporting build**.
Its observations remain raw-only; no native findings are promoted to the
common model for that build, and the run stays incomplete.

Native syntax diagnostics use `2147483647` as a whole-line end-column
sentinel; R3 also returned `-1` for the start column. The model explicitly
records `location_precision:"line"` for this known pattern and omits columns
in SARIF/Sonar rather than inventing or clamping a token range. Other invalid
positions fail validation. Native token-precise ranges are preserved with
UTF-16 semantics when valid. Raw protocol ranges remain in the observation
file. This is native syntax analysis, not a claim of exhaustive compiler,
dependency, security or instrumentation coverage.

## Reproducible scope

Observed locally on macOS/Apple Silicon on 2026-11-11:

| tool4d CLI version | LSP serverInfo |
|---|---|
| `21.0 build 21.100301` | `4D LSP Server`, version `2100` |
| `21 R3 build 21R3.100164` | `4D LSP Server`, version `2130` |

Both servers advertise `diagnosticProvider` with
`interFileDependencies: true`, `workspaceDiagnostics: false`, and
`textDocumentSync` with `openClose: true`, `change: 2`, `save: true`,
`willSave: true`. No alternate position encoding was returned, so the LSP
UTF-16 default applies. **The absence of standard workspace diagnostics does
not imply the absence of project-wide checking.**

`tests/fixtures/native` is a small package with four source files:

| File | Purpose | didOpen in the documented experiment |
|---|---|---|
| `Opened.4dm` | Unknown method reference and malformed `If (` | Yes |
| `Caller.4dm` | Calls a typed integer parameter with text | Yes |
| `Callee.4dm` | Declares the typed cross-file target | No |
| `Unopened.4dm` | Malformed `If (` in an unopened file | No |

The final integration tests assert the known error stimuli and selected scope.
Historically, the first bounded 8-12 second sessions initialized and returned
`result: null` for all four advertised `textDocument/diagnostic` requests;
no `textDocument/publishDiagnostics` arrived. This demonstrates a diagnostic
integration gap, not four clean files or proof of a checker defect.

## Official client investigation

Inspected the public official
[4d/4D-Analyzer-VSCode at 6115f352c7e4c4fb6d1a569ded91f4e377fac92a](https://github.com/4d/4D-Analyzer-VSCode/tree/6115f352c7e4c4fb6d1a569ded91f4e377fac92a):

* `editor/src/managers/LanguageServerManager.ts`: reverse TCP, `--lsp`, language
  ID `4d`, initialization options containing diagnostics configuration and
  dependency support. Its default launch does not pass `--project`.
* `editor/package.json`: diagnostics enabled by default, default scope
  `Workspace`; `Document` is also supported.
* `editor/src/lsp_ext.ts`, `commands.ts`: the custom
  `experimental/checkSyntax` request accepts `{uri: activeDocumentUri}` and
  expects a `WorkspaceDiagnosticReport` containing `items`. The UI offers it
  when automatic workspace diagnostics are not already running.
* `editor/src/managers/DependencyManager.ts`: `experimental/databaseInitialize`
  returns `{valid, id}`; `experimental/prepareComponents` is followed by
  component-install notifications. Fetching dependencies uses separate
  authenticated package-management code, which is **not reused**.
* `editor/src/config.ts`: `experimental/didChangeActiveTextEditor` notification
  and diagnostics settings synchronization.

The executable probe now uses the following flow, all inside its deadline:

1. Copy the package and launch
   `tool4d --project=<copy>/Project/Probe.4DProject --lsp=<loopback-port> --dataless --skip-onstartup`.
2. `initialize`, with repository `rootUri`, legacy `rootPath`,
   `workspaceFolders`, UTF-16 support, push diagnostic support,
   `initializationOptions.diagnostics={enable:true,scope:"Document"}` and
   `dependencies={enable:false}`.
3. `initialized`; request `experimental/databaseInitialize` for the project.
   On `valid:true`, send `experimental/prepareComponents` with the returned ID.
4. Wait for `dependency/installComponents/done`. If a before-install notification
   arrives, acknowledge `dependency/installComponents`. A `dependency/fetch`
   notification fails explicitly; no remote fetching or credentials.
5. On 21.0, `experimental/databaseInitialize` returns MethodNotFound; record
   that explicitly and check the already CLI-launched project instead. No
   fabricated readiness or diagnostic result is substituted.
6. Send `textDocument/didOpen` for selected files, the active-editor notification,
   `experimental/checkSyntax` for an opened source,
   `experimental/filesStatus`, and advertised `textDocument/diagnostic`
   requests for selected `.4dm` files (case-insensitive), including unopened ones.
7. Validate full workspace response coverage and positions, then finish at
   that response boundary. If no valid response arrives, collect until the
   deadline and fail incomplete. Close TCP and terminate/reap the owned process.
   No quiet interval or per-document response establishes workspace completion.

Historical R3 observations after adding preparation but **before canonical URI
resolution**:

| Operation | Observed result |
|---|---|
| `experimental/databaseInitialize` | `{valid:true,id:{uri:<copied .4DProject>}}` |
| `experimental/prepareComponents` | `dependency/installComponents/done` arrived |
| `experimental/checkSyntax` | `result:null`, not a workspace report |
| `experimental/filesStatus` | `result:[]` |
| Four `textDocument/diagnostic` requests | Each `result:null` |
| Push diagnostics | None observed during the bounded session |

An intermediate run with explicit diagnostics options and `checkSyntax` also
returned null on both 21.0 and R3. A bounded R3 comparison without `--project`,
matching the official client's launch shape, likewise returned null. That
comparison preceded the added component-ready handshake; it is not evidence
that every combination of official-client behavior has been exhausted.
No `didChange`/`didSave` mutation or automatic Workspace diagnostic loop is
implemented. The copy is a fresh, immutable source snapshot, so no incremental
diagnostic cache is trusted. The full custom response must name every selected
file, including clean and unopened files. This is a conservative selected-scope
contract; any omitted file fails closed. Null/unchanged standard pull responses
remain raw evidence and never become a synthetic clean result.

## Missing dependencies and version behavior

`tests/fixtures/missing-dependency` declares a deliberately absent local
component in `dependencies.json` and references it from a method. Use the same
CLI with `--project Project/MissingDependency.4DProject` and a fresh output path.
Dependency fetching is disabled; the contract server tests verify that an
explicit fetch request becomes a failure. The foundation does **not** resolve,
certify or automatically install dependencies. Native behavior for an absent
dependency is not yet a supported diagnostic contract, even if the server
reports that component preparation is done.

A historical eight-second R3 run before canonical URI resolution returned
`valid:true`, a component-install-done notification, `filesStatus:[]` and null
syntax/pull results, just like the main fixture. No push diagnostics or
stdout/stderr messages were observed. It exited `2` and produced no Sonar
report. Component-install-done is never used as proof that required
components exist or that the project was checked.

A final canonical-path run on the pinned 21.0 build returned a full report with
`The class cs.AbsentComponent is unknown.` at physical line 2. The declared
dependency still blocked completeness with
`Declared 4D dependencies are not validated by this foundation`; the run
retained that partial finding, exited `2` and produced no Sonar report.
An unresolved-class diagnostic is not evidence that dependency installation
or resolution has succeeded.

The runner checks `tool4d --version` under a timeout and refuses unparseable
versions and major versions below 21. Tests cover a reported 20.6 version,
missing executable, early exit, initialization error, malformed framing,
disconnect, foreign diagnostic URI, no connection, null response and deadline.
Only the two exact versions above were exercised with actual tool4d; Windows,
Linux, other 4D versions and real missing runtime libraries are unverified.

Repeated launches on this host also exposed an intermittent SIGSEGV in the
21.0 `--version` command itself. The runner records that actual failure and
returns incomplete/exit `2`; it does not retry or pretend the project is clean.
The failed observations were retained separately from successful native
fixture runs. This proprietary-runtime instability can fail the optional
native integration tests and remains an explicit production-readiness gap.

The deadline applies to TCP startup/observation after version probing and
copying; version probing has its own bounded timeout and process cleanup may
take an additional three seconds. Operational output records the owned
process's exit code and the final 16 KiB of stdout/stderr. These records may
contain source-derived information; they remain local unless deliberately
shared.

## Reuse decisions

The MIT-licensed Zed adapter at
[`383b4611e8fe805cff76311230834f2a16bcdac0`](https://github.com/miyako/language-4dm-zed/tree/383b4611e8fe805cff76311230834f2a16bcdac0)
documents and implements the reverse-TCP launch pattern. Its
`bridge/src/relay.rs` rewrites a matching null pull-diagnostic response to
`{kind:"full",items:[]}` for editor compatibility. That repair is unsuitable
as evidence for batch analysis and is deliberately **not** reused. The Python
runner accepts tool4d's compact `Content-Length:` framing but preserves null.

The MIT-licensed Nova grammar at
[`63052fea3a2eb9583a9cbdc1c3ffaebc7bd19a43`](https://github.com/miyako/language-4dm-nova/tree/63052fea3a2eb9583a9cbdc1c3ffaebc7bd19a43)
identifies itself as a 4D 21 R4 structural skeleton with unfinished statements
and types. Adding that parser would not solve native batch completion; this
foundation avoids vendoring it or promising full-language/security coverage.
No source from either project is incorporated.
