from __future__ import annotations

import json

from typer.testing import CliRunner

from skillscout import cli
from skillscout.catalog import CatalogUnavailableError
from skillscout.config import Config
from skillscout.models import AuditFinding, CatalogSkill, SkillDetail, SkillFile, utc_now


runner = CliRunner()


def _offline_config() -> Config:
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


def test_catalog_failure_has_useful_json_and_exit_code_three(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_config", _offline_config)

    class UnavailableCatalog:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def search(self, _query, *, limit):
            raise CatalogUnavailableError("catalog unavailable after bounded retries")

    monkeypatch.setattr(cli, "CatalogClient", UnavailableCatalog)
    result = runner.invoke(
        cli.app,
        ["recommend", "Implement FastAPI OAuth login", "--project", str(tmp_path), "--format", "json"],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1.0"
    assert payload["error"]["code"] == "catalog_error"


def test_current_critical_audit_produces_avoid_and_exit_code_four(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_config", _offline_config)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="service"\ndependencies=["fastapi>=0.100"]\n',
        encoding="utf-8",
    )
    skill = CatalogSkill(
        id="acme/skills/fastapi-oauth",
        name="FastAPI OAuth",
        source="acme/skills",
        slug="fastapi-oauth",
        url="https://skills.sh/acme/skills/fastapi-oauth",
        installUrl="https://github.com/acme/skills",
    )
    detail = SkillDetail(
        id=skill.id,
        source=skill.source,
        slug=skill.slug,
        hash="revision-1",
        files=[SkillFile(path="SKILL.md", contents="# FastAPI OAuth login")],
    )

    class CriticalCatalog:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def search(self, _query, *, limit):
            return [skill]

        def get_detail(self, _skill):
            return detail

        def get_audits(self, _skill):
            return (
                [
                    AuditFinding(
                        provider="Fixture",
                        status="fail",
                        auditedAt=utc_now(),
                        revision="revision-1",
                    )
                ],
                "available",
                [],
            )

    monkeypatch.setattr(cli, "CatalogClient", CriticalCatalog)
    result = runner.invoke(
        cli.app,
        ["recommend", "Implement FastAPI OAuth login", "--project", str(tmp_path), "--format", "json"],
    )

    assert result.exit_code == 4
    payload = json.loads(result.stdout)
    assert payload["decision"] == "AVOID"
    assert payload["candidates"][0]["decision"] == "AVOID"
    assert "npx skills add" in payload["candidates"][0]["proposed_install_command"]
