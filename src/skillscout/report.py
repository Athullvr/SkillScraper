"""Terminal and machine-readable report rendering."""

from __future__ import annotations

import re
from typing import TypeVar

from rich import box
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.table import Table

from skillscout.models import (
    CandidateReport,
    Decision,
    DoctorReport,
    InspectionEvidence,
    Report,
    SkillInspectionReport,
)

ModelT = TypeVar("ModelT", Report, SkillInspectionReport, DoctorReport)
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def escape(value: object) -> str:
    """Escape markup and strip terminal control characters from untrusted strings."""
    cleaned = CONTROL_CHARS.sub(" ", str(value))
    return rich_escape(cleaned)


def json_report(model: ModelT) -> str:
    """Serialize a validated report without mixing terminal formatting into stdout."""
    return model.model_dump_json(indent=2, by_alias=True)


def render_recommendation(report: Report, *, console: Console | None = None) -> None:
    console = console or Console(highlight=False)
    console.print(Panel(escape(report.summary), title=report.decision.value, box=box.ROUNDED))
    console.print(f"[bold]Task:[/bold] {escape(report.task)}")
    console.print(f"[bold]Agent:[/bold] {escape(report.agent)}")
    console.print(f"[bold]Project:[/bold] {escape(report.project.path)}")
    _render_project(report, console)
    if not report.candidates:
        console.print("\nNo skill is recommended for this task.")
    for candidate in report.candidates:
        _render_candidate(candidate, console)
    if report.limitations:
        console.print("\n[bold]Report limitations[/bold]")
        for item in report.limitations:
            console.print(f"  • {escape(item)}")


def render_inspection(report: SkillInspectionReport, *, console: Console | None = None) -> None:
    console = console or Console(highlight=False)
    console.print(Panel(f"Inspection: {escape(report.skill.name)}", title=report.decision.value))
    _render_skill_identity(report.skill, console)
    _render_compatibility(report.compatibility, console)
    _render_evidence(report.evidence, console)
    _render_decision(report.decision, report.decision_reasons, report.limitations, console)
    console.print(f"[bold]Proposed install command (not executed):[/bold] {escape(report.proposed_install_command)}")


def render_doctor(report: DoctorReport, *, console: Console | None = None) -> None:
    console = console or Console(highlight=False)
    console.print("[bold]SkillScout doctor[/bold]")
    lines = [
        ("Python", f"{report.python_version} ({'supported' if report.python_supported else 'unsupported'})"),
        ("Catalog", str(report.catalog_url)),
        ("Catalog credential", "configured" if report.catalog_auth_configured else "not configured"),
        ("Candidate limit", str(report.candidate_limit)),
        ("LLM API key", "configured" if report.llm_api_key_configured else "not configured"),
        ("Task text opt-in", "enabled" if report.llm_task_text_opt_in else "disabled"),
        ("Optional LLM path", "enabled" if report.llm_enabled else "disabled"),
    ]
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Check", style="bold")
    table.add_column("Result")
    for label, value in lines:
        table.add_row(label, escape(value))
    console.print(table)
    console.print("Doctor checks local configuration only; it does not make network requests.")


def _render_project(report: Report, console: Console) -> None:
    project = report.project
    facts = project.facts or ["No supported project metadata detected."]
    console.print("\n[bold]Project context[/bold]")
    for fact in facts:
        console.print(f"  • Detected: {escape(fact)}")
    for inferred in project.inferred:
        console.print(f"  • Inferred: {escape(inferred)}")
    for unknown in project.unknowns:
        console.print(f"  • Unknown: {escape(unknown)}")


