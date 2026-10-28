"""No-skill decisions, evidence assembly, and conservative policy gates."""

from __future__ import annotations

import shlex
from datetime import datetime, timedelta, timezone

from skillscout.models import (
    AuditFinding,
    CandidateReport,
    CatalogSkill,
    Compatibility,
    Decision,
    InspectionEvidence,
    ProjectSnapshot,
    Report,
    Severity,
    SkillDetail,
    SkillInspectionReport,
    utc_now,
)
from skillscout.ranking import relevance_threshold, task_is_routine
from skillscout.scanner import scan_skill

MAX_AUDIT_AGE = timedelta(days=90)


def no_skill_report(
    task: str,
    agent: str,
    project: ProjectSnapshot,
    *,
    reason: str,
    limitations: list[str] | None = None,
) -> Report:
    """Build a deterministic no-skill report without a catalog call."""
    return Report(
        task=task,
        agent=agent,
        project=project,
        decision=Decision.NO_SKILL_NEEDED,
        summary=reason,
        candidates=[],
        limitations=limitations or [],
    )


def incomplete_search_report(
    task: str,
    agent: str,
    project: ProjectSnapshot,
    *,
    limitations: list[str] | None = None,
) -> Report:
    """Report uncertainty when the catalog did not establish a complete search page."""
    return Report(
        task=task,
        agent=agent,
        project=project,
        decision=Decision.REVIEW,
        summary=(
            "The catalog search was incomplete; SkillScraper cannot determine whether a skill would help."
        ),
        candidates=[],
        limitations=[
            *(limitations or []),
            "The catalog indicated that more search results may be available; retry or review "
            "the catalog results manually.",
        ],
    )


def should_skip_catalog_for_task(task: str) -> bool:
    return task_is_routine(task)


def make_inspection_evidence(
    detail: SkillDetail,
    audits: list[AuditFinding],
    audit_availability: str,
    audit_notes: list[str],
    *,
    catalog_hash: str | None = None,
) -> InspectionEvidence:
    """Bind local and external evidence to the exact detail hash when one exists."""
    local_findings, inspected_files, scan_complete = scan_skill(detail)
    inspection_hash = detail.content_hash
    notes = list(audit_notes)
    evidence_status = "complete" if scan_complete else "incomplete"
    if inspection_hash is None:
        notes.append(
            "Detail response did not provide a content hash; inspection cannot be bound to an immutable revision."
        )
        if catalog_hash:
            notes.append(
                "A listing hash was supplied, but it was not used to bind a separate detail response."
            )
    if not scan_complete:
        notes.append(
            "The retrieved file set was missing, oversized, or did not include SKILL.md; inspection is incomplete."
        )

    audit_status = audit_availability
    if audit_availability == "available":
        if (
            not inspection_hash
            or not audits
            or not all(item.revision == inspection_hash for item in audits)
        ):
            audit_status = "unbound"
            notes.append(
                "External audit findings were not associated with the exact inspected content hash."
            )
        elif any(item.audited_at is None for item in audits):
            audit_status = "unbound"
            notes.append("At least one external audit omitted its timestamp; freshness is unknown.")
        elif any(
            _audit_freshness(item.audited_at) == "unbound"
            for item in audits
            if item.audited_at is not None
        ):
            audit_status = "unbound"
            notes.append(
                "At least one external audit timestamp lacks a timezone or is implausibly far in the future."
            )
        elif any(
            _audit_freshness(item.audited_at) == "stale"
            for item in audits
            if item.audited_at is not None
        ):
            audit_status = "stale"
            notes.append(
                "At least one revision-bound external audit is older than the 90-day freshness window."
            )
        else:
            audit_status = "current"

    return InspectionEvidence(
        content_hash=inspection_hash,
        files_inspected=inspected_files,
        inspection_time=utc_now(),
        evidence_status=evidence_status,
        external_audit_status=audit_status,
        external_audits=audits,
        local_findings=local_findings,
        evidence_notes=notes,
    )


