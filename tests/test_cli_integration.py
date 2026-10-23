from __future__ import annotations

import json

from typer.testing import CliRunner

from skillscout import cli
from skillscout.models import AuditFinding, CatalogSkill, SkillDetail, SkillFile, utc_now

runner = CliRunner()


def test_recommend_runs_end_to_end_without_install_or_candidate_execution(tmp_path, monkeypatch):
    detail_calls: list[str] = []
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="service"\nrequires-python=">=3.12"\ndependencies=["fastapi>=0.100"]\n',
        encoding="utf-8",
    )
    skill = CatalogSkill.model_validate(
        {
            "id": "acme/skills/fastapi-oauth",
            "name": "FastAPI OAuth",
            "source": "acme/skills",
            "slug": "fastapi-oauth",
            "installs": 8,
            "sourceType": "github",
            "url": "https://skills.sh/acme/skills/fastapi-oauth",
            "installUrl": "https://github.com/acme/skills",
        }
    )
    detail = SkillDetail(
        id=skill.id,
        source=skill.source,
        slug=skill.slug,
        hash="detail-revision-1",
        files=[
            SkillFile(
                path="SKILL.md",
                contents=(
                    "# FastAPI OAuth login patterns\n\n"
                    "Configure an OAuth login callback and validate tokens in FastAPI."
                ),
            )
        ],
    )

    class FakeCatalog:
        def __init__(self, config):
            self.config = config

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def search(self, query, *, limit):
            assert "OAuth" in query
            assert limit <= 50
            return [skill]

        def get_detail(self, candidate):
            detail_calls.append(candidate.id)
            assert candidate.id == skill.id
            return detail

        def get_audits(self, candidate):
            return (
                [
                    AuditFinding(
                        provider="FixtureAudit",
                        status="pass",
                        auditedAt=utc_now(),
                        revision="detail-revision-1",
                    )
                ],
                "available",
                [],
            )

    class FakeLLM:
        def __init__(self, config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def assess_task_fit(self, **kwargs):
            return None

    fake_catalog = FakeCatalog
    monkeypatch.setattr(cli, "CatalogClient", fake_catalog)
    monkeypatch.setattr(cli, "LLMAdapter", FakeLLM)

    result = runner.invoke(
        cli.app,
        ["recommend", "Implement OAuth login in FastAPI", "--project", str(tmp_path), "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["decision"] == "USE"
    assert report["candidates"][0]["evidence"]["external_audit_status"] == "current"
    assert report["candidates"][0]["skill"]["hash"] == "detail-revision-1"
    assert "npx skills add" in report["candidates"][0]["proposed_install_command"]
    assert detail_calls == [skill.id]
