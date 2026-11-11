"""Accept completed findings in CI without hiding analyzer failures."""

import sys

from .cli import main as analyze


def main(argv: list[str] | None = None) -> int:
    status = analyze(argv)
    return 0 if status == 1 else status


if __name__ == "__main__":
    sys.exit(main())
