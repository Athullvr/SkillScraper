"""Conservative, bounded heuristic checks for retrieved skill file contents."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from skillscout.models import LocalFinding, Severity, SkillDetail

MAX_SCANNED_FILES = 100
MAX_FILE_CHARS = 100_000
MAX_TOTAL_CHARS = 1_000_000
EXECUTABLE_SUFFIXES = {".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".ps1", ".rb", ".pl"}

PATTERNS: tuple[tuple[str, Severity, re.Pattern[str], str], ...] = (
    (
        "credential-exfiltration",
        Severity.CRITICAL,
        re.compile(
            r"(?is)(?:\.env|(?:api|access|secret|auth)[_-]?(?:key|token|secret)|credential)"
            r".{0,240}(?:curl|wget|https?\.post|fetch\s*\(|requests\.post|webhook|upload|exfil)"
            r"|(?:curl|wget|https?\.post|fetch\s*\(|requests\.post|webhook|upload|exfil)"
            r".{0,240}(?:\.env|(?:api|access|secret|auth)[_-]?(?:key|token|secret)|credential)"
        ),
        "Credential access appears near an outbound transfer instruction or call.",
    ),
    (
        "destructive-system-action",
        Severity.CRITICAL,
        re.compile(r"(?i)(?:rm\s+-rf\s+/(?:\s|$)|sudo\s+rm\s+-rf|mkfs(?:\.[a-z0-9]+)?\s+/dev/|dd\s+[^\n]*of=/dev/)") ,
        "A destructive system or device command was found.",
    ),
    (
        "credential-access",
        Severity.HIGH,
        re.compile(r"(?i)(?:read|print|dump|echo|cat|send|upload|collect).{0,100}(?:\.env|api[_ -]?key|secret[_ -]?key|access[_ -]?token|credentials?)"),
        "Instructions appear to read, expose, or collect credentials.",
    ),
    (
        "instruction-override",
        Severity.HIGH,
        re.compile(r"(?i)(?:ignore|override|disregard).{0,80}(?:previous|system|developer|safety).{0,50}instructions?"),
        "Text attempts to override the agent's governing instructions.",
    ),
    (
        "obfuscated-execution",
        Severity.HIGH,
        re.compile(r"(?i)(?:base64\s+(-d|--decode)|(?:eval|exec)\s*\(\s*(?:atob|decode|base64)|powershell.{0,80}-encodedcommand)"),
        "Obfuscated or encoded execution was found.",
    ),
    (
        "shell-pipeline-install",
        Severity.MEDIUM,
        re.compile(r"(?i)(?:curl|wget)\s+[^\n|]{1,250}\|\s*(?:sh|bash)\b"),
        "A downloaded shell script is piped directly to a shell.",
    ),
    (
        "package-lifecycle-hook",
        Severity.MEDIUM,
        re.compile(r"(?is)\"(?:preinstall|install|postinstall|prepare)\"\s*:"),
        "A package lifecycle hook can run commands during a package install.",
    ),
)


def scan_skill(detail: SkillDetail) -> tuple[list[LocalFinding], list[str], bool]:
    """Scan at most bounded text files; return findings, inspected paths, completeness."""
    files = detail.files
    if files is None or len(files) > MAX_SCANNED_FILES:
        return [], [], False

    findings: list[LocalFinding] = []
    inspected: list[str] = []
    total_chars = 0
    complete = True
    seen_checks: set[tuple[str, str]] = set()

    for skill_file in files:
        path = PurePosixPath(skill_file.path)
        if len(skill_file.contents) > MAX_FILE_CHARS:
            complete = False
            continue
        total_chars += len(skill_file.contents)
        if total_chars > MAX_TOTAL_CHARS:
            complete = False
            break
        inspected.append(skill_file.path)
        text = skill_file.contents
        for check_id, severity, pattern, summary in PATTERNS:
            if pattern.search(text) and (check_id, skill_file.path) not in seen_checks:
                findings.append(
                    LocalFinding(
                        check_id=check_id,
                        severity=severity,
                        summary=summary,
                        file_path=skill_file.path,
                    )
                )
                seen_checks.add((check_id, skill_file.path))
        if path.suffix.lower() in EXECUTABLE_SUFFIXES:
            skill_doc = next(
                (candidate.contents.lower() for candidate in files if candidate.path.lower() == "skill.md"),
                "",
            )
            if path.name.lower() not in skill_doc and ("unexpected-executable", skill_file.path) not in seen_checks:
                findings.append(
                    LocalFinding(
                        check_id="unexpected-executable",
                        severity=Severity.MEDIUM,
                        summary="Executable-looking content is present but not referenced by SKILL.md.",
                        file_path=skill_file.path,
                    )
                )
                seen_checks.add(("unexpected-executable", skill_file.path))

    if not any(file.path.lower() == "skill.md" for file in files):
        complete = False
    elif not any(
        file.path.lower() == "skill.md" and file.contents.strip()
        for file in files
    ):
        complete = False
    return findings, inspected, complete

