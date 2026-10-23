"""Bounded, deterministic excerpts from content already retrieved and inspected."""

from __future__ import annotations

import re
from dataclasses import dataclass

from skillscout.mcp_models import (
    MAX_EVIDENCE_EXCERPT_BYTES,
    MAX_EVIDENCE_EXCERPTS,
    MAX_EVIDENCE_PACKET_BYTES,
    MCPExcerptWarning,
    MCPTaskEvidenceExcerpt,
    MCPTaskEvidencePacket,
)
from skillscout.models import CandidateReport, ProjectSnapshot, SkillDetail
from skillscout.ranking import (
    CAPABILITY_LABELS,
    capability_match_for_excerpt,
    evidence_excerpt_match,
)

DEFAULT_EVIDENCE_PACKET_BYTES = 1_200
MAX_EXCERPT_BYTES = MAX_EVIDENCE_EXCERPT_BYTES
WINDOW_OVERLAP_BYTES = 96
HEADING = re.compile(r"^\s{0,3}#{1,6}[ \t]+(.+?)[ \t]*\r?\n?$")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class _SectionParagraph:
    path: str
    heading: str | None
    section_index: int
    paragraph_index: int
    text: str


@dataclass(frozen=True)
class _ScoredExcerpt:
    score: float
    file_order: int
    section_index: int
    paragraph_index: int
    window_index: int
    path: str
    heading: str | None
    text: str
    task_matches: tuple[str, ...]
    project_matches: tuple[str, ...]
    capability_groups: tuple[str, ...]
    source_truncated: bool


