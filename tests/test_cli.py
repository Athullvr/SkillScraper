from __future__ import annotations

import builtins
import json

import pytest

from typer.testing import CliRunner

from skillscout import entrypoint
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


def test_json_errors_use_the_versioned_error_shape(tmp_path):
    result = runner.invoke(
        app,
        ["recommend", "Implement OAuth login", "--project", str(tmp_path / "missing"), "--format", "json"],
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1.0"
    assert payload["error"]["code"] == "input_error"


def test_cli_entrypoint_reports_missing_dependencies_on_stderr(monkeypatch, capsys):
    original_import = builtins.__import__

    def missing_cli(name, *args, **kwargs):
        if name == "skillscout.cli":
            raise ModuleNotFoundError("missing Typer")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_cli)

    with pytest.raises(SystemExit) as error:
        entrypoint.cli_main()

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "Reinstall its wheel without --no-deps" in captured.err
    assert captured.out == ""