def _render_candidate(candidate: CandidateReport, console: Console) -> None:
    console.print("\n" + "─" * 72)
    console.print(Panel(escape(candidate.skill.name), title=f"#{candidate.rank} · {candidate.decision.value}"))
    _render_skill_identity(candidate.skill, console)
    console.print(f"[bold]Task-fit score:[/bold] {candidate.relevance_score:.3f}")
    console.print("[bold]Task-fit rationale:[/bold]")
    for reason in candidate.task_fit_rationale:
        console.print(f"  • {escape(reason)}")
    _render_compatibility(candidate.compatibility, console)
    _render_evidence(candidate.evidence, console)
    _render_decision(candidate.decision, candidate.decision_reasons, candidate.limitations, console)
    console.print(f"[bold]Proposed install command (not executed):[/bold] {escape(candidate.proposed_install_command)}")


def _render_skill_identity(skill, console: Console) -> None:
    console.print(f"[bold]Stable ID:[/bold] {escape(skill.id)}")
    console.print(f"[bold]Source:[/bold] {escape(skill.source)}")
    console.print(f"[bold]Source link:[/bold] {escape(str(skill.source_url) if skill.source_url else 'unknown')}")
    console.print(f"[bold]Revision/content hash:[/bold] {escape(skill.content_hash or 'unknown')}")
    console.print(f"[bold]Catalog retrieval time:[/bold] {skill.retrieved_at.isoformat()}")


def _render_compatibility(compatibility, console: Console) -> None:
    console.print(f"[bold]Project compatibility:[/bold] {compatibility.status}")
    for item in compatibility.detected_facts:
        console.print(f"  • Detected: {escape(item)}")
    for item in compatibility.inferred_facts:
        console.print(f"  • Inferred: {escape(item)}")
    for item in compatibility.unknowns:
        console.print(f"  • Unknown: {escape(item)}")


def _render_evidence(evidence: InspectionEvidence, console: Console) -> None:
    console.print("[bold]Evidence checked[/bold]")
    console.print(f"  • Inspected: {evidence.inspection_time.isoformat()} ({evidence.evidence_status})")
    console.print(f"  • Content hash: {escape(evidence.content_hash or 'unknown')}")
    console.print(f"  • Files: {escape(', '.join(evidence.files_inspected) or 'none')}")
    console.print(f"  • External audit status: {evidence.external_audit_status}")
    if evidence.external_audits:
        console.print("  • External audit findings:")
        for audit in evidence.external_audits:
            checked_at = audit.audited_at.isoformat() if audit.audited_at else "timestamp unknown"
            revision = audit.revision or "revision unbound"
            console.print(
                f"      - {escape(audit.provider)}: {escape(audit.status)}; "
                f"{escape(audit.risk_level or 'risk unknown')}; {escape(checked_at)}; "
                f"revision {escape(revision)} — {escape(audit.summary or 'no summary')}"
            )
    else:
        console.print("  • External audit findings: none available")
    if evidence.local_findings:
        console.print("  • Local heuristic findings:")
        for finding in evidence.local_findings:
            location = f" ({finding.file_path})" if finding.file_path else ""
            console.print(f"      - {finding.severity.value}: {escape(finding.summary + location)}")
    else:
        console.print("  • Local heuristic findings: none detected")
    for note in evidence.evidence_notes:
        console.print(f"  • Evidence limitation: {escape(note)}")


def _render_decision(decision: Decision, reasons: list[str], limitations: list[str], console: Console) -> None:
    console.print(f"[bold]Decision:[/bold] {decision.value}")
    console.print("[bold]Reasons:[/bold]")
    for reason in reasons:
        console.print(f"  • {escape(reason)}")
    console.print("[bold]Limitations and next step:[/bold]")
    for limitation in limitations:
        console.print(f"  • {escape(limitation)}")
    next_step = {
        Decision.NO_SKILL_NEEDED: "Continue without installing a skill.",
        Decision.USE: "Review the linked source and choose manually if the evidence fits your task.",
        Decision.REVIEW: "Resolve the listed unknowns and inspect the source before deciding.",
        Decision.AVOID: "Do not install this inspected revision while the hard block remains.",
    }[decision]
    console.print(f"  • Next step: {next_step}")