def build_evidence_packet(
    candidate: CandidateReport,
    detail: SkillDetail | None,
    task: str,
    project: ProjectSnapshot,
    budget_bytes: int = DEFAULT_EVIDENCE_PACKET_BYTES,
) -> MCPTaskEvidencePacket:
    """Select a few exact, task-relevant strings from this candidate's inspected detail."""
    content_hash = candidate.evidence.content_hash
    base = {
        "skill_id": candidate.skill.id,
        "source": candidate.skill.source,
        "content_hash": content_hash,
        "revision_binding": "bound" if content_hash else "unbound",
        "budget_bytes": budget_bytes,
        "used_bytes": 0,
        "excerpts": [],
        "truncated": False,
    }
    if budget_bytes == 0:
        return MCPTaskEvidencePacket(
            **base,
            status="disabled",
            explanation="Evidence excerpts were disabled by a zero-byte budget.",
        )
    if budget_bytes > MAX_EVIDENCE_PACKET_BYTES:
        raise ValueError(f"budget_bytes must not exceed {MAX_EVIDENCE_PACKET_BYTES}")
    if detail is None:
        return MCPTaskEvidencePacket(
            **base,
            status="unavailable",
            explanation="Catalog detail was not retrieved, so no inspected text is available.",
        )

    same_identity = (
        detail.id == candidate.skill.id
        and detail.source == candidate.skill.source
        and detail.slug == candidate.skill.slug
    )
    same_revision = detail.content_hash == candidate.evidence.content_hash
    if not same_identity or not same_revision:
        return MCPTaskEvidencePacket(
            **{
                **base,
                "content_hash": None,
                "revision_binding": "unbound",
            },
            status="unavailable",
            explanation=(
                "Retrieved detail identity or hash did not match the inspection evidence; "
                "SkillScout omitted excerpts to avoid mixing revisions."
            ),
        )

    inspected = set(candidate.evidence.files_inspected)
    source_files = []
    skipped_unrepresentable = False
    for file in detail.files or []:
        if file.path not in inspected:
            continue
        if CONTROL_CHARS.search(file.path) or not _is_utf8_encodable(file.path):
            skipped_unrepresentable = True
            continue
        if not _is_utf8_encodable(file.contents):
            skipped_unrepresentable = True
            continue
        source_files.append(file)
    if not source_files:
        return MCPTaskEvidencePacket(
            **{**base, "truncated": skipped_unrepresentable},
            status="empty",
            explanation=(
                "No inspected file was eligible for excerpt selection because the pipeline "
                "recorded no files or its available text could not be represented safely."
            ),
        )

    scored: list[_ScoredExcerpt] = []
    for file_order, skill_file in enumerate(source_files):
        for paragraph in _paragraphs(skill_file.path, skill_file.contents):
            for window_index, (window, source_truncated) in enumerate(_windows(paragraph.text)):
                score, task_matches, project_matches = evidence_excerpt_match(task, project, window)
                if score <= 0:
                    continue
                capability_groups, _ = capability_match_for_excerpt(task, window)
                scored.append(
                    _ScoredExcerpt(
                        score=score,
                        file_order=file_order,
                        section_index=paragraph.section_index,
                        paragraph_index=paragraph.paragraph_index,
                        window_index=window_index,
                        path=paragraph.path,
                        heading=paragraph.heading,
                        text=window,
                        task_matches=tuple(task_matches),
                        project_matches=tuple(project_matches),
                        capability_groups=tuple(capability_groups),
                        source_truncated=source_truncated,
                    )
                )

    if not scored:
        return MCPTaskEvidencePacket(
            **{**base, "truncated": skipped_unrepresentable},
            status="empty",
            explanation=(
                "No inspected paragraph matched task terms under the deterministic selector, "
                "so SkillScout could not justify an excerpt."
            ),
        )

    # Preserve existing ranking's normalized term overlap, with stable source-order ties.
    scored.sort(
        key=lambda item: (
            -item.score,
            item.file_order,
            item.section_index,
            item.paragraph_index,
            item.window_index,
        )
    )
    findings_by_file: dict[str, list] = {}
    for finding in candidate.evidence.local_findings:
        if finding.file_path:
            findings_by_file.setdefault(finding.file_path, []).append(finding)

    excerpts: list[MCPTaskEvidenceExcerpt] = []
    seen_sections: set[tuple[str, int]] = set()
    seen_text: set[str] = set()
    used_bytes = 0
    truncated = skipped_unrepresentable or len(scored) > MAX_EVIDENCE_EXCERPTS
    for item in scored:
        section_key = (item.path, item.section_index)
        duplicate_key = item.text.strip().casefold()
        if section_key in seen_sections or duplicate_key in seen_text:
            truncated = True
            continue
        remaining = budget_bytes - used_bytes
        if remaining <= 0:
            truncated = True
            break

        excerpt_text = item.text
        clipped_by_budget = len(excerpt_text.encode("utf-8")) > remaining
        if clipped_by_budget:
            excerpt_text = _utf8_prefix(excerpt_text, remaining)
            score, task_matches, project_matches = evidence_excerpt_match(
                task, project, excerpt_text
            )
            capability_groups, _ = capability_match_for_excerpt(task, excerpt_text)
            # A cut that removes every matching term is not useful enough to return.
            if score <= 0 or len(excerpt_text.strip()) < 24:
                truncated = True
                continue
        else:
            task_matches = list(item.task_matches)
            project_matches = list(item.project_matches)
            capability_groups = list(item.capability_groups)

        if not excerpt_text:
            truncated = True
            continue
        warnings = [
            MCPExcerptWarning(
                kind="scanner_finding",
                check_id=finding.check_id,
                severity=finding.severity,
                summary=finding.summary,
            )
            for finding in findings_by_file.get(item.path, [])
        ]
        if candidate.evidence.evidence_status != "complete":
            warnings.append(
                MCPExcerptWarning(
                    kind="inspection_coverage",
                    summary=(
                        "Inspection was incomplete; this excerpt came from a recorded scanned file, "
                        "but other content may be missing."
                    ),
                )
            )
        warnings_truncated = len(warnings) > 10
        warnings = warnings[:10]
        relevance_reason = _relevance_reason(
            task_matches, project_matches, capability_groups
        )
        excerpt_bytes = len(excerpt_text.encode("utf-8"))
        excerpts.append(
            MCPTaskEvidenceExcerpt(
                file_path=item.path,
                section_heading=(
                    item.heading
                    if item.heading is not None
                    and len(item.heading) <= 300
                    and not CONTROL_CHARS.search(item.heading)
                    and _is_utf8_encodable(item.heading)
                    else None
                ),
                section_heading_omitted=bool(
                    item.heading is not None
                    and (
                        len(item.heading) > 300
                        or CONTROL_CHARS.search(item.heading)
                        or not _is_utf8_encodable(item.heading)
                    )
                ),
                excerpt=excerpt_text,
                relevance_reason=relevance_reason,
                warnings=warnings,
                warnings_truncated=warnings_truncated,
                truncated=item.source_truncated or clipped_by_budget,
            )
        )
        used_bytes += excerpt_bytes
        seen_sections.add(section_key)
        seen_text.add(duplicate_key)
        if item.source_truncated or clipped_by_budget:
            truncated = True
        if len(excerpts) >= MAX_EVIDENCE_EXCERPTS:
            if len(scored) > len(excerpts):
                truncated = True
            break

    if not excerpts:
        return MCPTaskEvidencePacket(
            **{**base, "truncated": truncated},
            status="empty",
            explanation=(
                "Relevant text was found, but no excerpt containing its matching terms fit "
                "the configured byte budget."
            ),
        )
    return MCPTaskEvidencePacket(
        **{
            **base,
            "used_bytes": used_bytes,
            "truncated": truncated,
            "excerpts": excerpts,
        },
        status="available",
        explanation=(
            "Selected verbatim paragraphs by deterministic task-term overlap, with a smaller "
            "preference for declared project technology."
        ),
    )


