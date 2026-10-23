"""Interpretable task relevance and project compatibility signals."""

from __future__ import annotations

import re
from dataclasses import dataclass

from skillscout.llm import redact_secrets
from skillscout.models import CatalogSkill, Compatibility, ProjectSnapshot, SkillDetail

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "into",
    "is",
    "it",
    "make",
    "my",
    "of",
    "on",
    "or",
    "our",
    "please",
    "that",
    "the",
    "this",
    "to",
    "use",
    "using",
    "we",
    "with",
    "you",
    "your",
    "implement",
    "build",
    "add",
    "create",
    "write",
    "update",
    "fix",
    "help",
    "need",
    "want",
    "task",
    "project",
}
GENERIC_SKILL_TERMS = {
    "assistant",
    "coding",
    "code",
    "developer",
    "general",
    "programming",
    "software",
}
BROAD_TASK_TERMS = {
    "app",
    "application",
    "code",
    "coding",
    "developer",
    "feature",
    "general",
    "guide",
    "programming",
    "project",
    "service",
    "skill",
    "software",
    "solution",
    "system",
    "tool",
}
CAPABILITY_ACTIONS = {
    "implementation": re.compile(
        r"\b(?:implement(?:s|ed|ing)?|build(?:s|ing)?|built|"
        r"creat(?:e|es|ed|ing)|add(?:s|ed|ing)|configur(?:e|es|ed|ing)|"
        r"sets?\s+up|integrat(?:e|es|ed|ing)|writ(?:e|es|ing)|wrote|"
        r"scaffold(?:s|ed|ing)|enabl(?:e|es|ed|ing)|provid(?:e|es|ed|ing)|"
        r"support(?:s|ed|ing))\b",
        re.IGNORECASE,
    ),
    "diagnosis": re.compile(
        r"\b(?:debug\w*|diagnos\w*|troubleshoot\w*|fix\w*|repair\w*|"
        r"resolv\w*|investigat\w*)\b",
        re.IGNORECASE,
    ),
    "testing": re.compile(
        r"\b(?:test\w*|validat\w*|verif\w*|assert\w*|check\w*)\b",
        re.IGNORECASE,
    ),
    "migration": re.compile(
        r"\b(?:migrat\w*|upgrad\w*|downgrad\w*|port\w*|convert\w*)\b",
        re.IGNORECASE,
    ),
    "performance": re.compile(
        r"\b(?:optimi[sz]\w*|profil\w*|benchmark\w*|reduc\w*|speed\w*)\b",
        re.IGNORECASE,
    ),
    "security": re.compile(
        r"\b(?:secur(?:e|es|ed|ing)|harden\w*|protect\w*|audit\w*|mitigat\w*|"
        r"threat\s+model\w*|prevent\w*)\b",
        re.IGNORECASE,
    ),
    "deployment": re.compile(
        r"\b(?:deploy\w*|releas\w*|host\w*|containeri[sz]\w*|publish\w*|ship\w*)\b",
        re.IGNORECASE,
    ),
    "documentation": re.compile(
        r"\b(?:document\w*|explain\w*|describ\w*|write\s+(?:docs|documentation))\b",
        re.IGNORECASE,
    ),
    "design": re.compile(r"\b(?:design\w*|architect\w*|plan\w*)\b", re.IGNORECASE),
    "analysis": re.compile(
        r"\b(?:analy[sz]\w*|inspect\w*|examin\w*|review\w*)\b", re.IGNORECASE
    ),
}
CAPABILITY_LABELS = {
    "implementation": "implementation",
    "diagnosis": "diagnosis",
    "testing": "testing",
    "migration": "migration",
    "performance": "performance tuning",
    "security": "security hardening",
    "deployment": "deployment",
    "documentation": "documentation",
    "design": "design",
    "analysis": "analysis and review",
}
FRAMEWORKS = {
    "react",
    "nextjs",
    "next",
    "vue",
    "nuxt",
    "angular",
    "svelte",
    "astro",
    "django",
    "fastapi",
    "flask",
    "starlette",
    "sqlalchemy",
    "pydantic",
    "express",
    "nestjs",
    "nodejs",
    "python",
    "typescript",
}
EXCLUSIVE_FRAMEWORK_GROUPS = (
    {"react", "vue", "angular", "svelte", "astro"},
    {"django", "fastapi", "flask"},
)
ALIASES = {
    "oauth": {"auth", "authentication", "authorization"},
    "auth": {"oauth", "authentication", "authorization"},
    "authentication": {"oauth", "auth", "authorization"},
    "authorization": {"oauth", "auth", "authentication"},
    "postgres": {"postgresql", "sql"},
    "postgresql": {"postgres", "sql"},
    "accessibility": {"a11y", "wcag"},
    "a11y": {"accessibility", "wcag"},
    "k8s": {"kubernetes"},
    "kubernetes": {"k8s"},
    "performance": {"optimization", "profiling"},
    "security": {"secure", "vulnerability", "threat"},
    "test": {"testing", "pytest", "jest"},
}
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#._-]{1,}", re.IGNORECASE)
MAX_RANKING_DOCUMENT_CHARS = 20_000
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}[ \t]+(.+?)[ \t]*$")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")
CLAUSE_SPLIT_RE = re.compile(r"\b(?:but|however|although|though|instead)\b|[,;:]", re.IGNORECASE)
NEGATION_RE = re.compile(
    r"\b(?:not(?!\s+only)|never|cannot|can't|can\s+not|don't|do\s+not|"
    r"doesn't|does\s+not|without|avoid|lacks?|lack|fails?\s+to)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RankedSkill:
    skill: CatalogSkill
    score: float
    rationale: list[str]
    listing_score: float | None = None
    capability_supported: bool | None = None


def rank_catalog_results(task: str, skills: list[CatalogSkill]) -> list[RankedSkill]:
    """Rank listing results by direct task wording before weaker related wording."""
    ranked: list[RankedSkill] = []
    for index, skill in enumerate(skills):
        searchable = " ".join(
            (skill.name, skill.slug.replace("-", " "), skill.description or "")
        )
        score, rationale = _weighted_score(task, searchable, source="catalog listing")
        generic_terms = _terms(f"{skill.name} {skill.slug}") & GENERIC_SKILL_TERMS
        if generic_terms:
            score = max(0.0, score - 0.12)
            rationale.append(
                "Generic skill wording adds little beyond an agent's ordinary coding capability."
            )
        # Catalog order is a weak tie-break signal and never creates task relevance by itself.
        if score > 0:
            order_bonus = max(0.0, 0.03 - index * 0.003)
            score = min(1.0, score + order_bonus)
            if order_bonus:
                rationale.append("Catalog search order added a small tie-break signal.")
        if not rationale:
            rationale.append("No meaningful task terms were found in the catalog listing.")
        score = round(score, 3)
        ranked.append(
            RankedSkill(
                skill=skill,
                score=score,
                rationale=rationale,
                listing_score=score,
            )
        )
    return sorted(ranked, key=lambda item: item.score, reverse=True)


def refine_rank(task: str, ranked: RankedSkill, detail: SkillDetail) -> RankedSkill:
    """Let documented capability evidence confirm or demote a listing-level match."""
    document = next(
        (
            file.contents[:MAX_RANKING_DOCUMENT_CHARS]
            for file in (detail.files or [])
            if file.path.lower() == "skill.md"
        ),
        "",
    )
    if not document:
        return RankedSkill(
            skill=ranked.skill,
            score=ranked.score,
            rationale=[
                *ranked.rationale,
                "Task-specific capability could not be confirmed because SKILL.md content was unavailable.",
            ],
            listing_score=ranked.listing_score,
            capability_supported=None,
        )

    listing_score = ranked.listing_score if ranked.listing_score is not None else ranked.score
    document_score, document_rationale = _weighted_score(task, document, source="SKILL.md")
    if _terms(f"{ranked.skill.name} {ranked.skill.slug}") & GENERIC_SKILL_TERMS:
        document_score = max(0.0, document_score - 0.12)
    capability_supported, matched_groups, matched_terms = _documented_task_capability(
        task, document
    )
    score = listing_score * 0.2 + document_score * 0.8
    rationale = [*ranked.rationale]
    rationale.extend(document_rationale[:2])
    if capability_supported:
        score = min(1.0, score + 0.1)
        labels = ", ".join(CAPABILITY_LABELS[group] for group in sorted(matched_groups))
        rationale.append(
            "SKILL.md documents the requested "
            f"{labels} capability with task concepts: {_bounded_terms(matched_terms)}."
        )
    else:
        score = min(score, relevance_threshold() - 0.01)
        if matched_groups:
            labels = ", ".join(CAPABILITY_LABELS[group] for group in sorted(matched_groups))
            rationale.append(
                f"SKILL.md documents {labels}, but does not cover every requested operation."
            )
        elif document_rationale:
            rationale.append(
                "SKILL.md contains topical overlap, but no instruction matching the requested "
                "operation and task concepts was found."
            )
        else:
            rationale.append(
                "SKILL.md did not establish task-specific concepts together with a matching "
                "capability instruction."
            )
    return RankedSkill(
        skill=ranked.skill,
        score=round(score, 3),
        rationale=rationale,
        listing_score=round(listing_score, 3),
        capability_supported=capability_supported,
    )


def evidence_excerpt_match(
    task: str,
    project: ProjectSnapshot,
    excerpt: str,
) -> tuple[float, list[str], list[str]]:
    """Score an excerpt using the same deterministic terms as catalog ranking.

    Task overlap is the primary signal. Declared project metadata adds a smaller
    preference for technology-specific passages. Returned terms are the actual
    normalized overlaps used to explain selection.
    """
    task_terms = _terms(redact_secrets(task))
    project_terms = _terms(" ".join(project.facts))
    excerpt_terms = _terms(excerpt)
    task_matches = sorted(task_terms & excerpt_terms)
    project_matches = sorted(project_terms & excerpt_terms)
    if not task_matches:
        return 0.0, [], []

    task_coverage = len(task_matches) / len(task_terms) if task_terms else 0.0
    project_coverage = len(project_matches) / len(project_terms) if project_terms else 0.0
    score = task_coverage * 0.85 + project_coverage * 0.15
    operation_groups, _ = capability_match_for_excerpt(task, excerpt)
    if operation_groups:
        score = min(1.0, score + 0.12)
    return round(score, 6), task_matches, project_matches


def evaluate_compatibility(
    skill: CatalogSkill,
    detail: SkillDetail | None,
    project: ProjectSnapshot,
) -> Compatibility:
    """Distinguish metadata facts from inferred or unknown compatibility."""
    text = skill.name + " " + skill.slug
    if detail and detail.files:
        skill_doc = next(
            (file.contents[:10_000] for file in detail.files if file.path.lower() == "skill.md"), ""
        )
        text += " " + skill_doc
    skill_terms = _terms(text)
    targets = skill_terms & FRAMEWORKS
    project_terms = _project_framework_terms(project)
    known_facts = list(project.facts)
    unknowns: list[str] = []

    if not project.facts:
        unknowns.append("No framework or language facts were detected in the project metadata.")
    if not targets:
        if project.facts:
            return Compatibility(
                status="partial",
                detected_facts=known_facts,
                inferred_facts=["The skill does not name a specific project framework."],
                unknowns=["Skill prerequisites beyond its description are unknown."],
            )
        return Compatibility(status="unknown", unknowns=unknowns)

    matches = targets & project_terms
    if matches:
        return Compatibility(
            status="compatible",
            detected_facts=known_facts,
            inferred_facts=[
                f"Skill targets {', '.join(sorted(matches))}, detected in project metadata."
            ],
            unknowns=["Runtime and version-specific requirements were not executed or verified."],
        )

    conflicting_group = next(
        (
            group
            for group in EXCLUSIVE_FRAMEWORK_GROUPS
            if targets & group and project_terms & group and not targets & project_terms & group
        ),
        None,
    )
    if conflicting_group:
        target_text = ", ".join(sorted(targets & conflicting_group))
        project_text = ", ".join(sorted(project_terms & conflicting_group))
        return Compatibility(
            status="incompatible",
            detected_facts=known_facts,
            inferred_facts=[
                f"Skill targets {target_text}; detected project stack is {project_text}."
            ],
            unknowns=[
                "No source files were inspected to confirm whether these stacks are intentionally mixed."
            ],
        )

    if project_terms:
        return Compatibility(
            status="partial",
            detected_facts=known_facts,
            inferred_facts=[
                f"Skill names {', '.join(sorted(targets))}; no direct matching dependency was detected."
            ],
            unknowns=[
                "Detected project facts do not establish whether the skill's prerequisites are met."
            ],
        )

    return Compatibility(
        status="unknown",
        inferred_facts=[f"Skill appears specific to {', '.join(sorted(targets))}."],
        unknowns=unknowns or ["Project framework compatibility could not be established."],
    )


def task_is_routine(task: str) -> bool:
    """Recognize routine edits that rarely gain value from a specialist skill."""
    normalized = re.sub(r"\s+", " ", task.strip().lower())
    routine_patterns = (
        r"\b(?:fix|correct) (?:a |the )?(?:typo|spelling|punctuation)\b",
        r"\b(?:rename|change) (?:a |the )?(?:variable|function|class|file)\b",
        r"\b(?:rename|change) (?:a |the )?local variable\b",
        r"\b(?:format|reformat|lint) (?:this |the |my )?(?:file|code|project)?\b",
        r"\bremove (?:an? |the )?unused import\b",
        r"\b(?:add|write) (?:a |one )?(?:simple )?(?:unit )?test\b",
        r"\bupdate (?:the )?(?:readme|comment|docstring)\b",
    )
    return any(re.search(pattern, normalized) for pattern in routine_patterns) and not re.search(
        r"\b(?:security|migration|kubernetes|terraform|oauth|accessibility|performance)\b",
        normalized,
    )


def relevance_threshold() -> float:
    """Initial, unvalidated minimum score for additional task fit."""
    return 0.34


def _weighted_score(task: str, searchable: str, *, source: str) -> tuple[float, list[str]]:
    """Score exact normalized overlap fully and alias-only overlap conservatively."""
    task_terms = _surface_terms(redact_secrets(task)) - BROAD_TASK_TERMS
    if not task_terms:
        return 0.0, []
    direct_content = _surface_terms(searchable)
    expanded_content = _terms(searchable)
    direct_matches = task_terms & direct_content
    related_matches = (task_terms & expanded_content) - direct_matches
    if not direct_matches and not related_matches:
        return 0.0, []

    coverage = (len(direct_matches) + 0.35 * len(related_matches)) / len(task_terms)
    score = min(0.78, coverage * 0.78)
    rationale: list[str] = []
    if direct_matches:
        rationale.append(
            f"Direct {source} matches for task concepts: {_bounded_terms(direct_matches)}."
        )
    if related_matches:
        rationale.append(
            f"Weaker related wording in {source}: {_bounded_terms(related_matches)}."
        )
    return score, rationale


def _documented_task_capability(
    task: str, document: str
) -> tuple[bool | None, set[str], set[str]]:
    """Require an operation and task concept to occur in the same documentation unit."""
    safe_task = redact_secrets(task)
    task_topics = _surface_terms(safe_task) - BROAD_TASK_TERMS
    if not task_topics:
        return None, set(), set()

    requested_groups = _operation_groups(safe_task)
    if not requested_groups and _has_negated_operation(safe_task):
        return False, set(), set()
    matched_groups: set[str] = set()
    matched_topics: set[str] = set()
    for heading, unit in _capability_units(document):
        groups, topics = _capability_match(task, unit, topic_context=heading)
        matched_groups.update(groups)
        matched_topics.update(topics)

    if requested_groups:
        supported = requested_groups.issubset(matched_groups)
    else:
        supported = bool(matched_groups)
    return supported, matched_groups, matched_topics


def capability_match_for_excerpt(task: str, excerpt: str) -> tuple[list[str], list[str]]:
    """Return operation groups and task terms evidenced together in one text unit."""
    return _capability_match(task, excerpt)


def _capability_match(
    task: str, excerpt: str, *, topic_context: str = ""
) -> tuple[list[str], list[str]]:
    safe_task = redact_secrets(task)
    task_topics = _surface_terms(safe_task) - BROAD_TASK_TERMS
    topic_matches = task_topics & _terms(f"{topic_context} {excerpt}")
    if not topic_matches:
        return [], []
    excerpt_groups = _operation_groups(excerpt)
    requested_groups = _operation_groups(safe_task)
    if not requested_groups and _has_negated_operation(safe_task):
        return [], sorted(topic_matches)
    matched_groups = excerpt_groups & requested_groups if requested_groups else excerpt_groups
    return sorted(matched_groups), sorted(topic_matches)


def _operation_groups(text: str) -> set[str]:
    positive, _ = _operation_group_evidence(text)
    return positive


def _has_negated_operation(text: str) -> bool:
    _, negated = _operation_group_evidence(text)
    return bool(negated)


def _operation_group_evidence(text: str) -> tuple[set[str], set[str]]:
    positive: set[str] = set()
    negated: set[str] = set()
    for sentence in SENTENCE_SPLIT_RE.split(text):
        for clause in CLAUSE_SPLIT_RE.split(sentence):
            for group, pattern in CAPABILITY_ACTIONS.items():
                for action in pattern.finditer(clause):
                    context = clause[max(0, action.start() - 80) : action.start()]
                    if NEGATION_RE.search(context):
                        negated.add(group)
                    else:
                        positive.add(group)
    return positive, negated


def _capability_units(document: str):
    """Yield prose lines with heading context, while excluding fenced code."""
    heading = ""
    fence_marker: str | None = None
    fence_length = 0
    for raw_line in document.splitlines():
        line = raw_line.strip()
        fence = FENCE_RE.match(line)
        if fence:
            marker_text = fence.group(1)
            marker = marker_text[0]
            if fence_marker is None:
                fence_marker, fence_length = marker, len(marker_text)
            elif marker == fence_marker and len(marker_text) >= fence_length:
                fence_marker, fence_length = None, 0
            continue
        if fence_marker is not None:
            continue
        heading_match = HEADING_RE.match(line)
        if heading_match:
            heading = heading_match.group(1)[:200]
            continue
        line = LIST_PREFIX_RE.sub("", line)
        for fragment in SENTENCE_SPLIT_RE.split(line):
            fragment = fragment.strip()
            if fragment:
                yield heading, fragment


def _surface_terms(value: str) -> set[str]:
    raw = {token.lower().strip("._-") for token in TOKEN_RE.findall(value.lower())}
    terms = {token for token in raw if len(token) > 2 and token not in STOPWORDS}
    terms.update(
        part
        for token in raw
        for part in re.split(r"[._-]", token)
        if len(part) > 2 and part not in STOPWORDS
    )
    return terms


def _bounded_terms(terms) -> str:
    return ", ".join(sorted(terms)[:8])


def _terms(value: str) -> set[str]:
    terms = _surface_terms(value)
    expanded = set(terms)
    for term in terms:
        expanded.update(ALIASES.get(term, set()))
    return expanded


def _project_framework_terms(project: ProjectSnapshot) -> set[str]:
    text = " ".join(project.facts).lower()
    terms = _terms(text)
    if "python requirement" in text:
        terms.add("python")
    if "node.js" in text or "node project" in text:
        terms.update({"nodejs", "typescript"})
    if terms & {"django", "fastapi", "flask", "starlette", "sqlalchemy", "pytest"}:
        terms.add("python")
    if terms & {
        "react",
        "next",
        "nextjs",
        "vue",
        "nuxt",
        "angular",
        "svelte",
        "astro",
        "express",
        "nestjs",
    }:
        terms.add("nodejs")
    return terms & FRAMEWORKS
