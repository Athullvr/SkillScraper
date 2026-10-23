"""Console bootstrap that gives incomplete installs a useful error."""

from __future__ import annotations

import sys


def cli_main() -> None:
    """Load and run the CLI after the console script has started."""
    try:
        from skillscout.cli import app
    except ModuleNotFoundError:
        print(
            "SkillScout's CLI dependencies are missing. Reinstall its wheel without "
            "--no-deps so the declared dependencies are installed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    app()
