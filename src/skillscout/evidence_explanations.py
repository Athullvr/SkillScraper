"""MCP-facing explanations derived from existing inspection and policy evidence."""

from __future__ import annotations

from dataclasses import dataclass

from skillscout.mcp_models import MCPEvidenceStatus, MCPUnresolvedEvidence
from skillscout.models import (
    CandidateReport,
    Compatibility,
    InspectionEvidence,
    Severity,
    SkillInspectionReport,
)


@dataclass(frozen=True)
class EvidenceExplanation:
    statuses: list[MCPEvidenceStatus]
    unresolved: list[MCPUnresolvedEvidence]
    next_steps: list[str]


def explain_candidate(candidate: CandidateReport) -> EvidenceExplanation:
    """Explain a candidate's real evidence without changing its policy decision."""
    return _explain(
        candidate.evidence,
        candidate.compatibility,
        candidate.decision_reasons,
    )


def explain_inspection(report: SkillInspectionReport) -> EvidenceExplanation:
    """Explain inspect-only evidence, where task relevance is not assessed."""
    return _explain(
        report.evidence,
        report.compatibility,
        report.decision_reasons,
        inspection_only=True,
    )


def _explain(
    evidence: InspectionEvidence,
    compatibility: Compatibility,
    decision_reasons: list[str],
    *,
    inspection_only: bool = False,
) -> EvidenceExplanation:
    statuses: list[MCPEvidenceStatus] = []
    unresolved: list[MCPUnresolvedEvidence] = []

    statuses.append(
        MCPEvidenceStatus(
            category="skill_identity_source",
            status="catalog_metadata",
            summary=(
                "The stable ID and source came from catalog metadata; repository ownership and "
                "content authenticity were not independently verified."
            ),
        )
    )

    if evidence.evidence_status == "complete":
        inspection_summary = (
            "The retrieved file set stayed within scanner limits and included a non-empty SKILL.md."
        )
    elif evidence.evidence_status == "incomplete":
        inspection_summary = (
            "The retrieved file set was incomplete under the scanner's file or size limits, "
            "or did not include usable SKILL.md content."
        )
    else:
        inspection_summary = "Detail content was not available for inspection."
    statuses.append(
        MCPEvidenceStatus(
            category="content_inspection",
            status=evidence.evidence_status,
            summary=inspection_summary,
        )
    )
    if evidence.evidence_status != "complete":
        if evidence.evidence_status == "unknown":
            reason = "No skill file set was inspected because detail retrieval failed."
            steps = [
                "Retry after catalog access is available; if detail remains unavailable, review the linked source manually.",
                "Do not treat missing content as a clean inspection; SkillScraper cannot inspect content it could not retrieve.",
            ]
        else:
            reason = (
                "The retrieved file set did not satisfy the scanner's completeness requirements."
            )
            steps = [
                "Retry detail retrieval and check whether the catalog returns the full bounded file set, including non-empty SKILL.md.",
                "If it remains incomplete or exceeds scanner limits, review the source manually; SkillScraper cannot certify this snapshot as complete.",
            ]
        unresolved.append(
            MCPUnresolvedEvidence(
                category="content_inspection",
                status=evidence.evidence_status,
                reason=reason,
                next_steps=steps,
            )
        )

    if evidence.content_hash:
        statuses.append(
            MCPEvidenceStatus(
                category="content_revision",
                status="hash_present",
                summary=(
                    "The hash identifies the detail content used for this inspection; audit "
                    "eligibility is reported separately."
                ),
            )
        )
    else:
        statuses.append(
            MCPEvidenceStatus(
                category="content_revision",
                status="missing",
                summary="The inspected detail response did not include a content hash.",
            )
        )
        unresolved.append(
            MCPUnresolvedEvidence(
                category="content_revision",
                status="missing",
                reason=(
                    "The inspected detail has no content hash, so this evidence cannot be tied "
                    "to an immutable revision."
                ),
                next_steps=[
                    "Retrieve a detail response that includes its content hash, then re-inspect that exact content.",
                    "Do not substitute a listing hash or an audit hash for the missing detail hash.",
                ],
            )
        )

    audit_status, audit_summary, audit_gap = _audit_explanation(evidence)
    statuses.append(
        MCPEvidenceStatus(category="external_audit", status=audit_status, summary=audit_summary)
    )
    if audit_gap is not None:
        unresolved.append(audit_gap)

    findings = evidence.local_findings
    critical = [item for item in findings if item.severity == Severity.CRITICAL]
    review_findings = [
        item for item in findings if item.severity in {Severity.MEDIUM, Severity.HIGH}
    ]
    if critical:
        security_status = "critical_findings"
        security_summary = "The heuristic scanner found a critical pattern; this is a hard block under the existing policy."
    elif review_findings:
        security_status = "findings"
        security_summary = (
            "The heuristic scanner found medium or high findings that require review."
        )
    elif findings:
        security_status = "findings"
        security_summary = "The heuristic scanner reported findings; its checks are not a comprehensive security review."
    elif evidence.evidence_status == "complete":
        security_status = "no_findings_detected"
        security_summary = "No heuristic finding was detected in the inspected files; this does not establish safety."
    else:
        security_status = "unknown"
        security_summary = (
            "No finding was reported in inspected files, but inspection coverage is incomplete."
        )
    statuses.append(
        MCPEvidenceStatus(
            category="security_scan", status=security_status, summary=security_summary
        )
    )
    if critical:
        unresolved.append(
            MCPUnresolvedEvidence(
                category="security_scan",
                status="critical_findings",
                reason=(
                    "A critical local heuristic finding is present; the current policy returns AVOID."
                ),
                next_steps=[
                    "Do not use this inspected revision; manually examine the file-level finding and its source context.",
                    "If a materially changed revision is considered, retrieve and inspect it again and obtain audit evidence bound to that new hash.",
                ],
            )
        )
    elif review_findings:
        unresolved.append(
            MCPUnresolvedEvidence(
                category="security_scan",
                status="findings",
                reason=(
                    "Medium or high heuristic findings prevent a USE decision until they are reviewed."
                ),
                next_steps=[
                    "Inspect each reported file and decide whether the pattern is expected or risky; the scanner does not locate a precise line.",
                    "Re-inspect after any content change because prior evidence is revision-specific.",
                ],
            )
        )
    elif findings:
        unresolved.append(
            MCPUnresolvedEvidence(
                category="security_scan",
                status="findings",
                reason="The heuristic scanner reported a finding that should be interpreted in its file context.",
                next_steps=[
                    "Review the reported file and scanner summary; a heuristic finding is neither proof of harm nor proof of safety."
                ],
            )
        )

    compatibility_status = compatibility.status
    compatibility_summaries = {
        "compatible": "Detected project metadata supports the skill's stated framework, subject to listed runtime unknowns.",
        "partial": "The available project metadata supports only partial compatibility.",
        "unknown": "Project compatibility could not be established from the available metadata.",
        "incompatible": "Detected project metadata conflicts with the skill's stated framework.",
    }
    statuses.append(
        MCPEvidenceStatus(
            category="project_compatibility",
            status=compatibility_status,
            summary=compatibility_summaries[compatibility_status],
        )
    )
    if compatibility_status != "compatible":
        if compatibility_status == "incompatible":
            compatibility_reason = (
                "Detected metadata conflicts with the skill's stated target framework."
            )
            compatibility_steps = [
                "Confirm that the project intentionally uses both stacks or choose a skill for the detected framework.",
                "SkillScraper reads bounded metadata only and cannot verify a mixed stack by inspecting source files or running the project.",
            ]
        else:
            compatibility_reason = (
                "Available project metadata does not establish compatibility with this skill."
            )
            compatibility_steps = [
                "Confirm the project's framework and relevant versions from its declared metadata.",
                "If compatibility depends on source or runtime behavior, review that manually; SkillScraper does not inspect or execute project source.",
            ]
        unresolved.append(
            MCPUnresolvedEvidence(
                category="project_compatibility",
                status=compatibility_status,
                reason=compatibility_reason,
                next_steps=compatibility_steps,
            )
        )

    if inspection_only:
        relevance_status = "not_assessed"
        relevance_summary = "This inspection did not compare the skill to a specific coding task."
        relevance_gap = MCPUnresolvedEvidence(
            category="task_relevance",
            status="not_assessed",
            reason="Task relevance is not assessed by inspect_skill.",
            next_steps=[
                "Call prepare_task with the concrete coding task and project context to assess relevance."
            ],
        )
        unresolved.append(relevance_gap)
    elif any(
        reason.startswith("Not established: task-specific capability could not be confirmed")
        for reason in decision_reasons
    ):
        relevance_status = "not_assessed"
        relevance_summary = (
            "Task-specific capability could not be confirmed from SKILL.md; catalog listing overlap alone cannot establish it."
        )
        unresolved.append(
            MCPUnresolvedEvidence(
                category="task_relevance",
                status="not_assessed",
                reason=(
                    "Detail content did not establish whether the skill documents the requested operation for these task concepts."
                ),
                next_steps=[
                    "Review the candidate's SKILL.md instructions for the requested operation and task concepts.",
                    "Treat missing or incomplete instructions as unknown; do not infer task fit from the title alone.",
                ],
            )
        )
    elif any(
        reason.startswith("Not established: SKILL.md content did not provide")
        for reason in decision_reasons
    ):
        relevance_status = "below_threshold"
        relevance_summary = (
            "SKILL.md did not establish the requested operation together with task-specific concepts, so the candidate remains below the relevance threshold."
        )
        unresolved.append(
            MCPUnresolvedEvidence(
                category="task_relevance",
                status="below_threshold",
                reason=(
                    "The inspected SKILL.md did not establish a task-specific capability, and the candidate did not meet the initial task-relevance threshold."
                ),
                next_steps=[
                    "Until review confirms a direct operation match, continue without this skill.",
                    "A title or broad topic match alone does not establish task fit.",
                ],
            )
        )
    elif any(
        "task relevance exceeds the initial recommendation threshold" in reason.lower()
        and not reason.lower().startswith("not established:")
        for reason in decision_reasons
    ):
        relevance_status = "sufficient"
        if any(
            reason == "SKILL.md documents the requested operation together with task concepts."
            for reason in decision_reasons
        ):
            relevance_summary = (
                "SKILL.md documents the requested operation together with task-specific concepts, and the relevance score exceeds the initial threshold."
            )
        else:
            relevance_summary = "The existing decision reasons establish task relevance above the current initial threshold."
    elif any(
        reason.lower().startswith(
            "not established: task relevance exceeds the initial recommendation threshold"
        )
        for reason in decision_reasons
    ):
        relevance_status = "below_threshold"
        relevance_summary = "The existing decision reasons do not establish task relevance above the current initial threshold."
        relevance_gap = MCPUnresolvedEvidence(
            category="task_relevance",
            status="below_threshold",
            reason="The candidate did not meet SkillScraper's initial task-relevance threshold.",
            next_steps=[
                "If the task has a concrete framework or specialist constraint, include it and request a fresh recommendation; otherwise continue without this skill.",
                "Do not treat a low-relevance candidate's other evidence as a reason to use it.",
            ],
        )
        unresolved.append(relevance_gap)
    else:
        relevance_status = "not_assessed"
        relevance_summary = "Task relevance was not established in the decision reasons, including when a hard security block short-circuited the gates."

    statuses.append(
        MCPEvidenceStatus(
            category="task_relevance",
            status=relevance_status,
            summary=relevance_summary,
        )
    )

    next_steps: list[str] = []
    seen: set[str] = set()
    for item in unresolved:
        for step in item.next_steps:
            if step not in seen:
                next_steps.append(step)
                seen.add(step)
            if len(next_steps) >= 12:
                break
        if len(next_steps) >= 12:
            break

    return EvidenceExplanation(statuses=statuses, unresolved=unresolved, next_steps=next_steps)


