"""Validated domain objects and versioned report schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl, field_validator


def utc_now() -> datetime:
    """Return the current UTC time as an aware datetime."""
    return datetime.now(timezone.utc)


class Decision(StrEnum):
    NO_SKILL_NEEDED = "NO_SKILL_NEEDED"
    USE = "USE"
    REVIEW = "REVIEW"
    AVOID = "AVOID"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CatalogSkill(BaseModel):
    """A search/listing result with stable catalog provenance."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=3, max_length=500)
    name: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=300)
    slug: str = Field(min_length=1, max_length=200)
    installs: int = Field(default=0, ge=0)
    source_type: str | None = Field(default=None, alias="sourceType")
    source_url: HttpUrl | None = Field(default=None, alias="url")
    install_url: HttpUrl | None = Field(default=None, alias="installUrl")
    content_hash: str | None = Field(default=None, alias="hash", max_length=256)
    description: str | None = Field(default=None, max_length=10_000)
    retrieved_at: datetime = Field(default_factory=utc_now)

    @field_validator("source_url", "install_url")
    @classmethod
    def require_credential_free_https_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        if value is not None and (
            value.scheme != "https"
            or value.username
            or value.password
            or value.query
            or value.fragment
        ):
            raise ValueError("catalog URLs must use HTTPS without credentials, query, or fragment")
        return value


class SkillFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    contents: str = Field(max_length=100_000)

    @field_validator("path")
    @classmethod
    def require_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if value == "." or path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError("file path must stay within the skill directory")
        return value


class SkillDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=3, max_length=500)
    source: str = Field(min_length=1, max_length=300)
    slug: str = Field(min_length=1, max_length=200)
    content_hash: str | None = Field(default=None, alias="hash", max_length=256)
    files: list[SkillFile] | None = Field(default=None, max_length=100)


class AuditFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = Field(min_length=1, max_length=200)
    status: str = Field(min_length=1, max_length=100)
    summary: str = Field(default="", max_length=2_000)
    audited_at: datetime | None = Field(default=None, alias="auditedAt")
    risk_level: str | None = Field(default=None, alias="riskLevel", max_length=100)
    categories: list[str] = Field(default_factory=list, max_length=50)
    revision: str | None = Field(
        default=None,
        validation_alias=AliasChoices("revision", "hash", "contentHash"),
        max_length=256,
    )


class LocalFinding(BaseModel):
    check_id: str
    severity: Severity
    summary: str = Field(max_length=500)
    file_path: str | None = None


class InspectionEvidence(BaseModel):
    content_hash: str | None = None
    files_inspected: list[str] = Field(default_factory=list)
    inspection_time: datetime = Field(default_factory=utc_now)
    evidence_status: Literal["complete", "incomplete", "unknown"] = "unknown"
    external_audit_status: Literal["current", "stale", "unbound", "unavailable", "none"] = "none"
    external_audits: list[AuditFinding] = Field(default_factory=list)
    local_findings: list[LocalFinding] = Field(default_factory=list)
    evidence_notes: list[str] = Field(default_factory=list)


class Compatibility(BaseModel):
    status: Literal["compatible", "partial", "unknown", "incompatible"]
    detected_facts: list[str] = Field(default_factory=list)
    inferred_facts: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class CandidateReport(BaseModel):
    skill: CatalogSkill
    rank: int = Field(ge=1)
    relevance_score: float = Field(ge=0, le=1)
    task_fit_rationale: list[str]
    compatibility: Compatibility
    evidence: InspectionEvidence
    decision: Decision
    decision_reasons: list[str]
    limitations: list[str]
    proposed_install_command: str


class ProjectSnapshot(BaseModel):
    path: str
    detected_files: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    inferred: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class Report(BaseModel):
    """Public JSON schema. Increment schema_version for incompatible changes."""

    schema_version: Literal["1.0"] = "1.0"
    generated_at: datetime = Field(default_factory=utc_now)
    task: str
    agent: str
    project: ProjectSnapshot
    decision: Decision
    summary: str
    candidates: list[CandidateReport] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class LLMFitAssessment(BaseModel):
    """Strict response shape for optional task-fit assistance."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    score: float = Field(ge=0, le=1)
    rationale: list[str] = Field(min_length=1, max_length=5)


class DoctorReport(BaseModel):
    python_version: str
    python_supported: bool
    catalog_url: HttpUrl
    catalog_auth_configured: bool
    candidate_limit: int
    llm_api_key_configured: bool
    llm_task_text_opt_in: bool
    llm_enabled: bool


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorReport(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    error: ErrorDetail


class SkillInspectionReport(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: datetime = Field(default_factory=utc_now)
    skill: CatalogSkill
    evidence: InspectionEvidence
    compatibility: Compatibility
    decision: Decision
    decision_reasons: list[str]
    limitations: list[str]
    proposed_install_command: str