def _paragraphs(path: str, text: str) -> list[_SectionParagraph]:
    lines = text.splitlines(keepends=True)
    result: list[_SectionParagraph] = []
    heading: str | None = None
    section_index = 0
    paragraph_index = 0
    pending: list[str] = []
    fence_character: str | None = None
    fence_length = 0

    def flush() -> None:
        nonlocal paragraph_index
        if pending:
            paragraph = "".join(pending)
            if paragraph.strip():
                result.append(
                    _SectionParagraph(
                        path=path,
                        heading=heading,
                        section_index=section_index,
                        paragraph_index=paragraph_index,
                        text=paragraph,
                    )
                )
                paragraph_index += 1
            pending.clear()

    for line in lines:
        fence = FENCE.match(line)
        if fence:
            marker = fence.group(1)
            character = marker[0]
            if fence_character is None:
                fence_character, fence_length = character, len(marker)
            elif character == fence_character and len(marker) >= fence_length:
                fence_character, fence_length = None, 0
            pending.append(line)
            continue

        match = HEADING.match(line) if fence_character is None else None
        if match:
            flush()
            section_index += 1
            paragraph_index = 0
            heading = match.group(1).strip()
            continue
        if not line.strip():
            flush()
            continue
        pending.append(line)
    flush()
    return result


def _windows(text: str):
    """Yield exact Unicode-safe windows no larger than MAX_EXCERPT_BYTES."""
    if len(text.encode("utf-8")) <= MAX_EXCERPT_BYTES:
        yield text, False
        return

    start = 0
    while start < len(text):
        end = start
        used = 0
        while end < len(text):
            width = len(text[end].encode("utf-8"))
            if used + width > MAX_EXCERPT_BYTES:
                break
            used += width
            end += 1
        if end == start:
            break
        yield text[start:end], True
        if end == len(text):
            break

        next_start = end
        overlap = 0
        while next_start > start and overlap < WINDOW_OVERLAP_BYTES:
            next_start -= 1
            overlap += len(text[next_start].encode("utf-8"))
        while next_start < end and not text[next_start].isspace():
            next_start += 1
        start = max(start + 1, next_start)


def _utf8_prefix(text: str, max_bytes: int) -> str:
    """Return an exact codepoint-aligned prefix within a UTF-8 byte limit."""
    result: list[str] = []
    used = 0
    for character in text:
        width = len(character.encode("utf-8"))
        if used + width > max_bytes:
            break
        result.append(character)
        used += width
    return "".join(result)


def _relevance_reason(
    task_matches: list[str], project_matches: list[str], capability_groups: list[str]
) -> str:
    parts: list[str] = []
    if capability_groups:
        labels = ", ".join(
            CAPABILITY_LABELS[group]
            for group in sorted(capability_groups)
            if group in CAPABILITY_LABELS
        )
        if labels:
            parts.append(f"{labels} instructions")
    if task_matches:
        parts.append(f"task concepts: {_bounded_terms(task_matches)}")
    if project_matches:
        parts.append(f"declared project technology: {_bounded_terms(project_matches)}")
    return "Matches " + " and ".join(parts) + "."


def _bounded_terms(terms: list[str]) -> str:
    visible = [term for term in terms if len(term) <= 40 and not CONTROL_CHARS.search(term)]
    if not visible:
        return "normalized matches (long terms omitted)"
    return ", ".join(visible[:8])


def _is_utf8_encodable(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