def decide_candidate(
    evidence: InspectionEvidence,
    compatibility: Compatibility,
    relevance_score: float,
    task_support_confirmed: bool | None = None,
) -> tuple[Decision, list[str], list[str]]:
    """Apply policy independently of the relevance score and never override a block."""
    reasons: list[str] = []
    limitations: list[str] = [
        "Local content checks are heuristic and do not replace a comprehensive security review.",
        "USE is a recommendation based on the listed evidence, not a guarantee of safety.",
    ]

    critical_local = [
        item for item in evidence.local_findings if item.severity == Severity.CRITICAL
    ]
    current_audits = evidence.external_audits if evidence.external_audit_status == "current" else []
    critical_audit = [
        item
        for item in current_audits
        if item.status.strip().lower() == "fail"
        or (item.risk_level or "").strip().upper() in {"HIGH", "CRITICAL"}
    ]
    if critical_local:
        return (
            Decision.AVOID,
            ["A critical local heuristic finding is present in the inspected revision."],
            limitations,
        )
    if critical_audit:
        return (
            Decision.AVOID,
            ["A current external audit reports a failing or high/critical-risk result."],
            limitations,
        )

    checks = (
        (evidence.evidence_status == "complete", "The complete retrieved file set was inspected."),
        (evidence.content_hash is not None, "The inspection is bound to a catalog content hash."),
        (
            evidence.external_audit_status == "current",
            "External audit results are associated with this exact revision.",
        ),
        (
            bool(current_audits)
            and all(item.status.strip().lower() == "pass" for item in current_audits),
            "All available revision-bound external audits report pass.",
        ),
        (
            not any(
                item.status.strip().lower() == "warn"
                or (item.risk_level or "").strip().upper() == "MEDIUM"
                for item in current_audits
            ),
            "No current external audit reports a warning or medium risk level.",
        ),
        (
            compatibility.status == "compatible",
            "Project compatibility is supported by detected metadata.",
        ),
        (
            not any(
                item.severity in {Severity.MEDIUM, Severity.HIGH}
                for item in evidence.local_findings
            ),
            "No medium or high local heuristic findings require review.",
        ),
        (
            relevance_score >= relevance_threshold(),
            "Task relevance exceeds the initial recommendation threshold.",
        ),
    )
    for passed, explanation in checks:
        if passed:
            reasons.append(explanation)
        else:
            reasons.append(_failed_check_reason(explanation))

    if task_support_confirmed is True:
        reasons.append("SKILL.md documents the requested operation together with task concepts.")
    elif task_support_confirmed is False:
        reasons.append(
            "Not established: SKILL.md content did not provide an operation-and-topic match for the requested task."
        )
    else:
        reasons.append(
            "Not established: task-specific capability could not be confirmed from SKILL.md."
        )

    if all(passed for passed, _ in checks) and task_support_confirmed is True:
        return Decision.USE, reasons, limitations
    if evidence.evidence_status != "complete":
        limitations.append(
            "The candidate was not fully inspected, so it cannot be treated as passed."
        )
    if evidence.content_hash is None:
        limitations.append("The inspected files have no verified revision identity.")
    if evidence.external_audit_status in {"unavailable", "none"}:
        limitations.append("External audit status is unknown because no result was available.")
    elif evidence.external_audit_status == "unbound":
        limitations.append(
            "External audit results are present but their revision association is unknown or stale."
        )
    elif evidence.external_audit_status == "stale":
        limitations.append(
            "External audit results are revision-bound but older than the 90-day freshness window."
        )
    if compatibility.status in {"unknown", "partial"}:
        limitations.append("Project compatibility remains partly unknown.")
    if compatibility.status == "incompatible":
        limitations.append(
            "Detected project metadata conflicts with the skill's stated target framework."
        )
    warnings = [
        item
        for item in evidence.local_findings
        if item.severity in {Severity.MEDIUM, Severity.HIGH}
    ]
    if warnings:
        limitations.append("One or more local heuristic warnings need human review.")
    if task_support_confirmed is False:
        limitations.append(
            "Inspected SKILL.md content did not establish the requested task capability."
        )
    elif task_support_confirmed is None:
        limitations.append(
            "Task-specific capability remains unknown because supporting SKILL.md evidence was unavailable or insufficient."
        )
    return Decision.REVIEW, reasons, limitations


def build_candidate_report(
    *,
    skill: CatalogSkill,
    rank: int,
    relevance_score: float,
    task_fit_rationale: list[str],
    compatibility: Compatibility,
    evidence: InspectionEvidence,
    agent: str,
    task_support_confirmed: bool | None = None,
) -> CandidateReport:
    decision, reasons, limitations = decide_candidate(
        evidence, compatibility, relevance_score, task_support_confirmed
    )
    command = proposed_install_command(skill, agent)
    if not command.startswith("Unavailable:"):
        limitations.append(
            "The proposed install command is inert report text; SkillScraper never executes it."
        )
    return CandidateReport(
        skill=skill,
        rank=rank,
        relevance_score=relevance_score,
        task_fit_rationale=task_fit_rationale,
        compatibility=compatibility,
        evidence=evidence,
        decision=decision,
        decision_reasons=reasons,
        limitations=limitations,
        proposed_install_command=command,
    )


def proposed_install_command(skill: CatalogSkill, agent: str) -> str:
    """Return an inert install command from catalog-supplied install metadata."""
    if not skill.install_url:
        return "Unavailable: the catalog did not supply an install URL."
    values = ["npx", "skills", "add", str(skill.install_url), "--skill", skill.slug]
    if agent:
        values.extend(["--agent", agent])
    return " ".join(shlex.quote(value) for value in values)


