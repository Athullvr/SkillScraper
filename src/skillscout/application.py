"""Shared, presentation-independent SkillScraper application workflows."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import quote

from skillscout.catalog import CatalogClient, CatalogError
from skillscout.config import Config, load_config
from skillscout.decision import (
    build_candidate_report,
    incomplete_search_report,
    make_inspection_evidence,
    make_inspection_report,
    missing_evidence,
    no_skill_report,
    report_for_candidates,
    should_skip_catalog_for_task,
)
from skillscout.llm import LLMAdapter, LLMError, redact_secrets
from skillscout.models import (
    CandidateReport,
    CatalogSkill,
    DoctorReport,
    ProjectSnapshot,
    Report,
    SkillDetail,
    SkillInspectionReport,
)
from skillscout.project_context import read_project_context
from skillscout.ranking import (
    RankedSkill,
    evaluate_compatibility,
    rank_catalog_results,
    refine_rank,
)

MAX_TASK_CHARS = 4_000
AGENT_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,50}")


def validate_task(task: str) -> str:
    """Normalize and validate task text shared by CLI and MCP workflows."""
    task = task.strip()
    if not task or len(task) > MAX_TASK_CHARS:
        raise ValueError("task must contain between 1 and 4000 characters")
    return task


def validate_agent(agent: str) -> str:
    """Use the CLI's existing bounded agent identifier convention."""
    if not AGENT_PATTERN.fullmatch(agent):
        raise ValueError("agent must be 1–50 letters, digits, dots, underscores, or hyphens")
    return agent


def prepare_recommendation(
    task: str,
    project_path: str | Path | None = None,
    agent: str = "codex",
    *,
    allow_llm: bool = True,
    config_loader: Callable[[], Config] | None = None,
    project_context_reader: Callable[[str | Path], ProjectSnapshot] | None = None,
    catalog_client_factory: Callable[[Config], CatalogClient] | None = None,
    llm_adapter_factory: Callable[[Config], LLMAdapter] | None = None,
    detail_observer: Callable[[CandidateReport, SkillDetail, str, ProjectSnapshot], None]
    | None = None,
) -> Report:
    """Run the canonical recommendation workflow and return its validated report.

    Factories are injectable so the CLI and deterministic tests can share this exact
    application path. MCP callers set ``allow_llm=False`` so task text is never sent
    to an LLM provider from the agent interface.
    """
    task = validate_task(task)
    agent = validate_agent(agent)
    project = (project_context_reader or read_project_context)(
        Path.cwd() if project_path is None else project_path
    )

    if should_skip_catalog_for_task(task):
        return no_skill_report(
            task,
            agent,
            project,
            reason="This is a routine edit that is unlikely to gain clear value from an additional skill.",
            limitations=[
                "This no-skill decision used deterministic task wording and did not contact the catalog."
            ],
        )

    # Routine tasks are decided locally; catalog and optional-service configuration
    # must not block this fast path.
    config = (config_loader or load_config)()
    catalog_factory = catalog_client_factory or CatalogClient
    llm_factory = llm_adapter_factory or LLMAdapter
    llm_context = llm_factory(config) if allow_llm else nullcontext(None)
    with catalog_factory(config) as catalog, llm_context as llm:
        search_results = catalog.search(
            redact_secrets(task),
            limit=min(50, max(10, config.candidate_limit * 6)),
        )
        search_complete = getattr(search_results, "complete", True)
        if type(search_complete) is not bool:
            raise CatalogError("catalog search completeness metadata was invalid")
        if not search_results:
            if not search_complete:
                return incomplete_search_report(task, agent, project)
            return no_skill_report(
                task,
                agent,
                project,
                reason="The catalog returned no skill candidates for this task.",
                limitations=[
                    "The search completed successfully, but no catalog results were available."
                ],
            )

        ranked = rank_catalog_results(task, search_results)[: config.candidate_limit]
        candidate_reports = [
            _inspect_candidate(
                catalog, item, task, agent, project, llm, detail_observer=detail_observer
            )
            for item in ranked
        ]
        candidate_reports.sort(key=lambda item: item.relevance_score, reverse=True)
        candidate_reports = [
            candidate.model_copy(update={"rank": index})
            for index, candidate in enumerate(candidate_reports, start=1)
        ]
        report_limitations = [
            f"Inspected at most {config.candidate_limit} candidate detail records; "
            "ranking weights are initial hypotheses, not validated truth."
        ]
        if not search_complete:
            report_limitations.append(
                "The catalog indicated that more search results may be available; this "
                "recommendation covers only returned candidates."
            )
        return report_for_candidates(
            task,
            agent,
            project,
            candidate_reports,
            limitations=report_limitations,
            search_complete=search_complete,
        )


