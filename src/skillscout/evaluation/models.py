"""Typed wire records for evaluation runs, evidence, and blinded judgments."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import StrEnum
from typing import Any

from skillscout.evaluation.protocol import ArmName


class ArmStatus(StrEnum):
    NOT_RUN = "not_run"
    BLOCKED = "blocked"
    FAILED = "failed"
    PARTIAL = "partial"
    COMPLETE = "complete"


class PairStatus(StrEnum):
    NOT_RUN = "not_run"
    BLOCKED = "blocked"
    FAILED = "failed"
    PARTIAL = "partial"
    COMPLETE = "complete"


class SlotStatus(StrEnum):
    OCCUPIED = "occupied"
    EMPTY = "empty"
    UNKNOWN = "unknown"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_RUN = "not_run"


class Decision(StrEnum):
    NO_SKILL_NEEDED = "NO_SKILL_NEEDED"
    USE = "USE"
    REVIEW = "REVIEW"
    AVOID = "AVOID"


class RelevanceLabel(StrEnum):
    RELEVANT = "relevant"
    IRRELEVANT = "irrelevant"
    UNKNOWN = "unknown"


class ReviewResolution(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class DecisionConformity(StrEnum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    INDETERMINATE = "INDETERMINATE"


class TerminationStatus(StrEnum):
    CONFIRMED = "confirmed"
    NOT_CONFIRMED = "not_confirmed"
    UNKNOWN = "unknown"


class LinkageStatus(StrEnum):
    MATCHED = "matched"
    UNVERIFIED = "unverified"
    MISMATCH = "mismatch"
    NOT_EXPOSED = "not_exposed"
    NOT_APPLICABLE = "not_applicable"


class LookupStatus(StrEnum):
    AVAILABLE = "available"
    FAILED = "failed"
    NOT_STARTED_AFTER_DEADLINE = "not_started_after_deadline"
    NOT_STARTED_AFTER_REVIEW_STOP = "not_started_after_review_stop"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    stable_id: str
    source_slug: str | None = None
    rank: int = 1
    arm_content_hash: str | None = None
    arm_revision: str | None = None
    returned_metadata: dict[str, str] = field(default_factory=dict)
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class PrimaryResponse:
    """A parsed response returned by one arm; no raw body is stored."""

    candidates: tuple[CandidateObservation, ...] = ()
    decision: Decision | None = None
    partial: bool = False
    metadata: dict[str, str] = field(default_factory=dict)
    internal_attempt_count: int | None = None


@dataclass(frozen=True, slots=True)
class InvocationReturn:
    response: PrimaryResponse
    termination_status: TerminationStatus
    termination_evidence: str


class InvocationFailure(RuntimeError):
    """A runner-reported terminal error, timeout, or harness failure."""

    def __init__(
        self,
        category: str,
        *,
        termination_status: TerminationStatus = TerminationStatus.UNKNOWN,
        termination_evidence: str = "",
        harness_outcome: str | None = None,
    ) -> None:
        super().__init__(category)
        self.category = category
        self.termination_status = termination_status
        self.termination_evidence = termination_evidence
        self.harness_outcome = harness_outcome or category


@dataclass(slots=True)
class LookupRecord:
    kind: str
    status: LookupStatus = LookupStatus.NOT_STARTED_AFTER_DEADLINE
    attempt_count: int = 0
    start_monotonic_s: float | None = None
    end_monotonic_s: float | None = None
    start_utc: str | None = None
    end_utc: str | None = None
    http_status: int | None = None
    error_category: str | None = None
    timeout: bool = False
    termination_status: TerminationStatus = TerminationStatus.UNKNOWN
    termination_evidence: str | None = None
    late: bool = False
    returned_candidate_id: str | None = None
    content_hash: str | None = None
    revision: str | None = None
    identity_linkage: LinkageStatus = LinkageStatus.NOT_APPLICABLE
    revision_linkage: LinkageStatus = LinkageStatus.NOT_APPLICABLE
    audit_status: str | None = None
    audit_matches_revision: bool | None = None
    detail_sufficient_for_relevance: bool | None = None
    response_metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class CandidateSlot:
    position: int
    status: SlotStatus
    stable_id: str | None = None
    source_slug: str | None = None
    rank: int | None = None
    arm_content_hash: str | None = None
    arm_revision: str | None = None
    detail: LookupRecord | None = None
    audit: LookupRecord | None = None
    relevance_label: RelevanceLabel | None = None
    review_resolution: ReviewResolution | None = None
    opaque_candidate_key: str | None = None


@dataclass(slots=True)
class ArmRecord:
    arm: ArmName
    status: ArmStatus
    invocation_timeout_s: int
    invocation_start_monotonic_s: float | None = None
    invocation_end_monotonic_s: float | None = None
    invocation_start_utc: str | None = None
    invocation_end_utc: str | None = None
    response_latency_s: float | None = None
    harness_outcome: str | None = None
    underlying_termination: TerminationStatus = TerminationStatus.UNKNOWN
    termination_evidence: str | None = None
    error_category: str | None = None
    decision: Decision | None = None
    primary_response_valid: bool = False
    late_primary_response: bool = False
    internal_attempt_count: int | None = None
    returned_metadata: dict[str, str] = field(default_factory=dict)
    slots: list[CandidateSlot] = field(default_factory=list)
    evidence_ready_monotonic_s: float | None = None
    evidence_ready_utc: str | None = None
    tud_s: float | None = None
    tud_status: str = "incomplete"
    evidence_retrieval_duration_s: float = 0.0
    pair_wait_s: float = 0.0


@dataclass(slots=True)
class PairRecord:
    schema_version: str
    run_id: str
    dataset_sha256: str
    pair_number: int
    pair_id: str
    task_id: str
    task_index: int
    repetition: int
    arm_order: tuple[ArmName, ArmName]
    context_manifest_sha256: str
    status: PairStatus
    status_reason: str | None
    first_invocation_start_monotonic_s: float | None
    second_invocation_start_monotonic_s: float | None
    first_invocation_start_utc: str | None
    second_invocation_start_utc: str | None
    pairing_interval_s: float | None
    pairing_within_window: bool | None
    arms: dict[ArmName, ArmRecord]
    lookup_budget_start_monotonic_s: float | None = None
    lookup_deadline_monotonic_s: float | None = None
    lookup_budget_start_utc: str | None = None
    lookup_deadline_utc: str | None = None
    evidence_retrieval_duration_s: float = 0.0
    pair_evidence_ready_monotonic_s: float | None = None
    pair_tud_s: float | None = None
    pair_tud_status: str = "not_assessable"
    run_stop_required: bool = False
    run_stop_reason: str | None = None
    deviation_codes: list[str] = field(default_factory=list)
    catalog_metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def _jsonable(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(key.value if isinstance(key, StrEnum) else key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value):
        return _jsonable(asdict(value))
    return value
