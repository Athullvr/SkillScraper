from __future__ import annotations

import json

from typer.testing import CliRunner

from skillscout.cli import app

runner = CliRunner()


def test_routine_task_skips_catalog_and_emits_versioned_json(tmp_path):
    result = runner.invoke(
        app,
        ["recommend", "fix typo in README", "--project", str(tmp_path), "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1.0"
    assert payload["decision"] == "NO_SKILL_NEEDED"
    assert payload["candidates"] == []


def test_version_option_works_without_a_subcommand():
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "SkillScout 0.1.0"
