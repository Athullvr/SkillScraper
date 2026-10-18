from __future__ import annotations

from skillscout.models import CatalogSkill, ProjectSnapshot, SkillDetail, SkillFile
from skillscout.ranking import evaluate_compatibility, rank_catalog_results, task_is_routine


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
        files=[SkillFile(path="SKILL.md", contents="Use FastAPI with OAuth.")],
    )

    compatibility = evaluate_compatibility(skill, detail, project)

    assert compatibility.status == "compatible"
    assert "Dependency detected: FastAPI" in compatibility.detected_facts
    assert compatibility.unknowns


def test_routine_edits_are_classified_as_no_skill():
    assert task_is_routine("rename the variable")
    assert task_is_routine("fix a typo in the docs")
    assert not task_is_routine("Design a Kubernetes security policy")