def make_inspection_report(
    skill: CatalogSkill,
    evidence: InspectionEvidence,
    compatibility: Compatibility,
    agent: str = "codex",
) -> SkillInspectionReport:
    current_audits = evidence.external_audits if evidence.external_audit_status == "current" else []
    hard_block = any(item.severity == Severity.CRITICAL for item in evidence.local_findings) or any(
        item.status.strip().lower() == "fail"
        or (item.risk_level or "").strip().upper() in {"HIGH", "CRITICAL"}
        for item in current_audits
    )
    decision = Decision.AVOID if hard_block else Decision.REVIEW
    reasons = [
        "Inspection reports evidence only; task relevance has not been assessed.",
        "A hard block was found in current evidence."
        if hard_block
        else "No current hard block was established by this inspection.",
    ]
    limitations = [
        "This inspection is not a recommendation to use the skill for a particular task.",
        "Local content checks are heuristic and do not replace a comprehensive security review.",
    ]
    return SkillInspectionReport(
        skill=skill,
        evidence=evidence,
        compatibility=compatibility,
        decision=decision,
        decision_reasons=reasons,
        limitations=limitations,
        proposed_install_command=proposed_install_command(skill, agent),
    )


def report_for_candidates(
    task: str,
    agent: str,
    project: ProjectSnapshot,
    candidates: list[CandidateReport],
    *,
    limitations: list[str] | None = None,
    search_complete: bool = True,
) -> Report:
    """Choose the overall result from relevance first and policy second."""
    if not candidates:
        if not search_complete:
            return incomplete_search_report(
                task,
                agent,
                project,
                limitations=limitations,
            )
        return no_skill_report(
            task,
            agent,
            project,
            reason="No inspected catalog candidate demonstrated enough additional task value.",
            limitations=limitations,
        )
    best = max(candidates, key=lambda candidate: candidate.relevance_score)
    if best.decision == Decision.AVOID:
        return Report(
            task=task,
            agent=agent,
            project=project,
            decision=Decision.AVOID,
            summary=(
                f"A hard security block applies to the strongest inspected candidate: "
                f"{best.skill.name}."
            ),
            candidates=candidates,
            limitations=limitations or [],
        )
    if best.relevance_score < relevance_threshold():
        if not search_complete:
            return Report(
                task=task,
                agent=agent,
                project=project,
                decision=Decision.REVIEW,
                summary=(
                    "No inspected candidate met the initial relevance threshold, but the catalog "
                    "search may have more results."
                ),
                candidates=candidates,
                limitations=[
                    *(limitations or []),
                    "An incomplete search cannot establish that no useful skill exists.",
                ],
            )
        incomplete = [
            candidate
            for candidate in candidates
            if candidate.evidence.evidence_status != "complete"
        ]
        if incomplete:
            return Report(
                task=task,
                agent=agent,
                project=project,
                decision=Decision.REVIEW,
                summary=(
                    "No inspected candidate met the initial relevance threshold, and at least one "
                    "candidate inspection was incomplete."
                ),
                candidates=candidates,
                limitations=[
                    *(limitations or []),
                    "Incomplete candidate inspection prevents a definitive no-skill decision.",
                ],
            )
        return Report(
            task=task,
            agent=agent,
            project=project,
            decision=Decision.NO_SKILL_NEEDED,
            summary=(
                "No inspected candidate provided enough SKILL.md evidence of the requested operation together with task-specific concepts; "
                f"the strongest inspected match scored {best.relevance_score:.2f}."
            ),
            candidates=candidates,
            limitations=limitations or [],
        )
    decision = best.decision
    summary = f"Best task-fit candidate: {best.skill.name} ({decision.value})."
    if decision.value == "USE":
        supported_reason = next(
            (
                reason
                for reason in best.task_fit_rationale
                if reason.startswith("SKILL.md documents the requested")
            ),
            None,
        )
        if supported_reason:
            summary += f" {supported_reason}"
    elif decision.value == "REVIEW":
        task_gap = next(
            (
                reason
                for reason in best.decision_reasons
                if reason.startswith("Not established: task-specific capability")
                or reason.startswith("Not established: SKILL.md content did not provide")
            ),
            None,
        )
        if task_gap:
            summary = f"{best.skill.name} needs review. {task_gap}"
    return Report(
        task=task,
        agent=agent,
        project=project,
        decision=decision,
        summary=summary,
        candidates=candidates,
        limitations=limitations or [],
    )


def missing_evidence(skill: CatalogSkill, reason: str) -> InspectionEvidence:
    """Create explicit unknown evidence after an inspection failure."""
    return InspectionEvidence(
        content_hash=None,
        files_inspected=[],
        inspection_time=utc_now(),
        evidence_status="unknown",
        external_audit_status="unavailable",
        evidence_notes=[reason],
    )


def _failed_check_reason(explanation: str) -> str:
    return f"Not established: {explanation[0].lower()}{explanation[1:]}"


def _audit_freshness(audited_at: datetime) -> str:
    if audited_at.tzinfo is None or audited_at.utcoffset() is None:
        return "unbound"
    age = datetime.now(timezone.utc) - audited_at
    if age < -timedelta(days=1):
        return "unbound"
    if age > MAX_AUDIT_AGE:
        return "stale"
    return "current"
