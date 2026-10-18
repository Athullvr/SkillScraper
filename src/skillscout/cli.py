"""Typer command line interface and top-level orchestration."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import typer
from rich.console import Console

from skillscout import __version__
from skillscout.catalog import (
    CatalogAuthenticationError,
    CatalogClient,
    CatalogDataError,
    CatalogError,
    CatalogUnavailableError,
)
from skillscout.config import ConfigurationError, load_config
from skillscout.decision import (
    build_candidate_report,
    make_inspection_evidence,
    make_inspection_report,
    missing_evidence,
    no_skill_report,
    proposed_install_command,
    report_for_candidates,
    should_skip_catalog_for_task,
)
from skillscout.models import (
    CatalogSkill,
    Compatibility,
    Decision,
    DoctorReport,
    ProjectSnapshot,
    SkillDetail,
)
from skillscout.llm import LLMAdapter, LLMError
from skillscout.project_context import ProjectContextError, read_project_context
from skillscout.ranking import (
    RankedSkill,
    evaluate_compatibility,
    rank_catalog_results,
    refine_rank,
    relevance_threshold,
)
from skillscout.report import json_report, render_doctor, render_inspection, render_recommendation

app = typer.Typer(
    name="skillscout",
    help="Find and inspect agent skills without installing or activating them.",
    no_args_is_help=True,
    add_completion=False,
)
ERROR_CONSOLE = Console(stderr=True, highlight=False)
VALID_FORMATS = Literal["text", "json"]


@app.command()
def recommend(
    task: str = typer.Argument(..., help="Coding task to assess."),
    project: Path = typer.Option(Path("."), "--project", help="Project directory for bounded metadata detection."),
    agent: str = typer.Option("codex", "--agent", help="Target agent for the inert install command."),
    output_format: VALID_FORMATS = typer.Option(
        "text", "--format", help="Output format: terminal text or versioned JSON."
    ),
) -> None:
    """Assess whether a skill adds value, then inspect a bounded set of candidates."""
    try:
        config = load_config()
        project_context = read_project_context(project)
        if should_skip_catalog_for_task(task):
            report = no_skill_report(
                task,
                agent,
                project_context,
                reason="This is a routine edit that is unlikely to gain clear value from an additional skill.",
                limitations=["This no-skill decision used deterministic task wording and did not contact the catalog."],
            )
            _emit(report, output_format)
            return

        with CatalogClient(config) as catalog, LLMAdapter(config) as llm:
            search_results = catalog.search(task, limit=min(50, max(10, config.candidate_limit * 6)))
            if not search_results:
                report = no_skill_report(
                    task,
                    agent,
                    project_context,
                    reason="The catalog returned no skill candidates for this task.",
                    limitations=["The search completed successfully, but no catalog results were available."],
                )
                _emit(report, output_format)
                return

            ranked = rank_catalog_results(task, search_results)[: config.candidate_limit]
            candidate_reports = []
            for item in ranked:
                candidate_reports.append(
                    _inspect_candidate(catalog, item, task, agent, project_context, llm)
                )
            candidate_reports.sort(key=lambda item: item.relevance_score, reverse=True)
            candidate_reports = [
                candidate.model_copy(update={"rank": index})
                for index, candidate in enumerate(candidate_reports, start=1)
            ]
            report = report_for_candidates(
                task,
                agent,
                project_context,
                candidate_reports,
                limitations=[
                    f"Inspected at most {config.candidate_limit} candidate detail records; ranking weights are initial hypotheses, not validated truth."
                ],
            )
        _emit(report, output_format)
        if report.decision == Decision.AVOID:
            raise typer.Exit(code=4)
    except typer.Exit:
        raise
    except (ConfigurationError, ProjectContextError) as exc:
        _emit_error(str(exc), output_format, code="configuration_error", exit_code=2)
    except CatalogAuthenticationError as exc:
        _emit_error(str(exc), output_format, code="catalog_authentication_error", exit_code=3)
    except (CatalogUnavailableError, CatalogDataError, CatalogError) as exc:
        _emit_error(str(exc), output_format, code="catalog_error", exit_code=3)


@app.command()
def inspect(
    skill_id: str = typer.Argument(..., help="Stable skills.sh skill ID, such as owner/repo/skill."),
    project: Path = typer.Option(Path("."), "--project", help="Project directory for bounded metadata detection."),
    output_format: VALID_FORMATS = typer.Option("text", "--format", help="Output format: text or JSON."),
) -> None:
    """Fetch and inspect a skill revision without installing it."""
    try:
        config = load_config()
        project_context = read_project_context(project)
        skill = _placeholder_skill(skill_id, config.catalog_base_url)
        with CatalogClient(config) as catalog:
            try:
                listings = catalog.search(skill_id, limit=50)
                skill = next((item for item in listings if item.id == skill_id), skill)
            except CatalogError:
                # Search can be authenticated even when direct details are available; details remain usable.
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
        compatibility = evaluate_compatibility(skill, detail, project_context)
        report = make_inspection_report(skill, evidence, compatibility)
        if output_format == "json":
            typer.echo(json_report(report))
        else:
            render_inspection(report)
        if report.decision == Decision.AVOID:
            raise typer.Exit(code=4)
    except typer.Exit:
        raise
    except (ConfigurationError, ProjectContextError, ValueError) as exc:
        _emit_error(str(exc), output_format, code="input_error", exit_code=2)
    except CatalogAuthenticationError as exc:
        _emit_error(str(exc), output_format, code="catalog_authentication_error", exit_code=3)
    except (CatalogUnavailableError, CatalogDataError, CatalogError) as exc:
        _emit_error(str(exc), output_format, code="catalog_error", exit_code=3)


@app.command()
def doctor() -> None:
    """Check local runtime and configuration without making network requests."""
    try:
        config = load_config()
    except ConfigurationError as exc:
        _emit_error(str(exc), "text", code="configuration_error", exit_code=2)
        return
    python_version = ".".join(str(part) for part in sys.version_info[:3])
    report = DoctorReport(
        python_version=python_version,
        python_supported=sys.version_info >= (3, 12),
        catalog_url=config.catalog_base_url,
        catalog_auth_configured=bool(config.catalog_token),
        candidate_limit=config.candidate_limit,
        llm_api_key_configured=bool(config.llm_api_key),
        llm_task_text_opt_in=config.llm_send_task,
        llm_enabled=bool(config.llm_api_key and config.llm_send_task),
    )
    render_doctor(report)
    if not report.python_supported:
        raise typer.Exit(code=2)


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", help="Show the SkillScout version and exit."),
) -> None:
    if version:
        typer.echo(f"SkillScout {__version__}")
        raise typer.Exit()


def _inspect_candidate(
    catalog: CatalogClient,
    ranked: RankedSkill,
    task: str,
    agent: str,
    project: ProjectSnapshot,
    llm: LLMAdapter,
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
                    *[f"Optional LLM task-fit suggestion: {reason}" for reason in assessment.rationale],
                ],
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
    )
    if llm_failure:
        candidate.limitations.append("Optional LLM assessment failed; deterministic task-fit ranking was used.")
    return candidate


def _placeholder_skill(skill_id: str, catalog_base_url: str) -> CatalogSkill:
    source, separator, slug = skill_id.rpartition("/")
    if not separator or not source or not slug or any(part in {".", ".."} for part in skill_id.split("/")):
        raise ValueError("skill ID must have the form source/skill and contain no dot path segments")
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


def _emit(report, output_format: str) -> None:
    if output_format == "json":
        typer.echo(json_report(report))
    else:
        render_recommendation(report)


def _emit_error(message: str, output_format: str, *, code: str, exit_code: int) -> None:
    if output_format == "json":
        typer.echo(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "error": {"code": code, "message": message},
                },
                indent=2,
            )
        )
    else:
        ERROR_CONSOLE.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code=exit_code)


if __name__ == "__main__":
    app()

