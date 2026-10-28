from __future__ import annotations

import builtins
import json

import pytest

from typer.testing import CliRunner

from skillscout import cli, entrypoint
from skillscout.config import Config
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
    assert result.stdout.strip() == "SkillScraper 0.1.0"


def test_help_uses_the_canonical_product_name():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert result.stdout.lstrip().startswith("Usage: skillscraper ")
    assert "SkillScraper helps coding agents" in result.stdout


def test_doctor_uses_the_canonical_product_name_without_catalog_access(monkeypatch):
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: Config(
            catalog_base_url="https://skills.sh",
            catalog_token=None,
            candidate_limit=3,
            connect_timeout_seconds=5.0,
            read_timeout_seconds=15.0,
            max_response_bytes=2_000_000,
            retry_count=2,
            llm_api_key=None,
            llm_base_url="https://api.openai.com/v1",
            llm_model="test-model",
            llm_send_task=False,
        ),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "SkillScraper doctor" in result.stdout
    assert "Catalog credential" in result.stdout


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