def inspect_skill(
    skill_id: str,
    project_path: str | Path | None = None,
    *,
    config_loader: Callable[[], Config] | None = None,
    project_context_reader: Callable[[str | Path], ProjectSnapshot] | None = None,
    catalog_client_factory: Callable[[Config], CatalogClient] | None = None,
) -> SkillInspectionReport:
    """Run the existing stable-ID detail, scan, compatibility, and audit workflow."""
    config = (config_loader or load_config)()
    project = (project_context_reader or read_project_context)(
        Path.cwd() if project_path is None else project_path
    )
    skill = _placeholder_skill(skill_id, config.catalog_base_url)
    catalog_factory = catalog_client_factory or CatalogClient

    with catalog_factory(config) as catalog:
        try:
            listings = catalog.search(skill_id, limit=50)
            skill = next((item for item in listings if item.id == skill_id), skill)
        except CatalogError:
            # Preserve the CLI path: details may work when search requires authentication.
            pass
        detail = catalog.get_detail(skill)
        skill = skill.model_copy(update={"content_hash": detail.content_hash or skill.content_hash})
        audits, availability, audit_notes = catalog.get_audits(skill)

    evidence = make_inspection_evidence(
        detail,
        audits,
        availability,
        audit_notes,
        catalog_hash=skill.content_hash,
    )
    compatibility = evaluate_compatibility(skill, detail, project)
    return make_inspection_report(skill, evidence, compatibility)


def doctor_report(config_loader: Callable[[], Config] | None = None) -> DoctorReport:
    """Build the existing local-only doctor report without making network calls."""
    config = (config_loader or load_config)()
    python_version = ".".join(str(part) for part in sys.version_info[:3])
    return DoctorReport(
        python_version=python_version,
        python_supported=sys.version_info >= (3, 12),
        catalog_url=config.catalog_base_url,
        catalog_auth_configured=bool(config.catalog_token),
        candidate_limit=config.candidate_limit,
        llm_api_key_configured=bool(config.llm_api_key),
        llm_task_text_opt_in=config.llm_send_task,
        llm_enabled=bool(config.llm_api_key and config.llm_send_task),
    )


def _inspect_candidate(
    catalog: CatalogClient,
    ranked: RankedSkill,
    task: str,
    agent: str,
    project: ProjectSnapshot,
    llm: LLMAdapter | None,
    *,
    detail_observer: Callable[[CandidateReport, SkillDetail, str, ProjectSnapshot], None]
    | None = None,
):
    try:
        detail = catalog.get_detail(ranked.skill)
    except CatalogError as exc:
        compatibility = evaluate_compatibility(ranked.skill, None, project)
        return build_candidate_report(
            skill=ranked.skill,
            rank=1,
            relevance_score=ranked.score,
            task_fit_rationale=ranked.rationale,
            compatibility=compatibility,
            evidence=missing_evidence(ranked.skill, f"Detail inspection failed: {exc}."),
            agent=agent,
        )

    skill = ranked.skill.model_copy(
        update={"content_hash": detail.content_hash or ranked.skill.content_hash}
    )
    refined = refine_rank(task, ranked, detail)
    llm_failure = False
    if llm is not None and refined.capability_supported is True:
        try:
            skill_document = next(
                (item.contents for item in (detail.files or []) if item.path.lower() == "skill.md"),
                "",
            )
            assessment = llm.assess_task_fit(
                task=task,
                skill_id=skill.id,
                skill_name=skill.name,
                skill_description=skill.description or skill_document,
            )
            if assessment is not None:
                refined = RankedSkill(
                    skill=skill,
                    score=round(refined.score * 0.75 + assessment.score * 0.25, 3),
                    rationale=[
                        *refined.rationale,
                        "Optional LLM contributed 25% to the displayed relevance score.",
                        *[
                            f"Optional LLM task-fit suggestion: {reason}"
                            for reason in assessment.rationale
                        ],
                    ],
                    listing_score=refined.listing_score,
                    capability_supported=refined.capability_supported,
                )
        except LLMError:
            llm_failure = True

    audits, audit_status, audit_notes = catalog.get_audits(skill)
    evidence = make_inspection_evidence(
        detail,
        audits,
        audit_status,
        audit_notes,
        catalog_hash=ranked.skill.content_hash,
    )
    compatibility = evaluate_compatibility(skill, detail, project)
    candidate = build_candidate_report(
        skill=skill,
        rank=1,
        relevance_score=refined.score,
        task_fit_rationale=refined.rationale,
        compatibility=compatibility,
        evidence=evidence,
        agent=agent,
        task_support_confirmed=refined.capability_supported,
    )
    if detail_observer is not None:
        detail_observer(candidate, detail, task, project)
    if llm_failure:
        candidate.limitations.append(
            "Optional LLM assessment failed; deterministic task-fit ranking was used."
        )
    return candidate


def _placeholder_skill(skill_id: str, catalog_base_url: str) -> CatalogSkill:
    source, separator, slug = skill_id.rpartition("/")
    if (
        not separator
        or not source
        or not slug
        or any(part in {".", ".."} for part in skill_id.split("/"))
    ):
        raise ValueError(
            "skill ID must have the form source/skill and contain no dot path segments"
        )
    source_parts = source.split("/")
    if any(not part for part in source_parts):
        raise ValueError("skill ID contains an empty source segment")
    encoded_id = "/".join(quote(part, safe="") for part in skill_id.split("/"))
    return CatalogSkill.model_validate(
        {
            "id": skill_id,
            "source": source,
            "slug": slug,
            "name": slug.replace("-", " "),
            "sourceType": None,
            "url": f"{catalog_base_url}/{encoded_id}",
            "installUrl": None,
            "installs": 0,
        }
    )
