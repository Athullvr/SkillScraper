"""Dependency-safe bootstrap for the optional Codex prompt hook."""

from __future__ import annotations

import json
import sys


def main() -> None:
    """Run the hook or emit a protocol-safe installation instruction."""
    try:
        from skillscout.codex_hook import main as run_hook
    except ImportError:
        response = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": (
                    "SkillScraper check is REVIEW: hook dependencies are missing. Reinstall "
                    "the SkillScraper wheel without `--no-deps` so declared dependencies are "
                    "installed; no recommendation was made."
                ),
            }
        }
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        return

    run_hook()
