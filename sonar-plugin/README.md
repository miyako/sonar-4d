# 4D Community Plugin for SonarQube

Native Java **language-registration foundation**, targeting **SonarQube Server
2025.1**. It registers language key `fourd`, display name `4D`, and file suffix
`4dm` (matching `.4dm` files). This community project is not an official
SonarSource or 4D product.

## Scope

- Registers the language and the `sonar.fourd.file.suffixes` setting, available
  globally and per project. The default is `4dm`; a comma-separated override
  uses suffixes **without leading dots**, for example `4dm,custom4d`. Empty or
  blank-only values fall back to `4dm`. Sonar's suffix matching is
  case-insensitive.
- Uses the API's default `publishAllFiles() = true`, so recognized source files
  can be published even without sensor data.
- Does **not** implement a sensor, parser, native analysis rules, quality
  profiles, syntax highlighting, metrics, or execution of an external analyzer.
- Does **not** implement or replace generic issue import. External reports use
  SonarQube's built-in `sonar.externalIssuesReportPaths` processing. External
  rules are managed by the producing analyzer, not by Sonar quality profiles.

Generic issue import itself does not require this plugin. The plugin's purpose
is explicit 4D language registration, not an alternative report format.

## Compatibility contract and evidence

| Component | Pinned value / supported target |
| --- | --- |
| Server target | SonarQube **Server 2025.1** |
| Compile-time and minimum runtime Plugin API | `org.sonarsource.api.plugin:sonar-plugin-api:11.1.0.2693` (`provided`) |
| Packaging extension | `org.sonarsource.sonar-packaging-maven-plugin:sonar-packaging-maven-plugin:1.23.0.740` |
| Java bytecode / minimum plugin runtime | Java 17 (`maven.compiler.release=17`, class-file major 61) |
| Build tooling used for verification | Apache Maven 3.9.9 on Temurin JDK 18.0.1 |

This is a declared **target baseline**, not a claim of tested deployment or
SonarSource certification. Earlier servers, later servers, SonarQube Community
Build, SonarQube Cloud, and SonarQube for IDE are not validated or claimed as
supported here. Server 2025.1 is not the latest release; its versioned docs now
state that it is no longer patched. Choose a maintained server for production
and revalidate before broadening this target.

Official evidence:

1. [SonarSource's 2025.1 plugin basics](https://docs.sonarsource.com/sonarqube-server/2025.1/extension-guide/developing-a-plugin/plugin-basics)
   explains that Plugin API releases have been independent of server releases
   since 9.5, and that `pluginApiMinVersion` writes the minimum **API** version
   into `Sonar-Version`. Do not put `2025.1` in that manifest field.
2. [SonarSource staff confirmation, January 29, 2025](https://community.sonarsource.com/t/api-version-of-sonarqube-server-2025-1-lta/134022/4)
   identifies **11.1.0.2693** as the API included in Server 2025.1.
   The [versioned SonarSource source for Community Build 25.2](https://github.com/SonarSource/sonarqube/blob/25.2.0.102705/gradle.properties)
   also pins that API. This corroboration is not a claim that Community Build
   25.2 and Server 2025.1 are the same product.
3. [Pinned API language contract](https://github.com/SonarSource/sonar-plugin-api/blob/11.1.0.2693/plugin-api/src/main/java/org/sonar/api/resources/Language.java)
   documents suffix matching and `publishAllFiles`; the
   [property definition API](https://github.com/SonarSource/sonar-plugin-api/blob/11.1.0.2693/plugin-api/src/main/java/org/sonar/api/config/PropertyDefinition.java)
   provides the configuration scopes used here.
4. [Pinned packaging implementation](https://github.com/SonarSource/sonar-packaging-maven-plugin/tree/1.23.0.740)
   defines the plugin manifest, including `Jre-Min-Version`. The published
   [packaging artifact](https://repo.maven.apache.org/maven2/org/sonarsource/sonar-packaging-maven-plugin/sonar-packaging-maven-plugin/1.23.0.740/)
   and [API artifact](https://repo.maven.apache.org/maven2/org/sonarsource/api/plugin/sonar-plugin-api/11.1.0.2693/)
   are the pinned build dependencies, not versions inferred from latest docs.
5. [2025.1 generic issue format and import parameter](https://docs.sonarsource.com/sonarqube-server/2025.1/analyzing-source-code/importing-external-issues/generic-issue-import-format)
   documents the built-in importer and the absence of external rules from
   native rules pages and quality profiles.

## Build and verification

With Maven 3.9.9 and a JDK supporting `--release 17`, from this directory:

```sh
mvn -B -ntp clean verify
```

Artifact: `target/sonar-fourd-plugin-0.1.0-SNAPSHOT.jar`.

The unit tests cover language identity, default/configured/blank suffixes,
language publication defaults, and the exact registration/property scope.
The Failsafe `PluginPackageIT` checks the **built JAR**, its entry point, display
name, plugin/API/JRE manifest metadata, Java 17 bytecode, and absence of bundled
API/test libraries. Despite its `IT` suffix, it is a packaging test, **not** a
running-server integration test. Run `verify`, not just `test`, to include it.

Local result (2026-11-11): `clean verify` passed, with **6 unit tests and 1
packaging test**, no failures or skipped tests. The generated manifest contains
`Sonar-Version: 11.1.0.2693` and `Jre-Min-Version: 17`; both production classes
were checked for Java 17 bytecode.

For the local verification, `mvn` was missing from PATH. Maven 3.9.9 was then
downloaded from Maven Central and its archive SHA-512 verified. Tooling and the
dependency cache are isolated under ignored `.build-tools/`; no global Maven
installation is required or changed. From the repository root, the equivalent
command using that local download is:

```sh
sonar-plugin/.build-tools/apache-maven-3.9.9/bin/mvn -B -ntp \
  -Dmaven.repo.local=sonar-plugin/.build-tools/repository \
  -f sonar-plugin/pom.xml clean verify
```

## Manual deployment and scanner use (not yet verified)

No SonarQube server was started for this change. Server startup, installation,
scanner language recognition, source publication, generic issue ingestion, and
UI display remain **unverified**. A successful Maven build does not verify
these behaviors. Before relying on the plugin:

1. Provision a disposable **SonarQube Server 2025.1** instance using that server
   release's supported runtime and database prerequisites. The local JDK 18
   build runtime is **not** a server runtime recommendation.
2. Stop the instance, copy the built JAR into its `extensions/plugins/`
   directory, remove any older copy of this plugin, and restart. Check
   `logs/web.log` for successful plugin deployment and the administration UI
   for **4D Community Plugin for SonarQube**.
3. Scan a project with an indexed `.4dm` file and an independently generated
   report in the documented **2025.1 generic issue format**. For example, with
   authentication and server URL configured separately:

   ```sh
   sonar-scanner \
     -Dsonar.projectKey=fourd-smoke \
     -Dsonar.sources=src \
     -Dsonar.externalIssuesReportPaths=reports/sonar-external-issues.json
   ```

   Run from the scanned project's base directory, not this plugin's source
   directory. Reports must refer to existing files within the indexed source
   scope, using paths resolvable from the project base directory (or absolute
   paths). Do not use the removed `sonar.language` parameter. Do not configure a
   custom plugin report-path property: there is none.
4. Confirm the scanner recognizes the file as `fourd`, analysis finishes without
   a native quality profile supplied by this plugin, and the imported external
   issue is attached to the expected source file/location. Check both scanner
   and server logs. If a server requires additional extensions to analyze this
   registration-only language, record that failure rather than assuming native
   rule/profile support.

Installation follows SonarSource's
[cold-deploy instructions](https://docs.sonarsource.com/sonarqube-server/2025.1/extension-guide/developing-a-plugin/plugin-basics#deploy).
No Marketplace publication, server compatibility matrix, or end-to-end analyzer
integration is implied by this foundation.
