from __future__ import annotations

from skillscout.models import CatalogSkill, ProjectSnapshot, SkillDetail, SkillFile
from skillscout.ranking import (
    evaluate_compatibility,
    rank_catalog_results,
    refine_rank,
    task_is_routine,
)


def test_ranking_exposes_concept_match_and_generic_penalty():
    skills = [
        CatalogSkill(id="acme/skills/fastapi-auth", source="acme/skills", slug="fastapi-auth", name="FastAPI OAuth"),
        CatalogSkill(id="acme/general", source="acme", slug="general", name="General coding assistant"),
    ]

    ranked = rank_catalog_results("Implement OAuth authentication in FastAPI", skills)

    assert ranked[0].skill.id == "acme/skills/fastapi-auth"
    assert ranked[0].score > ranked[1].score
    assert any("Direct catalog listing matches" in text for text in ranked[0].rationale)


def test_documented_operation_demotes_topical_only_match_and_explains_why():
    skill = CatalogSkill(
        id="acme/skills/fastapi-oauth",
        source="acme/skills",
        slug="fastapi-oauth",
        name="FastAPI OAuth login",
    )
    listing = rank_catalog_results("Implement OAuth login in FastAPI", [skill])[0]

    topical = refine_rank(
        "Implement OAuth login in FastAPI",
        listing,
        SkillDetail(
            id=skill.id,
            source=skill.source,
            slug=skill.slug,
            files=[
                SkillFile(
                    path="SKILL.md",
                    contents="# FastAPI OAuth login patterns\n\nAuthentication guidance and examples.",
                )
            ],
        ),
    )

    assert topical.listing_score > topical.score
    assert topical.capability_supported is False
    assert topical.score < 0.34
    assert any("topical overlap" in reason for reason in topical.rationale)


def test_operation_and_task_concepts_in_skill_instructions_confirm_match():
    skill = CatalogSkill(
        id="acme/skills/fastapi-oauth",
        source="acme/skills",
        slug="fastapi-oauth",
        name="FastAPI OAuth login",
    )
    task = "Implement OAuth login in FastAPI"
    ranked = rank_catalog_results(task, [skill])[0]

    refined = refine_rank(
        task,
        ranked,
        SkillDetail(
            id=skill.id,
            source=skill.source,
            slug=skill.slug,
            files=[
                SkillFile(
                    path="SKILL.md",
                    contents=(
                        "# OAuth login in FastAPI\n\n"
                        "Configure the OAuth login callback in FastAPI and validate token claims."
                    ),
                )
            ],
        ),
    )

    assert refined.capability_supported is True
    assert refined.score >= 0.34
    assert any("SKILL.md documents the requested implementation" in reason for reason in refined.rationale)


def test_operation_evidence_can_move_a_lower_listing_match_above_a_topical_title_match():
    task = "Implement OAuth login in FastAPI"
    topical = CatalogSkill(
        id="acme/title-match",
        source="acme",
        slug="fastapi-oauth-login",
        name="FastAPI OAuth login",
    )
    documented = CatalogSkill(
        id="acme/documented-match",
        source="acme",
        slug="oauth-authentication",
        name="OAuth authentication",
    )
    listing = rank_catalog_results(task, [topical, documented])
    assert listing[0].skill.id == topical.id

    details = {
        topical.id: SkillDetail(
            id=topical.id,
            source=topical.source,
            slug=topical.slug,
            files=[
                SkillFile(
                    path="SKILL.md",
                    contents="# FastAPI OAuth login patterns\n\nGeneral authentication guidance.",
                )
            ],
        ),
        documented.id: SkillDetail(
            id=documented.id,
            source=documented.source,
            slug=documented.slug,
            files=[
                SkillFile(
                    path="SKILL.md",
                    contents="Configure an OAuth login callback in FastAPI.",
                )
            ],
        ),
    }

    refined = sorted(
        (refine_rank(task, item, details[item.skill.id]) for item in listing),
        key=lambda item: item.score,
        reverse=True,
    )

    assert refined[0].skill.id == documented.id
    assert refined[0].capability_supported is True
    assert refined[1].skill.id == topical.id
    assert refined[1].capability_supported is False


def test_heading_or_code_only_operation_does_not_confirm_capability():
    skill = CatalogSkill(
        id="acme/skills/fastapi-oauth",
        source="acme/skills",
        slug="fastapi-oauth",
        name="FastAPI OAuth login",
    )
    task = "Implement OAuth login in FastAPI"
    ranked = rank_catalog_results(task, [skill])[0]

    for document in (
        "# Implement OAuth login in FastAPI\n\nThis guide covers authentication.",
        "# FastAPI OAuth login\n\nThis implementation describes authentication patterns.",
        "# FastAPI OAuth login\n\nDo not implement OAuth login in FastAPI.",
        "# FastAPI OAuth login\n\n```text\nImplement OAuth login in FastAPI\n```",
    ):
        refined = refine_rank(
            task,
            ranked,
            SkillDetail(
                id=skill.id,
                source=skill.source,
                slug=skill.slug,
                files=[SkillFile(path="SKILL.md", contents=document)],
            ),
        )
        assert refined.capability_supported is False
        assert refined.score < 0.34


def test_negated_task_operation_is_not_treated_as_a_request_for_that_capability():
    task = "Do not implement OAuth login in FastAPI"
    skill = CatalogSkill(
        id="acme/skills/fastapi-oauth",
        source="acme/skills",
        slug="fastapi-oauth",
        name="FastAPI OAuth login",
    )
    ranked = rank_catalog_results(task, [skill])[0]

    refined = refine_rank(
        task,
        ranked,
        SkillDetail(
            id=skill.id,
            source=skill.source,
            slug=skill.slug,
            files=[
                SkillFile(
                    path="SKILL.md",
                    contents="Configure OAuth login in FastAPI.",
                )
            ],
        ),
    )

    assert refined.capability_supported is False
    assert refined.score < 0.34


def test_alias_only_task_overlap_scores_below_direct_wording():
    task = "Implement OAuth login in FastAPI"
    direct = CatalogSkill(
        id="acme/direct",
        source="acme",
        slug="direct",
        name="FastAPI OAuth login",
    )
    alias_only = CatalogSkill(
        id="acme/alias",
        source="acme",
        slug="alias",
        name="FastAPI authentication login",
    )

    direct_rank, alias_rank = rank_catalog_results(task, [direct, alias_only])

    assert direct_rank.skill.id == direct.id
    assert direct_rank.score > alias_rank.score
    assert any("Weaker related wording" in item for item in alias_rank.rationale)


def test_task_secret_is_not_repeated_in_matching_rationale():
    secret = "short-secret-value"
    task = f"Implement OAuth login in FastAPI with API_KEY={secret}"
    skill = CatalogSkill(
        id="acme/fastapi-oauth",
        source="acme",
        slug="fastapi-oauth",
        name=f"FastAPI OAuth login {secret}",
    )

    ranked = rank_catalog_results(task, [skill])[0]

    assert secret not in " ".join(ranked.rationale)


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

