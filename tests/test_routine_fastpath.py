from __future__ import annotations

import json

from typer.testing import CliRunner

from skillscout import cli
from skillscout.catalog import CatalogUnavailableError
from skillscout.config import Config
from skillscout.ranking import task_is_routine

runner = CliRunner()


def _config() -> Config:
    return Config(
        catalog_base_url="https://skills.test",
        catalog_token=None,
        candidate_limit=3,
        connect_timeout_seconds=0.2,
        read_timeout_seconds=0.2,
        max_response_bytes=2_000_000,
        retry_count=2,
        llm_api_key=None,
        llm_base_url="https://llm.test/v1",
        llm_model="test-model",
        llm_send_task=False,
    )


def test_routine_and_skill_relevant_phrasing_remains_distinguished():
    assert task_is_routine("Rename a local variable in a Python helper function.")
    assert task_is_routine("Change the local variable name in a helper.")
    assert task_is_routine("rename the variable")
    assert not task_is_routine("Rename a local variable during a security review")
    assert not task_is_routine("Implement FastAPI OAuth login")


def test_local_variable_rename_uses_no_catalog_path(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_config", _config)

    def unexpected_catalog(_config):
        raise AssertionError("routine local-variable rename must not contact the catalog")

    monkeypatch.setattr(cli, "CatalogClient", unexpected_catalog)
    result = runner.invoke(
        cli.app,
        [
            "recommend",
            "Rename a local variable in a Python helper function.",
            "--project",
            str(tmp_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "NO_SKILL_NEEDED"
    assert payload["candidates"] == []


def test_skill_relevant_catalog_failure_stays_operational_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_config", _config)

    class UnavailableCatalog:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def search(self, _query, *, limit):
            raise CatalogUnavailableError("catalog request failed after bounded retries")

    monkeypatch.setattr(cli, "CatalogClient", UnavailableCatalog)
    result = runner.invoke(
        cli.app,
        [
            "recommend",
            "Implement FastAPI OAuth login",
            "--project",
            str(tmp_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "catalog_error"
    assert "decision" not in payload
