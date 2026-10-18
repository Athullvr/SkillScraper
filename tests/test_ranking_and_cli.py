from __future__ import annotations

import json

from typer.testing import CliRunner

from skillscout.cli import app
from skillscout.models import CatalogSkill, ProjectSnapshot, SkillDetail, SkillFile
from skillscout.ranking import evaluate_compatibility, rank_catalog_results, task_is_routine

runner = CliRunner()


def test_ranking_exposes_concept_match_and_generic_penalty():
    skills = [
        CatalogSkill(id="acme/skills/fastapi-auth", source="acme/skills", slug="fastapi-auth", name="FastAPI OAuth"),
        CatalogSkill(id="acme/general", source="acme", slug="general", name="General coding assistant"),
    ]

    ranked = rank_catalog_results("Implement OAuth authentication in FastAPI", skills)

    assert ranked[0].skill.id == "acme/skills/fastapi-auth"
    assert ranked[0].score > ranked[1].score
    assert any("Matching task concepts" in text for text in ranked[0].rationale)


def test_compatibility_distinguishes_detected_and_unknown():
    project = ProjectSnapshot(path="/tmp/sample", facts=["Dependency detected: FastAPI"])
    skill = CatalogSkill(id="acme/fastapi/skill", source="acme/fastapi", slug="skill", name="FastAPI OAuth")
    detail = SkillDetail(
        id=skill.id,
        source=skill.source,
        slug=skill.slug,
        files=[SkillFile(path="SKILL.md", contents="Use FastAPI with OAuth." )],
    )

    compatibility = evaluate_compatibility(skill, detail, project)

    assert compatibility.status == "compatible"
    assert "Dependency detected: FastAPI" in compatibility.detected_facts
    assert compatibility.unknowns


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


def test_routine_edits_are_classified_as_no_skill():
    assert task_is_routine("rename the variable")
    assert task_is_routine("fix a typo in the docs")
    assert not task_is_routine("Design a Kubernetes security policy")

