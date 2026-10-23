"""Console entry point for SkillScout's stdio MCP server."""

from __future__ import annotations

import sys


def main() -> None:
    """Start the installed MCP server without relying on the current directory."""
    try:
        from skillscout.mcp_server import run_stdio
    except ImportError:
        print(
            "SkillScout MCP support could not load a required runtime dependency. "
            "Reinstall its wheel without --no-deps so the declared dependencies are installed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    run_stdio()