def _audit_explanation(
    evidence: InspectionEvidence,
) -> tuple[str, str, MCPUnresolvedEvidence | None]:
    audits = evidence.external_audits
    if evidence.external_audit_status == "current":
        concerning = [
            item
            for item in audits
            if item.status.strip().lower() != "pass"
            or (item.risk_level or "").strip().upper() in {"MEDIUM", "HIGH", "CRITICAL"}
        ]
        if not concerning:
            return (
                "current_passing",
                "Available audit findings are dated, within the freshness window, and bound to the inspected hash; all report pass without medium-or-higher risk.",
                None,
            )
        has_hard_block = any(
            item.status.strip().lower() == "fail"
            or (item.risk_level or "").strip().upper() in {"HIGH", "CRITICAL"}
            for item in concerning
        )
        gap = MCPUnresolvedEvidence(
            category="external_audit",
            status="current_findings",
            reason=(
                "Current, revision-bound audit evidence contains a finding that is not an unqualified pass."
            ),
            next_steps=[
                "Review the audit provider's finding for this exact content hash; a pass from another revision does not resolve it.",
                "Do not use this revision while a failing or high-risk audit finding remains; any changed revision needs a fresh matching audit.",
            ]
            if has_hard_block
            else [
                "Review the audit warning or medium-risk result for this exact content hash and obtain clarification if needed.",
                "A newer audit may address freshness or interpretation, but it does not itself prove the skill safe.",
            ],
        )
        return (
            "current_findings",
            "Current, revision-bound audit evidence contains a warning, non-pass status, or medium-or-higher risk result.",
            gap,
        )

    if evidence.external_audit_status == "stale":
        gap = MCPUnresolvedEvidence(
            category="external_audit",
            status="stale",
            reason="The revision-bound audit is older than SkillScraper's 90-day freshness window.",
            next_steps=[
                "Obtain a fresh audit for the exact inspected content hash and re-run inspection.",
                "Until then, treat the old audit as stale evidence, not as a current pass.",
            ],
        )
        return (
            "stale",
            "The audit is revision-bound but older than the configured 90-day freshness window.",
            gap,
        )

    if evidence.external_audit_status == "unbound":
        mismatched = bool(
            evidence.content_hash
            and any(item.revision and item.revision != evidence.content_hash for item in audits)
        )
        if mismatched:
            status = "mismatched"
            summary = (
                "At least one audit revision differs from the content hash that was inspected."
            )
            reason = "The available audit does not apply to the inspected content hash."
            steps = [
                "Obtain audit evidence whose revision exactly matches the detail content hash reported here.",
                "Do not use the mismatched audit as evidence for this revision; re-inspect if the content changes.",
            ]
        else:
            status = "unbound"
            summary = "Audit revision or timestamp information is missing or invalid, so applicability or freshness cannot be established."
            reason = "Available audit evidence cannot be fully bound to a fresh inspected revision."
            steps = [
                "Obtain audit evidence with a revision matching the inspected hash and a valid timezone-aware timestamp.",
                "If such evidence is not available, have the exact revision reviewed independently; SkillScraper cannot create audits.",
            ]
        return (
            status,
            summary,
            MCPUnresolvedEvidence(
                category="external_audit", status=status, reason=reason, next_steps=steps
            ),
        )

    if evidence.external_audit_status == "unavailable":
        gap = MCPUnresolvedEvidence(
            category="external_audit",
            status="unavailable",
            reason="The catalog did not provide usable audit evidence for this inspection.",
            next_steps=[
                "Retry when authorized catalog access is available, or arrange an independent review tied to the inspected hash.",
                "SkillScraper cannot issue an audit; missing audit data is not a pass.",
            ],
        )
        return (
            "unavailable",
            "Audit lookup was unavailable or its response could not be validated.",
            gap,
        )

    gap = MCPUnresolvedEvidence(
        category="external_audit",
        status="none",
        reason="No external audit result is available for the inspected candidate.",
        next_steps=[
            "Obtain applicable audit evidence for the exact inspected content hash, or arrange an independent review.",
            "The product cannot request or generate an audit, and absence of an audit is not a safety finding.",
        ],
    )
    return "none", "No external audit results were available for the inspected candidate.", gap
