"""Compact, versioned response models for SkillScout's MCP tools."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from skillscout.models import Compatibility, Decision, Severity


class MCPToolError(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)


class MCPProjectContext(BaseModel):
    """Project facts with no local filesystem path."""

    detected_files: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    inferred: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class MCPSkillIdentity(BaseModel):
    id: str = Field(min_length=3, max_length=500)
    name: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=300)
    slug: str = Field(min_length=1, max_length=200)


class MCPAuditEvidence(BaseModel):
    provider: str = Field(min_length=1, max_length=200)
    status: str = Field(min_length=1, max_length=100)
    risk_level: str | None = Field(default=None, max_length=100)
    audited_at: datetime | None = None
    revision: str | None = Field(default=None, max_length=256)


class MCPLocalFinding(BaseModel):
    check_id: str
    severity: Severity
    summary: str = Field(max_length=500)
    file_path: str | None = Field(default=None, max_length=500)


class MCPInspectionEvidence(BaseModel):
    content_hash: str | None = Field(default=None, max_length=256)
    evidence_status: Literal["complete", "incomplete", "unknown"]
    external_audit_status: Literal["current", "stale", "unbound", "unavailable", "none"]
    external_audits: list[MCPAuditEvidence] = Field(default_factory=list)
    external_audit_count: int = Field(ge=0)
    external_audits_truncated: bool = False
    local_findings: list[MCPLocalFinding] = Field(default_factory=list)
    local_finding_count: int = Field(ge=0)
    local_findings_truncated: bool = False
    files_inspected: list[str] = Field(default_factory=list)
    files_inspected_count: int = Field(ge=0)
    files_inspected_truncated: bool = False
    evidence_notes: list[str] = Field(default_factory=list)


class MCPInstallAdvice(BaseModel):
    label: Literal["informational_only"] = "informational_only"
    command: str = Field(max_length=2_000)
    truncated: bool = False


class MCPRecommendation(BaseModel):
    skill: MCPSkillIdentity
    rank: int = Field(ge=1)
    relevance_score: float = Field(ge=0, le=1)
    task_fit_rationale: list[str] = Field(default_factory=list)
    compatibility: Compatibility
    evidence: MCPInspectionEvidence
    decision: Decision
    decision_reasons: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    install_advice: MCPInstallAdvice


class PrepareTaskResult(BaseModel):
    """Stable MCP result shape for ``prepare_task``."""

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["success", "error"]
    decision: Decision | None = None
    task: str | None = Field(default=None, max_length=4_000)
    explanation: str = Field(max_length=1_000)
    project_context: MCPProjectContext | None = None
    recommendations: list[MCPRecommendation] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    next_action: str = Field(max_length=500)
    error: MCPToolError | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> PrepareTaskResult:
        if self.status == "success" and (self.decision is None or self.error is not None):
            raise ValueError("successful prepare_task results require a decision and no error")
        if self.status == "error" and (self.decision is not None or self.error is None):
            raise ValueError("failed prepare_task results require an error and no decision")
        return self


class InspectSkillResult(BaseModel):
    """Stable MCP result shape for ``inspect_skill``."""

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["success", "error"]
    decision: Decision | None = None
    skill: MCPSkillIdentity | None = None
    compatibility: Compatibility | None = None
    evidence: MCPInspectionEvidence | None = None
    explanation: str = Field(max_length=1_000)
    uncertainties: list[str] = Field(default_factory=list)
    next_action: str = Field(max_length=500)
    error: MCPToolError | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> InspectSkillResult:
        complete = (
            self.decision is not None
            and self.skill is not None
            and self.compatibility is not None
            and self.evidence is not None
        )
        if self.status == "success" and (not complete or self.error is not None):
            raise ValueError(
                "successful inspect_skill results require inspection data and no error"
            )
        if self.status == "error" and (complete or self.error is None):
            raise ValueError("failed inspect_skill results require an error and no inspection data")
        return self


class MCPDoctorResult(BaseModel):
    """Safe subset of the local doctor report; network access is not tested."""

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["success", "error"]
    python_version: str | None = None
    python_supported: bool | None = None
    catalog_auth_configured: bool | None = None
    candidate_limit: int | None = Field(default=None, ge=1, le=5)
    optional_llm_configured: bool | None = None
    catalog_access: Literal["not_checked"] = "not_checked"
    error: MCPToolError | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> MCPDoctorResult:
        if self.status == "success" and (self.python_version is None or self.error is not None):
            raise ValueError("successful doctor results require local diagnostic fields")
        if self.status == "error" and self.error is None:
            raise ValueError("failed doctor results require an error")
        return self
