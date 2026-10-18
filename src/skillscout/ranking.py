"""Interpretable task relevance and project compatibility signals."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from skillscout.models import CatalogSkill, Compatibility, ProjectSnapshot, SkillDetail

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "i", "in",
    "into", "is", "it", "make", "my", "of", "on", "or", "our", "please", "that", "the",
    "this", "to", "use", "using", "we", "with", "you", "your", "implement", "build", "add",
    "create", "write", "update", "fix", "help", "need", "want", "task", "project",
}
GENERIC_SKILL_TERMS = {"assistant", "coding", "code", "developer", "general", "programming", "software"}
FRAMEWORKS = {
    "react", "nextjs", "next", "vue", "nuxt", "angular", "svelte", "astro", "django", "fastapi",
    "flask", "starlette", "sqlalchemy", "pydantic", "express", "nestjs", "nodejs", "python", "typescript",
}
ALIASES = {
    "oauth": {"auth", "authentication", "authorization"},
    "auth": {"oauth", "authentication", "authorization"},
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


@dataclass(frozen=True)
class RankedSkill:
    skill: CatalogSkill
    score: float
    rationale: list[str]


def rank_catalog_results(task: str, skills: list[CatalogSkill]) -> list[RankedSkill]:
    """Rank search results using only task-match and a small generic-skill penalty."""
    task_terms = _terms(task)
    ranked: list[RankedSkill] = []
    for index, skill in enumerate(skills):
        searchable = " ".join((skill.name, skill.slug.replace("-", " "), skill.source, skill.description or ""))
        score, rationale = _score(task_terms, searchable)
        generic_terms = _terms(f"{skill.name} {skill.slug}") & GENERIC_SKILL_TERMS
        if generic_terms:
            score = max(0.0, score - 0.12)
            rationale.append("Generic skill wording adds little beyond an agent's ordinary coding capability.")
        # Catalog order is a weak tie-break signal and never creates task relevance by itself.
        if score > 0:
            score = min(1.0, score + max(0.0, 0.03 - index * 0.003))
        if not rationale:
            rationale.append("No meaningful task terms were found in the skill listing metadata.")
        ranked.append(RankedSkill(skill=skill, score=round(score, 3), rationale=rationale))
    return sorted(ranked, key=lambda item: item.score, reverse=True)


def refine_rank(task: str, ranked: RankedSkill, detail: SkillDetail) -> RankedSkill:
    """Refine task match from bounded catalog skill text without affecting security policy."""
    document = "\n".join(
        file.contents[:5_000]
        for file in (detail.files or [])
        if file.path.lower() == "skill.md"
    )
    if not document:
        return ranked
    score, rationale = _score(_terms(task), f"{ranked.skill.name} {ranked.skill.slug} {document}")
    score = max(ranked.score, score)
    rationale = [*ranked.rationale]
    additional = [item for item in _score(_terms(task), document)[1] if item not in rationale]
    rationale.extend(additional[:2])
    return RankedSkill(skill=ranked.skill, score=round(score, 3), rationale=rationale)


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
            inferred_facts=[f"Skill targets {', '.join(sorted(matches))}, detected in project metadata."],
            unknowns=["Runtime and version-specific requirements were not executed or verified."],
        )

    if project_terms:
        target_text = ", ".join(sorted(targets))
        project_text = ", ".join(sorted(project_terms))
        return Compatibility(
            status="incompatible",
            detected_facts=known_facts,
            inferred_facts=[f"Skill targets {target_text}; detected project stack is {project_text}."],
            unknowns=["No source files were inspected to confirm whether these stacks are intentionally mixed."],
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
        r"\b(?:format|reformat|lint) (?:this |the |my )?(?:file|code|project)?\b",
        r"\bremove (?:an? |the )?unused import\b",
        r"\b(?:add|write) (?:a |one )?(?:simple )?(?:unit )?test\b",
        r"\bupdate (?:the )?(?:readme|comment|docstring)\b",
    )
    if any(re.search(pattern, normalized) for pattern in routine_patterns):
        if not re.search(r"\b(?:security|migration|kubernetes|terraform|oauth|accessibility|performance)\b", normalized):
            return True
    return False


def relevance_threshold() -> float:
    """Initial, unvalidated minimum score for additional task fit."""
    return 0.34


def _score(task_terms: set[str], searchable: str) -> tuple[float, list[str]]:
    if not task_terms:
        return 0.0, []
    content = _terms(searchable)
    matches = task_terms & content
    if not matches:
        return 0.0, []
    coverage = len(matches) / len(task_terms)
    phrase_bonus = 0.15 if len(matches) >= 2 and _contains_task_phrase(task_terms, searchable) else 0.0
    score = min(1.0, coverage * 0.78 + phrase_bonus)
    rationale = [f"Matching task concepts: {', '.join(sorted(matches))}."]
    if phrase_bonus:
        rationale.append("The task concepts appear together in the candidate description or skill text.")
    return score, rationale


def _contains_task_phrase(task_terms: set[str], searchable: str) -> bool:
    # Phrase matching is a small signal only; content may contain prompt text as well as metadata.
    content_terms = _terms(searchable)
    return len(task_terms & content_terms) >= 2 and len(task_terms) <= 5


def _terms(value: str) -> set[str]:
    raw = {token.lower().strip("._-") for token in TOKEN_RE.findall(value.lower())}
    terms = {token for token in raw if len(token) > 2 and token not in STOPWORDS}
    expanded = set(terms)
    for term in terms:
        expanded.update(ALIASES.get(term, set()))
    # Split common dotted/hyphenated forms into stable concepts too.
    expanded.update(
        part
        for token in raw
        for part in re.split(r"[._-]", token)
        if len(part) > 2 and part not in STOPWORDS
    )
    return expanded


def _project_framework_terms(project: ProjectSnapshot) -> set[str]:
    text = " ".join(project.facts).lower()
    terms = _terms(text)
    if "python requirement" in text:
        terms.add("python")
    if "node.js" in text or "node project" in text:
        terms.update({"nodejs", "typescript"})
    return terms & FRAMEWORKS

