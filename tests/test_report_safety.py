from __future__ import annotations

from rich.console import Console

from skillscout.report import escape, render_recommendation
from skillscout.models import Decision, ProjectSnapshot, Report


def test_terminal_renderer_escapes_untrusted_markup_and_removes_controls():
    hostile = "[bold red]spoof[/bold red]\x1b[2J\x9b31m"
    report = Report(
        task=hostile,
        agent="codex",
        project=ProjectSnapshot(path=hostile),
        decision=Decision.NO_SKILL_NEEDED,
        summary=hostile,
        candidates=[],
    )
    console = Console(record=True, color_system=None, width=120)

    render_recommendation(report, console=console)
    rendered = console.export_text()

    assert "[bold red]spoof[/bold red]" in rendered
    assert "\x1b" not in escape(hostile)
    assert "\x9b" not in escape(hostile)
    assert "spoof" in rendered
