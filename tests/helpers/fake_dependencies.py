"""Stand-in for miyako/4D fetch_dependencies.py: replays SONAR4D_FAKE_DEPENDENCIES as the report."""

import json
import os
from pathlib import Path
import sys


arguments = sys.argv[1:]
lock = (Path(arguments[arguments.index("--project") + 1])
        / f"userPreferences.{arguments[arguments.index('--user') + 1]}" / "dependencies-lock.json")
package = Path(arguments[arguments.index("--project") + 1])
# Like the emulator: environment4d.json in the package folder or the nearest parent folder.
environment = next((folder / "environment4d.json" for folder in [package, *package.parents]
                    if (folder / "environment4d.json").is_file()), None)
log = os.environ.get("SONAR4D_FAKE_DEPENDENCIES_LOG")
if log:
    Path(log).write_text(json.dumps({
        "argv": arguments, "token": os.environ.get("DEPENDENCIES_GITHUB_TOKEN"),
        "githubOutput": "GITHUB_OUTPUT" in os.environ,
        "lock": lock.read_text(encoding="utf-8") if lock.is_file() else None,
        "environment": str(environment) if environment else None,
        "environmentText": environment.read_text(encoding="utf-8") if environment else None,
    }), encoding="utf-8")
if "SONAR4D_FAKE_DEPENDENCIES_LOCK" in os.environ:
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(os.environ["SONAR4D_FAKE_DEPENDENCIES_LOCK"], encoding="utf-8")
code = int(os.environ.get("SONAR4D_FAKE_DEPENDENCIES_EXIT", "0"))
if code == 2:
    print("fetch_dependencies: usage error from fake", file=sys.stderr)
    sys.exit(2)
output = Path(arguments[arguments.index("--output") + 1])
output.write_text(os.environ["SONAR4D_FAKE_DEPENDENCIES"], encoding="utf-8")
sys.exit(code)
