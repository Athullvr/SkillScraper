"""Offline construction and integrity-checked storage for blinded evidence packets.

Internal candidate identity and arm linkage are used only while transforming
runtime records. Reviewer artifacts contain opaque references and an explicit
evidence state; the source crosswalk is written to a separate private directory.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from skillscout.evaluation.fixtures import (
    FixtureError,
    has_credential_like_text,
    has_protected_audit_disclosure,
    redact_credential_like_text,
    verify_fixture_pack,
)
from skillscout.evaluation.metrics import compute_metrics
from skillscout.evaluation.models import (
    PairRecord,
    LinkageStatus,
    LookupStatus,
    SlotStatus,
)
from skillscout.evaluation.protocol import (
    DATASET_SHA256,
    ELIGIBLE_TASK_COUNT,
    PLANNED_PAIR_COUNT,
    ArmName,
    EvaluationTask,
    build_schedule,
    find_repository_root,
    load_dataset,
)
from skillscout.evaluation.reviews import (
    EvidenceExcerpt,
    ReviewLedger,
    ReviewKind,
    ReviewerPacket,
    apply_candidate_review_labels,
    build_candidate_relevance_packet,
    build_decision_conformity_packet,
    decision_outcomes_by_pair,
    prepare_review_order,
)
from skillscout.evaluation.validation import validate_records


EVIDENCE_ARTIFACT_SCHEMA = "skillscout-blinded-evidence-artifact-v2"
REVIEW_BUNDLE_SCHEMA = "skillscout-review-packet-bundle-v2"
RESTRICTED_CROSSWALK_SCHEMA = "skillscout-review-crosswalk-v2"
EVIDENCE_SOURCE_SCHEMA = "skillscout-restricted-evidence-source-v1"
MAX_EXCERPTS = 8
MAX_EXCERPT_CHARS = 2000
MAX_TOTAL_EXCERPT_CHARS = 8000
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_CANDIDATE_OCCURRENCES = PLANNED_PAIR_COUNT * 2 * 3
MAX_DECISION_PACKETS = PLANNED_PAIR_COUNT
MAX_PROTECTED_EVIDENCE_ROWS = MAX_CANDIDATE_OCCURRENCES * MAX_EXCERPTS


class EvidencePacketError(ValueError):
    """An evidence packet or its restricted crosswalk is unsafe or malformed."""


class EvidenceStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"
    STALE = "stale"
    MISMATCHED = "mismatched"
    LATE = "late"
    UNVERIFIED = "unverified"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RestrictedEvidenceExcerpt:
    """Sensitive audit evidence retained only in the restricted evaluator artifact."""

    content: str
    evidence_type: str


_PROVENANCE = {
    "decision_time_arm_evidence",
    "retrospective_evaluator_evidence",
    "task_context",
}
_EVIDENCE_TYPES = {"candidate_detail", "candidate_readme", "compatibility", "integration", "task_context", "other"}
_PROTECTED_EVIDENCE_TYPES = {"audit", "audit_finding", "safety_finding", "scanner_finding"}
_PACKET_FIELDS = {
    "packet_key", "kind", "task_text", "criteria", "approved_context", "evidence",
    "decision_options", "schema_version", "evidence_status", "assessment_allowed",
    "context_status", "context_reasons", "evidence_reasons",
}
_EXCERPT_FIELDS = {
    "content", "evidence_type", "provenance", "relevance_sufficient", "identity_linked",
    "revision_linked", "retrieval_status", "candidate_ref", "observed_revision_ref", "truncated",
}
_BUNDLE_FIELDS = {"schema_version", "dataset_sha256", "catalog_mode", "candidate_artifacts", "decision_packets", "integrity_sha256"}
_ARTIFACT_FIELDS = {
    "schema_version", "opaque_candidate_id", "observed_revision_ref", "provenance",
    "retrieval_status", "status_flags", "observed_at_utc", "retrieval_started_at_utc",
    "retrieval_completed_at_utc", "retrieval_deadline_at_utc", "packet",
}
_CROSSWALK_FIELDS = {
    "schema_version", "dataset_sha256", "candidate_entries", "decision_entries",
    "decision_gates", "protected_evidence", "integrity_sha256",
}
_EVIDENCE_SOURCE_FIELDS = {"schema_version", "dataset_sha256", "identifier_seed", "entries", "integrity_sha256"}
_EVIDENCE_SOURCE_ENTRY_FIELDS = {"pair_id", "arm", "rank", "attachment"}
_EVIDENCE_SOURCE_ATTACHMENT_FIELDS = {
    "excerpts", "protected_evidence", "provenance", "observed_at_utc", "retrieved_at_utc",
    "retrieved_at_monotonic_s", "late", "stale", "truncated", "protected_review_required",
}
_EVIDENCE_SOURCE_EXCERPT_FIELDS = {"content", "evidence_type"}
_STATUS_FLAGS = {
    item.value for item in EvidenceStatus
} | {
    "detail_not_recorded", "retrieval_failed", "detail_unavailable", "identity_or_revision_unverified",
    "source_marked_stale", "excerpt_truncated", "protected_text_withheld", "excerpt_missing",
    "detail_insufficient", "evidence_timing_unverified", "context_incomplete",
}
_CANDIDATE_CROSSWALK_ROW_FIELDS = {
    "opaque_candidate_id", "task_id", "pair_id", "repetition", "arm", "rank", "stable_id",
    "source_slug", "source_url", "arm_content_hash", "arm_revision", "observed_content_hash",
    "observed_revision", "observed_revision_ref", "evaluator_status_flags",
}
_DECISION_CROSSWALK_ROW_FIELDS = {"opaque_decision_packet_key", "pair_id", "task_id", "repetition", "arm"}
_DECISION_GATE_ROW_FIELDS = {"pair_id", "task_id", "status"}
_PROTECTED_EVIDENCE_ROW_FIELDS = {
    "opaque_candidate_id", "task_id", "pair_id", "repetition", "arm", "rank",
    "evidence_type", "provenance", "content", "observed_at_utc", "retrieved_at_utc",
}
_OPAQUE_RE = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
_ISO_UTC_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$")
_SENSITIVE_FIELD_RE = re.compile(
    r"(?i)\b(?:expected[_ -]?(?:decision|label|outcome)|human[_ -]?relevance[_ -]?judgment|"
    r"task[_ -]?(?:id|category)|arm|system[_ -]?identity|rank|score|popularity|install[_ -]?count|"
    r"audit(?:[_ -]?(?:status|finding|result|verdict))?|"
    r"safety(?:[_ -]?(?:status|finding|result|verdict))?|"
    r"scanner(?:[_ -]?(?:status|finding|result|verdict))?)\b\s*[:=]"
)
_PROTECTED_FINDING_TEXT_RE = re.compile(r"(?i)\b(?:protected\s+)?(?:safety|audit|scanner)\s+(?:finding|status|result|verdict)\b")
_PROTECTED_AUDIT_FIELD_RE = re.compile(
    r"(?i)\b(?:audit|safety|scanner)(?:[_ -]?(?:status|finding|result|verdict))?\s*[:=]"
)
_DISCLOSED_DECISION_RE = re.compile(
    r"(?i)\bexpected\s+(?:task(?:-level)?\s+)?(?:decision|label|outcome)\b.{0,80}\b"
    r"(?:NO_SKILL_NEEDED|USE|REVIEW|AVOID)\b"
)
_SYSTEM_IDENTITY_RE = re.compile(r"(?i)\b(?:SkillScout(?![-_][A-Za-z0-9])|ordinary[ _-]+search|skills\.sh)\b")
_RANKING_METADATA_RE = re.compile(
    r"(?i)\b(?:rank(?:ed|ing)?|result\s+position|score|popularity|install(?:ation)?\s+count|"
    r"download\s+count|search\s+order)\b(?:\s*(?:was|is|of|=|:|#)\s*[^\s,.;]+)?"
)


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _enum_text(value: Any) -> str | None:
    return None if value is None else str(getattr(value, "value", value))


def _arm_record(pair: Any, arm: ArmName) -> Any:
    arms = _get(pair, "arms", {})
    return arms.get(arm, arms.get(arm.value)) if isinstance(arms, Mapping) else None


def _utc_is_valid(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or not _ISO_UTC_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _safe_source_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme.casefold(), host, parsed.path, "", ""))
    except ValueError:
        return None


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _with_integrity(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "integrity_sha256": hashlib.sha256(_canonical(payload)).hexdigest()}


def _verify_integrity(value: dict[str, Any]) -> None:
    digest = value.get("integrity_sha256")
    payload = {key: item for key, item in value.items() if key != "integrity_sha256"}
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise EvidencePacketError("artifact integrity field is malformed")
    if hashlib.sha256(_canonical(payload)).hexdigest() != digest:
        raise EvidencePacketError("artifact integrity check failed")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidencePacketError("artifact contains duplicate JSON keys")
        result[key] = value
    return result


def _task_sensitive_values(task: EvaluationTask) -> tuple[str, ...]:
    values: set[str] = {task.task_id}
    if task.category.strip() and len(task.category.strip()) >= 8:
        values.add(task.category.strip())
    if task.expected_decision.strip():
        values.add(task.expected_decision.strip())
    judgment = task.human_relevance_judgment
    if isinstance(judgment, Mapping):
        def visit(value: Any, key: str = "") -> None:
            if key == "criteria":
                return
            if isinstance(value, Mapping):
                for nested_key, nested in value.items():
                    visit(nested, str(nested_key))
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    visit(nested, key)
            elif isinstance(value, str) and len(value.strip()) >= 8:
                values.add(value.strip())
        visit(judgment)
    return tuple(sorted(values, key=len, reverse=True))


def _has_sensitive_disclosure(value: str, task: EvaluationTask) -> bool:
    folded = value.casefold()
    if (
        has_credential_like_text(value)
        or has_protected_audit_disclosure(value)
        or _PROTECTED_FINDING_TEXT_RE.search(value)
        or _SYSTEM_IDENTITY_RE.search(value)
        or _RANKING_METADATA_RE.search(value)
    ):
        return True
    if task.expected_decision.strip() and re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(task.expected_decision.strip())}(?![A-Za-z0-9_])", value
    ):
        return True
    if any(
        secret.casefold() in folded
        for secret in _task_sensitive_values(task)
        if secret and secret != task.expected_decision.strip()
    ):
        return True
    return bool(_SENSITIVE_FIELD_RE.search(value) or _DISCLOSED_DECISION_RE.search(value))


def _detected_protected_evidence_type(value: str) -> str | None:
    if not (
        has_protected_audit_disclosure(value)
        or _PROTECTED_FINDING_TEXT_RE.search(value)
        or _PROTECTED_AUDIT_FIELD_RE.search(value)
    ):
        return None
    folded = value.casefold()
    if "scanner" in folded:
        return "scanner_finding"
    if "safety" in folded:
        return "safety_finding"
    return "audit_finding"


@dataclass(frozen=True, slots=True)
class EvidenceAttachment:
    """Allowlisted excerpt material supplied separately from raw runtime records."""

    excerpts: tuple[str, ...] = ()
    evidence_types: tuple[str, ...] = ()
    protected_evidence: tuple[RestrictedEvidenceExcerpt, ...] = ()
    provenance: str = "retrospective_evaluator_evidence"
    observed_at_utc: str | None = None
    retrieved_at_utc: str | None = None
    retrieved_at_monotonic_s: float | None = None
    late: bool = False
    stale: bool = False
    truncated: bool = False
    protected_review_required: bool = False

    @classmethod
    def from_value(cls, value: EvidenceAttachment | Mapping[str, Any] | None) -> EvidenceAttachment:
        if value is None:
            return cls()
        if isinstance(value, cls):
            if any(not isinstance(item, RestrictedEvidenceExcerpt) for item in value.protected_evidence):
                raise EvidencePacketError("restricted evidence excerpt is malformed")
            raw_excerpts = [
                {"content": content, "evidence_type": value.evidence_types[index] if index < len(value.evidence_types) else "candidate_detail"}
                for index, content in enumerate(value.excerpts)
            ]
            value = {
                "excerpts": raw_excerpts,
                "protected_evidence": [
                    {"content": item.content, "evidence_type": item.evidence_type}
                    for item in value.protected_evidence
                ],
                "provenance": value.provenance,
                "observed_at_utc": value.observed_at_utc,
                "retrieved_at_utc": value.retrieved_at_utc,
                "retrieved_at_monotonic_s": value.retrieved_at_monotonic_s,
                "late": value.late,
                "stale": value.stale,
                "truncated": value.truncated,
                "protected_review_required": value.protected_review_required,
            }
        if not isinstance(value, Mapping):
            raise EvidencePacketError("evidence attachment is malformed")
        raw_excerpts = value.get("excerpts", ())
        if not isinstance(raw_excerpts, (tuple, list)):
            raise EvidencePacketError("evidence excerpts are malformed")
        excerpts: list[str] = []
        evidence_types: list[str] = []
        protected_evidence: list[RestrictedEvidenceExcerpt] = []
        declared_types = value.get("evidence_types")
        if declared_types is not None and (
            not isinstance(declared_types, (tuple, list))
            or len(declared_types) != len(raw_excerpts)
            or not all(isinstance(item, str) for item in declared_types)
        ):
            raise EvidencePacketError("evidence types are malformed")
        for index, item in enumerate(raw_excerpts[: MAX_EXCERPTS + 1]):
            if isinstance(item, str):
                content = item
                evidence_type = declared_types[index] if declared_types is not None else "candidate_detail"
            elif isinstance(item, Mapping):
                content = item.get("content")
                evidence_type = item.get("evidence_type", "candidate_detail")
            else:
                raise EvidencePacketError("evidence excerpt is malformed")
            if not isinstance(content, str) or not isinstance(evidence_type, str):
                raise EvidencePacketError("evidence excerpt is malformed")
            kind = evidence_type.casefold()
            if kind in _PROTECTED_EVIDENCE_TYPES:
                if len(content) > MAX_EXCERPT_CHARS:
                    raise EvidencePacketError("restricted evidence excerpt exceeds its size bound")
                protected_evidence.append(RestrictedEvidenceExcerpt(content, kind))
            elif kind in _EVIDENCE_TYPES:
                excerpts.append(content)
                evidence_types.append(kind)
            else:
                raise EvidencePacketError("evidence excerpt type is unsupported")
        explicit_protected = value.get("protected_evidence", ())
        if not isinstance(explicit_protected, (tuple, list)) or len(explicit_protected) > MAX_EXCERPTS:
            raise EvidencePacketError("restricted evidence excerpts are malformed")
        for item in explicit_protected:
            if not isinstance(item, Mapping):
                raise EvidencePacketError("restricted evidence excerpt is malformed")
            content = item.get("content")
            kind = item.get("evidence_type", "audit_finding")
            if (
                not isinstance(content, str)
                or len(content) > MAX_EXCERPT_CHARS
                or not isinstance(kind, str)
                or kind.casefold() not in _PROTECTED_EVIDENCE_TYPES
            ):
                raise EvidencePacketError("restricted evidence excerpt is malformed")
            protected_evidence.append(RestrictedEvidenceExcerpt(content, kind.casefold()))
        if len(protected_evidence) > MAX_EXCERPTS:
            raise EvidencePacketError("too many restricted evidence excerpts")
        provenance = value.get("provenance", "retrospective_evaluator_evidence")
        if not isinstance(provenance, str) or provenance not in _PROVENANCE:
            raise EvidencePacketError("evidence provenance is unsupported")
        observed = value.get("observed_at_utc")
        retrieved = value.get("retrieved_at_utc")
        if not _utc_is_valid(observed) or not _utc_is_valid(retrieved):
            raise EvidencePacketError("evidence timestamp is malformed")
        retrieved_monotonic = value.get("retrieved_at_monotonic_s")
        if retrieved_monotonic is not None and (
            not isinstance(retrieved_monotonic, (int, float))
            or isinstance(retrieved_monotonic, bool)
            or not math.isfinite(float(retrieved_monotonic))
            or retrieved_monotonic < 0
        ):
            raise EvidencePacketError("monotonic evidence timestamp is malformed")
        bool_fields = ("late", "stale", "truncated", "protected_review_required")
        if any(key in value and not isinstance(value[key], bool) for key in bool_fields):
            raise EvidencePacketError("evidence status flags are malformed")
        if len(raw_excerpts) > MAX_EXCERPTS:
            excerpts = excerpts[:MAX_EXCERPTS]
            evidence_types = evidence_types[:MAX_EXCERPTS]
            truncated = True
        else:
            truncated = bool(value.get("truncated", False))
        return cls(
            excerpts=tuple(excerpts),
            evidence_types=tuple(evidence_types),
            protected_evidence=tuple(protected_evidence),
            provenance=provenance,
            observed_at_utc=observed,
            retrieved_at_utc=retrieved,
            retrieved_at_monotonic_s=float(retrieved_monotonic) if retrieved_monotonic is not None else None,
            late=bool(value.get("late", False)),
            stale=bool(value.get("stale", False)),
            truncated=truncated,
            protected_review_required=bool(value.get("protected_review_required", False)),
        )


@dataclass(frozen=True, slots=True)
class EvidenceSourceArtifact:
    """Versioned evaluator-side excerpts keyed to one planned candidate occurrence."""

    schema_version: str
    dataset_sha256: str
    identifier_seed: str
    attachments: Mapping[tuple[str, str, int], EvidenceAttachment]


def _seeded_reference_factory(seed: str, purpose: str) -> Callable[[], str]:
    key = bytes.fromhex(seed)
    counter = 0

    def next_reference() -> str:
        nonlocal counter
        message = f"skillscout-review-v1:{purpose}:{counter}".encode("utf-8")
        counter += 1
        return hmac.new(key, message, hashlib.sha256).hexdigest()[:32]

    return next_reference


def _attachment_to_dict(attachment: EvidenceAttachment) -> dict[str, Any]:
    return {
        "excerpts": [
            {"content": content, "evidence_type": attachment.evidence_types[index]}
            for index, content in enumerate(attachment.excerpts)
        ],
        "protected_evidence": [
            {"content": item.content, "evidence_type": item.evidence_type}
            for item in attachment.protected_evidence
        ],
        "provenance": attachment.provenance,
        "observed_at_utc": attachment.observed_at_utc,
        "retrieved_at_utc": attachment.retrieved_at_utc,
        "retrieved_at_monotonic_s": attachment.retrieved_at_monotonic_s,
        "late": attachment.late,
        "stale": attachment.stale,
        "truncated": attachment.truncated,
        "protected_review_required": attachment.protected_review_required,
    }


def _planned_pairs_by_id(tasks: Sequence[EvaluationTask] | None = None) -> dict[str, Any]:
    task_list = tuple(load_dataset().eligible_tasks if tasks is None else tasks)
    return {
        f"p{plan.pair_number:03d}-t{plan.task_index:02d}-r{plan.repetition}": plan
        for plan in build_schedule(task_list)
    }


def write_evidence_source_artifact(
    attachments: Mapping[tuple[str, str | ArmName, int], EvidenceAttachment | Mapping[str, Any]],
    path: str | Path,
    *,
    identifier_seed: str | None = None,
) -> Path:
    """Persist bounded source excerpts in a private, integrity-checked artifact."""
    planned = _planned_pairs_by_id()
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for key, raw_attachment in attachments.items():
        if not isinstance(key, tuple) or len(key) != 3:
            raise EvidencePacketError("evidence source occurrence key is malformed")
        pair_id, raw_arm, rank = key
        arm = _enum_text(raw_arm)
        if (
            not isinstance(pair_id, str)
            or pair_id not in planned
            or arm not in {item.value for item in ArmName}
            or not isinstance(rank, int)
            or isinstance(rank, bool)
            or rank not in {1, 2, 3}
        ):
            raise EvidencePacketError("evidence source occurrence key is outside the fixed schedule")
        occurrence = (pair_id, arm, rank)
        if occurrence in seen:
            raise EvidencePacketError("evidence source contains duplicate candidate occurrences")
        seen.add(occurrence)
        attachment = EvidenceAttachment.from_value(raw_attachment)
        entries.append({
            "pair_id": pair_id,
            "arm": arm,
            "rank": rank,
            "attachment": _attachment_to_dict(attachment),
        })
    entries.sort(key=lambda item: (item["pair_id"], item["arm"], item["rank"]))
    seed = secrets.token_hex(32) if identifier_seed is None else identifier_seed
    if not isinstance(seed, str) or not re.fullmatch(r"[0-9a-f]{64}", seed):
        raise EvidencePacketError("evidence source identifier seed must be 32 bytes of lowercase hexadecimal")
    payload = {
        "schema_version": EVIDENCE_SOURCE_SCHEMA,
        "dataset_sha256": DATASET_SHA256,
        "identifier_seed": seed,
        "entries": entries,
    }
    serialized = _canonical(payload)
    if len(serialized) > MAX_ARTIFACT_BYTES or has_credential_like_text(serialized.decode("utf-8")):
        raise EvidencePacketError("evidence source is oversized or contains credential-like text")
    destination = Path(path)
    _ensure_private_parent(destination)
    try:
        _write_new_private(destination, _canonical(_with_integrity(payload)))
    except FileExistsError as exc:
        raise EvidencePacketError("refusing to overwrite an existing evidence source artifact") from exc
    return destination


def load_evidence_source_artifact(path: str | Path) -> EvidenceSourceArtifact:
    """Load and validate the restricted input used by the offline packet builder."""
    value = _load_json(Path(path), restricted=True)
    if set(value) != _EVIDENCE_SOURCE_FIELDS or value.get("schema_version") != EVIDENCE_SOURCE_SCHEMA:
        raise EvidencePacketError("evidence source schema is malformed or unsupported")
    if value.get("dataset_sha256") != DATASET_SHA256:
        raise EvidencePacketError("evidence source dataset hash differs from the approved cohort")
    seed = value.get("identifier_seed")
    if not isinstance(seed, str) or not re.fullmatch(r"[0-9a-f]{64}", seed):
        raise EvidencePacketError("evidence source identifier seed is malformed")
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_CANDIDATE_OCCURRENCES:
        raise EvidencePacketError("evidence source collection exceeds the fixed cohort bounds")
    planned = _planned_pairs_by_id()
    attachments: dict[tuple[str, str, int], EvidenceAttachment] = {}
    for row in entries:
        if not isinstance(row, Mapping) or set(row) != _EVIDENCE_SOURCE_ENTRY_FIELDS:
            raise EvidencePacketError("evidence source entry is malformed")
        pair_id, arm, rank = row.get("pair_id"), row.get("arm"), row.get("rank")
        if (
            not isinstance(pair_id, str)
            or pair_id not in planned
            or not isinstance(arm, str)
            or arm not in {item.value for item in ArmName}
            or not isinstance(rank, int)
            or isinstance(rank, bool)
            or rank not in {1, 2, 3}
        ):
            raise EvidencePacketError("evidence source entry is outside the fixed schedule")
        occurrence = (pair_id, arm, rank)
        if occurrence in attachments:
            raise EvidencePacketError("evidence source repeats a candidate occurrence")
        raw_attachment = row.get("attachment")
        if not isinstance(raw_attachment, Mapping) or set(raw_attachment) != _EVIDENCE_SOURCE_ATTACHMENT_FIELDS:
            raise EvidencePacketError("evidence source attachment schema is malformed")
        excerpts = raw_attachment.get("excerpts")
        protected = raw_attachment.get("protected_evidence")
        if (
            not isinstance(excerpts, list)
            or any(not isinstance(item, Mapping) or set(item) != _EVIDENCE_SOURCE_EXCERPT_FIELDS for item in excerpts)
            or not isinstance(protected, list)
            or any(not isinstance(item, Mapping) or set(item) != _EVIDENCE_SOURCE_EXCERPT_FIELDS for item in protected)
        ):
            raise EvidencePacketError("evidence source excerpt schema is malformed")
        normalized = EvidenceAttachment.from_value(raw_attachment)
        attachments[occurrence] = normalized
    return EvidenceSourceArtifact(EVIDENCE_SOURCE_SCHEMA, DATASET_SHA256, seed, attachments)


@dataclass(frozen=True, slots=True)
class ReviewerEvidenceArtifact:
    schema_version: str
    opaque_candidate_id: str
    observed_revision_ref: str | None
    provenance: str
    retrieval_status: EvidenceStatus
    status_flags: tuple[str, ...]
    observed_at_utc: str | None
    retrieval_started_at_utc: str | None
    retrieval_completed_at_utc: str | None
    retrieval_deadline_at_utc: str | None
    packet: ReviewerPacket

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "opaque_candidate_id": self.opaque_candidate_id,
            "observed_revision_ref": self.observed_revision_ref,
            "provenance": self.provenance,
            "retrieval_status": self.retrieval_status.value,
            "status_flags": list(self.status_flags),
            "observed_at_utc": self.observed_at_utc,
            "retrieval_started_at_utc": self.retrieval_started_at_utc,
            "retrieval_completed_at_utc": self.retrieval_completed_at_utc,
            "retrieval_deadline_at_utc": self.retrieval_deadline_at_utc,
            "packet": self.packet.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ReviewPacketBundle:
    candidate_artifacts: tuple[ReviewerEvidenceArtifact, ...]
    decision_packets: tuple[ReviewerPacket, ...]
    candidate_crosswalk: tuple[dict[str, Any], ...]
    decision_crosswalk: tuple[dict[str, Any], ...]
    decision_gates: tuple[dict[str, Any], ...]
    protected_evidence: tuple[dict[str, Any], ...] = ()
    catalog_mode: str = "offline"
    dataset_sha256: str = DATASET_SHA256

    @property
    def reviewer_packets(self) -> tuple[ReviewerPacket, ...]:
        return tuple(artifact.packet for artifact in self.candidate_artifacts) + self.decision_packets

    def review_order(self, *, seed: int) -> tuple[ReviewerPacket, ...]:
        return prepare_review_order(self.reviewer_packets, seed=seed).packets

    def public_payload(self) -> dict[str, Any]:
        return {
            "schema_version": REVIEW_BUNDLE_SCHEMA,
            "dataset_sha256": self.dataset_sha256,
            "catalog_mode": self.catalog_mode,
            "candidate_artifacts": [item.to_dict() for item in self.candidate_artifacts],
            "decision_packets": [item.to_dict() for item in self.decision_packets],
        }

    def restricted_payload(self) -> dict[str, Any]:
        return {
            "schema_version": RESTRICTED_CROSSWALK_SCHEMA,
            "dataset_sha256": self.dataset_sha256,
            "candidate_entries": [dict(item) for item in self.candidate_crosswalk],
            "decision_entries": [dict(item) for item in self.decision_crosswalk],
            "decision_gates": [dict(item) for item in self.decision_gates],
            "protected_evidence": [dict(item) for item in self.protected_evidence],
        }


def _lookup_status(slot: Any) -> tuple[EvidenceStatus, tuple[str, ...], Any]:
    detail = _get(slot, "detail")
    if detail is None:
        return EvidenceStatus.MISSING, ("detail_not_recorded",), None
    status = _enum_text(_get(detail, "status"))
    identity = _enum_text(_get(detail, "identity_linkage"))
    revision = _enum_text(_get(detail, "revision_linkage"))
    flags: list[str] = []
    late = _get(detail, "late", False) or status == LookupStatus.NOT_STARTED_AFTER_DEADLINE.value
    stable_id = _get(slot, "stable_id")
    mismatch = (
        identity == LinkageStatus.MISMATCH.value
        or _get(detail, "returned_candidate_id") != stable_id
        or revision == LinkageStatus.MISMATCH.value
        or (
            _get(slot, "arm_content_hash")
            and _get(detail, "content_hash") != _get(slot, "arm_content_hash")
        )
        or (_get(slot, "arm_revision") and _get(detail, "revision") != _get(slot, "arm_revision"))
    )
    if late:
        flags.append("late")
    if mismatch:
        flags.append("mismatched")
    if late:
        return EvidenceStatus.LATE, tuple(flags), detail
    if mismatch:
        return EvidenceStatus.MISMATCHED, tuple(flags), detail
    if status == LookupStatus.FAILED.value:
        return EvidenceStatus.FAILED, ("retrieval_failed",), detail
    if status != LookupStatus.AVAILABLE.value:
        return EvidenceStatus.MISSING, ("detail_unavailable",), detail
    if identity != LinkageStatus.MATCHED.value or revision != LinkageStatus.MATCHED.value:
        return EvidenceStatus.UNVERIFIED, ("identity_or_revision_unverified",), detail
    return EvidenceStatus.COMPLETE, (), detail


def _attachment_timing(
    attachment: EvidenceAttachment,
    pair: Any,
    detail: Any,
) -> tuple[bool, bool]:
    """Return (late, unverified) without treating an absent timestamp as timely."""
    if attachment.late:
        return True, False
    if not attachment.excerpts and not attachment.protected_evidence:
        return False, False
    deadline = _get(pair, "lookup_deadline_monotonic_s")
    retrieved = attachment.retrieved_at_monotonic_s
    if retrieved is not None:
        if deadline is None:
            return False, True
        detail_end = _get(detail, "end_monotonic_s") if detail is not None else None
        if detail_end is not None and retrieved < detail_end:
            return False, True
        return retrieved > deadline, False
    if attachment.retrieved_at_utc is None or deadline is None:
        return False, True
    deadline_utc = _get(pair, "lookup_deadline_utc")
    if deadline_utc is None or not _utc_is_valid(deadline_utc):
        return False, True
    retrieved_utc = datetime.fromisoformat(attachment.retrieved_at_utc.replace("Z", "+00:00"))
    deadline_time = datetime.fromisoformat(deadline_utc.replace("Z", "+00:00"))
    if retrieved_utc > deadline_time:
        return True, False
    if (
        detail is not None
        and attachment.retrieved_at_utc == _get(detail, "end_utc")
        and _get(detail, "end_monotonic_s") is not None
        and _get(detail, "end_monotonic_s") <= deadline
        and not _get(detail, "late", False)
    ):
        return False, False
    return False, True


def _occurrence_key(pair_id: str, arm: ArmName, rank: int) -> tuple[str, str, int]:
    return pair_id, arm.value, rank


def _parse_candidate_crosswalk(entries: Iterable[Any]) -> dict[tuple[str, str, int], Mapping[str, Any]]:
    result: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    for value in entries:
        row = value.to_dict() if hasattr(value, "to_dict") else value
        if not isinstance(row, Mapping):
            raise EvidencePacketError("restricted candidate crosswalk is malformed")
        pair_id = row.get("pair_id")
        arm = _enum_text(row.get("arm"))
        rank = row.get("rank")
        if not isinstance(pair_id, str) or arm not in {item.value for item in ArmName} or not isinstance(rank, int):
            raise EvidencePacketError("restricted candidate crosswalk is malformed")
        key = (pair_id, arm, rank)
        if key in result:
            raise EvidencePacketError("restricted candidate crosswalk has duplicate occurrences")
        result[key] = row
    return result


def _new_reference(factory: Callable[[], str], *, forbidden: Iterable[str] = ()) -> str:
    value = factory()
    if not isinstance(value, str) or not _OPAQUE_RE.fullmatch(value):
        raise EvidencePacketError("opaque reference generator returned an invalid value")
    folded = value.casefold()
    if any(
        isinstance(item, str) and len(item) >= 5 and item.casefold() in folded
        for item in forbidden
    ):
        raise EvidencePacketError("opaque reference contains source identity")
    return value


def _safe_excerpt_text(raw: str, identifiers: Iterable[str], task: EvaluationTask) -> tuple[str | None, bool]:
    if _has_sensitive_disclosure(raw, task):
        return None, True
    sanitized = redact_credential_like_text(raw)
    for identifier in sorted((item for item in identifiers if item), key=len, reverse=True):
        sanitized = re.sub(re.escape(identifier), "[candidate identifier masked]", sanitized, flags=re.IGNORECASE)
    if has_protected_audit_disclosure(sanitized) or has_credential_like_text(sanitized) or _has_sensitive_disclosure(sanitized, task):
        return None, True
    return sanitized, sanitized != raw


def _parse_packet(value: Mapping[str, Any]) -> ReviewerPacket:
    if set(value) != _PACKET_FIELDS:
        raise EvidencePacketError("reviewer packet fields do not match the declared schema")
    try:
        evidence = []
        if not isinstance(value["evidence"], list):
            raise TypeError
        if not isinstance(value["criteria"], list) or not all(isinstance(item, str) for item in value["criteria"]):
            raise TypeError
        if not isinstance(value["decision_options"], list) or not all(isinstance(item, str) for item in value["decision_options"]):
            raise TypeError
        if value["context_status"] not in {"ready", "incomplete_by_protocol", "unverified"}:
            raise TypeError
        if not isinstance(value["context_reasons"], list) or not all(isinstance(item, str) for item in value["context_reasons"]):
            raise TypeError
        if not isinstance(value["evidence_reasons"], list) or not all(isinstance(item, str) for item in value["evidence_reasons"]):
            raise TypeError
        for row in value["evidence"]:
            if not isinstance(row, Mapping) or set(row) != _EXCERPT_FIELDS:
                raise TypeError
            if not isinstance(row.get("content"), str) or not isinstance(row.get("evidence_type"), str):
                raise TypeError
            if row.get("evidence_type") not in _EVIDENCE_TYPES:
                raise TypeError
            if row.get("provenance") not in _PROVENANCE:
                raise TypeError
            if row.get("retrieval_status") not in {item.value for item in EvidenceStatus}:
                raise TypeError
            if any(row.get(key) is not None and not isinstance(row.get(key), bool) for key in (
                "relevance_sufficient", "identity_linked", "revision_linked"
            )):
                raise TypeError
            if not isinstance(row.get("truncated"), bool):
                raise TypeError
            for key in ("candidate_ref", "observed_revision_ref"):
                ref = row.get(key)
                if ref is not None and (not isinstance(ref, str) or not _OPAQUE_RE.fullmatch(ref)):
                    raise TypeError
            if len(row["content"]) > MAX_EXCERPT_CHARS:
                raise TypeError
            evidence.append(EvidenceExcerpt(**row))
        packet = ReviewerPacket(
            packet_key=value["packet_key"],
            kind=ReviewKind(value["kind"]),
            task_text=value["task_text"],
            criteria=tuple(value["criteria"]),
            approved_context=value["approved_context"],
            evidence=tuple(evidence),
            decision_options=tuple(value["decision_options"]),
            schema_version=value["schema_version"],
            evidence_status=value["evidence_status"],
            assessment_allowed=value["assessment_allowed"],
            context_status=value["context_status"],
            context_reasons=tuple(value["context_reasons"]),
            evidence_reasons=tuple(value["evidence_reasons"]),
        )
    except (TypeError, KeyError, ValueError) as exc:
        raise EvidencePacketError("reviewer packet is malformed") from exc
    if not isinstance(packet.packet_key, str) or not _OPAQUE_RE.fullmatch(packet.packet_key):
        raise EvidencePacketError("reviewer packet key is malformed")
    if not isinstance(packet.task_text, str) or not isinstance(packet.approved_context, str):
        raise EvidencePacketError("reviewer packet text fields are malformed")
    if not all(isinstance(item, str) for item in (*packet.criteria, *packet.decision_options)):
        raise EvidencePacketError("reviewer packet lists are malformed")
    if not isinstance(packet.assessment_allowed, bool):
        raise EvidencePacketError("reviewer packet assessability is malformed")
    if len(packet.evidence) > MAX_EXCERPTS or sum(len(item.content) for item in packet.evidence) > MAX_TOTAL_EXCERPT_CHARS:
        raise EvidencePacketError("reviewer packet excerpts exceed the schema bounds")
    if packet.kind is ReviewKind.CANDIDATE_RELEVANCE and packet.decision_options:
        raise EvidencePacketError("candidate relevance packet contains decision options")
    if packet.kind is ReviewKind.DECISION_CONFORMITY and packet.decision_options != (
        "NO_SKILL_NEEDED", "USE", "REVIEW", "AVOID"
    ):
        raise EvidencePacketError("decision packet options do not match the approved schema")
    if packet.kind is ReviewKind.CANDIDATE_RELEVANCE and any(
        item.candidate_ref not in {None, packet.packet_key} for item in packet.evidence
    ):
        raise EvidencePacketError("candidate packet evidence references another candidate")
    if packet.assessment_allowed and (
        not packet.evidence
        or any(
            item.relevance_sufficient is not True
            or item.identity_linked is not True
            or item.revision_linked is not True
            or item.truncated
            or item.retrieval_status not in {EvidenceStatus.COMPLETE.value, EvidenceStatus.PARTIAL.value}
            for item in packet.evidence
        )
    ):
        raise EvidencePacketError("assessable reviewer packet lacks complete linked evidence")
    from skillscout.evaluation.reviews import ReviewError, _verify_packet_boundary
    try:
        _verify_packet_boundary(packet)
    except ReviewError as exc:
        raise EvidencePacketError("reviewer packet failed blinded schema validation") from exc
    return packet


class EvidencePacketPipeline:
    """Transform restricted pair records into blinded artifacts and a private crosswalk."""

    def __init__(
        self,
        *,
        key_factory: Callable[[], str] | None = None,
        revision_ref_factory: Callable[[], str] | None = None,
        decision_packet_key_factory: Callable[[], str] | None = None,
    ) -> None:
        self._key_factory = key_factory or (lambda: str(uuid.uuid4()))
        self._revision_ref_factory = revision_ref_factory or (lambda: str(uuid.uuid4()))
        self._decision_packet_key_factory = decision_packet_key_factory or (lambda: str(uuid.uuid4()))

    def build(
        self,
        pairs: Sequence[PairRecord | Mapping[str, Any]],
        tasks: Sequence[EvaluationTask],
        *,
        approved_context_by_task: Mapping[str, str],
        attachments: Mapping[tuple[str, str, int], EvidenceAttachment | Mapping[str, Any]] | None = None,
        candidate_crosswalk_entries: Iterable[Any] = (),
        decision_criteria_by_task: Mapping[str, Sequence[str]] | None = None,
        decision_assessable_pairs: Iterable[str] = (),
        protected_decision_pairs: Iterable[str] = (),
        catalog_mode: str = "offline",
    ) -> ReviewPacketBundle:
        if catalog_mode not in {"offline", "mocked"}:
            raise EvidencePacketError("review packet pipeline only accepts offline or mocked records")
        task_list = tuple(tasks)
        if len(task_list) != ELIGIBLE_TASK_COUNT:
            raise EvidencePacketError("review packet pipeline requires the fixed 14-task cohort")
        if set(approved_context_by_task) != {task.task_id for task in task_list} or any(
            not isinstance(value, str) for value in approved_context_by_task.values()
        ):
            raise EvidencePacketError("approved reviewer contexts must be explicitly supplied for all tasks")
        if len(pairs) != PLANNED_PAIR_COUNT:
            raise EvidencePacketError("review packet pipeline requires the fixed 42-pair schedule")
        errors = validate_records(pairs, task_list)
        if errors:
            raise EvidencePacketError("pair records failed fixed-schedule validation")
        task_by_id = {task.task_id: task for task in task_list}
        fixture_root = find_repository_root() / "docs/evaluation/v1/contexts"
        try:
            fixture_state = verify_fixture_pack(fixture_root, task_list)
        except (FixtureError, OSError, ValueError) as exc:
            raise EvidencePacketError("pinned task context readiness could not be verified") from exc
        context_by_task = {
            str(row["task_id"]): (str(row["context_status"]), tuple(row["blocked_facts"]))
            for row in fixture_state["fixtures"]
        }
        pair_by_id = {str(_get(pair, "pair_id")): pair for pair in pairs}
        attachments = attachments or {}
        source_crosswalk = _parse_candidate_crosswalk(candidate_crosswalk_entries)
        assessable_pair_ids = set(decision_assessable_pairs)
        protected_pairs = set(protected_decision_pairs)
        if not assessable_pair_ids.issubset(pair_by_id) or not protected_pairs.issubset(pair_by_id):
            raise EvidencePacketError("decision review gate refers to an unknown planned pair")

        candidate_key_factory = self._key_factory
        candidate_identity_keys: dict[tuple[str, str, str, str], str] = {}
        revision_refs: dict[tuple[str, str, str], str] = {}
        candidate_crosswalk: list[dict[str, Any]] = []
        protected_evidence_rows: list[dict[str, Any]] = []
        pair_safe_evidence: dict[str, list[EvidenceExcerpt]] = defaultdict(list)
        pair_protected: set[str] = set(protected_pairs)
        artifact_occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for pair in pairs:
            pair_id = str(_get(pair, "pair_id"))
            task_id = str(_get(pair, "task_id"))
            task = task_by_id[task_id]
            repetition = int(_get(pair, "repetition"))
            for arm_name in ArmName:
                arm = _arm_record(pair, arm_name)
                if arm is None:
                    continue
                for slot in _get(arm, "slots", ()):
                    if _enum_text(_get(slot, "status")) != SlotStatus.OCCUPIED.value:
                        continue
                    stable_id = _get(slot, "stable_id")
                    rank = _get(slot, "rank")
                    if not isinstance(stable_id, str) or not stable_id.strip() or not isinstance(rank, int):
                        raise EvidencePacketError("occupied candidate is missing identity or rank")
                    arm_hash = _get(slot, "arm_content_hash")
                    arm_revision = _get(slot, "arm_revision")
                    source_slug = str(_get(slot, "source_slug") or "")
                    occurrence = _occurrence_key(pair_id, arm_name, rank)
                    crosswalk_source = source_crosswalk.get(occurrence)
                    if crosswalk_source is not None and (
                        crosswalk_source.get("stable_id") != stable_id
                        or _enum_text(crosswalk_source.get("arm")) != arm_name.value
                        or crosswalk_source.get("task_id") != task_id
                        or crosswalk_source.get("pair_id") != pair_id
                        or crosswalk_source.get("repetition") != repetition
                        or crosswalk_source.get("rank") != rank
                        or (
                            _get(slot, "opaque_candidate_key") is not None
                            and crosswalk_source.get("opaque_candidate_key") is not None
                            and crosswalk_source.get("opaque_candidate_key") != _get(slot, "opaque_candidate_key")
                        )
                        or (crosswalk_source.get("content_hash") is not None and crosswalk_source.get("content_hash") != arm_hash)
                        or (crosswalk_source.get("revision") is not None and crosswalk_source.get("revision") != arm_revision)
                    ):
                        raise EvidencePacketError("candidate identity does not match its restricted crosswalk")
                    identity_key = (task_id, stable_id, str(arm_hash or ""), str(arm_revision or ""))
                    if not arm_hash and not arm_revision:
                        identity_key = (task_id, stable_id, f"{pair_id}:{arm_name.value}:{rank}", "")
                    opaque = _get(slot, "opaque_candidate_key")
                    forbidden_candidate_identity = [stable_id, source_slug, task_id]
                    if crosswalk_source:
                        forbidden_candidate_identity.extend((
                            str(crosswalk_source.get("source_url") or ""),
                            str(crosswalk_source.get("source_slug") or ""),
                        ))
                    if isinstance(opaque, str) and (
                        not _OPAQUE_RE.fullmatch(opaque)
                        or any(
                            len(identity) >= 5 and identity.casefold() in opaque.casefold()
                            for identity in forbidden_candidate_identity if identity
                        )
                    ):
                        opaque = None
                    if opaque is None:
                        opaque = candidate_identity_keys.get(identity_key)
                        if opaque is None:
                            opaque = _new_reference(candidate_key_factory, forbidden=forbidden_candidate_identity)
                            candidate_identity_keys[identity_key] = opaque
                    if not isinstance(opaque, str) or not _OPAQUE_RE.fullmatch(opaque):
                        raise EvidencePacketError("runtime candidate reference is not opaque")
                    if hasattr(slot, "opaque_candidate_key"):
                        slot.opaque_candidate_key = opaque
                    elif isinstance(slot, dict):
                        slot["opaque_candidate_key"] = opaque
                    detail_status, detail_flags, detail = _lookup_status(slot)
                    attachment = EvidenceAttachment.from_value(attachments.get(occurrence))
                    attachment_late, timing_unverified = _attachment_timing(attachment, pair, detail)
                    def retain_protected_excerpt(content: str, evidence_type: str) -> None:
                        pair_protected.add(pair_id)
                        if has_credential_like_text(content):
                            return
                        row = {
                            "opaque_candidate_id": opaque,
                            "task_id": task_id,
                            "pair_id": pair_id,
                            "repetition": repetition,
                            "arm": arm_name.value,
                            "rank": rank,
                            "evidence_type": evidence_type,
                            "provenance": attachment.provenance,
                            "content": content,
                            "observed_at_utc": attachment.observed_at_utc or (
                                _get(detail, "end_utc") if detail is not None else None
                            ),
                            "retrieved_at_utc": attachment.retrieved_at_utc or (
                                _get(detail, "end_utc") if detail is not None else None
                            ),
                        }
                        if not any(
                            existing["opaque_candidate_id"] == opaque
                            and existing["pair_id"] == pair_id
                            and existing["content"] == content
                            for existing in protected_evidence_rows
                        ):
                            protected_evidence_rows.append(row)

                    for protected_excerpt in attachment.protected_evidence:
                        retain_protected_excerpt(protected_excerpt.content, protected_excerpt.evidence_type)
                    if attachment.protected_review_required or has_protected_audit_disclosure(task.project_context):
                        pair_protected.add(pair_id)
                    sufficient = _get(detail, "detail_sufficient_for_relevance") is True if detail is not None else False
                    context_status, context_reasons = context_by_task[task_id]
                    raw_excerpt_rows = list(zip(attachment.excerpts, attachment.evidence_types or ("candidate_detail",) * len(attachment.excerpts)))
                    excerpts: list[EvidenceExcerpt] = []
                    sensitive = False
                    truncated = attachment.truncated
                    char_budget = MAX_TOTAL_EXCERPT_CHARS
                    identities = [stable_id, str(_get(slot, "source_slug") or "")]
                    if crosswalk_source:
                        identities.extend(str(crosswalk_source.get(key) or "") for key in ("source_url",))
                    for raw_text, evidence_type in raw_excerpt_rows[:MAX_EXCERPTS]:
                        if evidence_type not in _EVIDENCE_TYPES:
                            continue
                        if len(raw_text) > MAX_EXCERPT_CHARS or len(raw_text) > char_budget:
                            truncated = True
                        bounded = raw_text[: min(MAX_EXCERPT_CHARS, char_budget)]
                        char_budget -= len(bounded)
                        protected_kind = _detected_protected_evidence_type(bounded)
                        if protected_kind:
                            retain_protected_excerpt(bounded, protected_kind)
                        safe_text, redacted_or_blocked = _safe_excerpt_text(bounded, identities, task)
                        if redacted_or_blocked:
                            sensitive = True
                            continue
                        if safe_text:
                            excerpts.append(EvidenceExcerpt(
                                content=safe_text,
                                evidence_type=evidence_type,
                                provenance=attachment.provenance,
                                relevance_sufficient=_get(detail, "detail_sufficient_for_relevance"),
                                identity_linked=_enum_text(_get(detail, "identity_linkage")) == LinkageStatus.MATCHED.value,
                                revision_linked=_enum_text(_get(detail, "revision_linkage")) == LinkageStatus.MATCHED.value,
                                retrieval_status=detail_status.value,
                                candidate_ref=opaque,
                                truncated=truncated,
                            ))
                    status = detail_status
                    flags = list(detail_flags)
                    if attachment_late:
                        status = EvidenceStatus.LATE
                        flags.append("late")
                    if status is EvidenceStatus.COMPLETE and not sufficient:
                        status = EvidenceStatus.PARTIAL
                        flags.append("detail_insufficient")
                    if attachment.stale:
                        status = EvidenceStatus.STALE
                        flags.append("source_marked_stale")
                    if timing_unverified and status in {EvidenceStatus.COMPLETE, EvidenceStatus.PARTIAL}:
                        status = EvidenceStatus.UNVERIFIED
                        flags.append("evidence_timing_unverified")
                    if sensitive:
                        status = EvidenceStatus.UNVERIFIED
                        flags.append("protected_text_withheld")
                        excerpts = []
                    if truncated:
                        if status is EvidenceStatus.COMPLETE:
                            status = EvidenceStatus.PARTIAL
                        flags.append("excerpt_truncated")
                        excerpts = [EvidenceExcerpt(
                            content=item.content,
                            evidence_type=item.evidence_type,
                            provenance=item.provenance,
                            relevance_sufficient=item.relevance_sufficient,
                            identity_linked=item.identity_linked,
                            revision_linked=item.revision_linked,
                            retrieval_status=status.value,
                            candidate_ref=item.candidate_ref,
                            truncated=True,
                        ) for item in excerpts]
                    if not excerpts and status in {EvidenceStatus.COMPLETE, EvidenceStatus.PARTIAL}:
                        status = EvidenceStatus.MISSING
                        flags.append("excerpt_missing")
                    if status not in {EvidenceStatus.COMPLETE, EvidenceStatus.PARTIAL}:
                        excerpts = []
                    if not sufficient:
                        if "detail_insufficient" not in flags:
                            flags.append("detail_insufficient")
                    if detail is None:
                        retrieval_started = None
                        retrieval_completed = None
                        observed_at = attachment.observed_at_utc
                    else:
                        retrieval_started = _get(detail, "start_utc")
                        retrieval_completed = _get(detail, "end_utc") or attachment.retrieved_at_utc
                        observed_at = attachment.observed_at_utc or _get(detail, "end_utc")
                    retrieval_deadline = _get(pair, "lookup_deadline_utc")
                    for stamp in (retrieval_started, retrieval_completed, observed_at, retrieval_deadline):
                        if not _utc_is_valid(stamp):
                            raise EvidencePacketError("runtime evidence timestamps are malformed")
                    observed_revision = _get(detail, "revision") if detail is not None else None
                    observed_hash = _get(detail, "content_hash") if detail is not None else None
                    revision_ref = None
                    if observed_revision or observed_hash:
                        rev_key = (task_id, stable_id, f"{observed_hash or ''}:{observed_revision or ''}")
                        revision_ref = revision_refs.get(rev_key)
                        if revision_ref is None:
                            revision_ref = _new_reference(
                                self._revision_ref_factory,
                                forbidden=(stable_id, task_id, str(observed_revision or ""), str(observed_hash or "")),
                            )
                            revision_refs[rev_key] = revision_ref
                    excerpts = [EvidenceExcerpt(
                        content=item.content,
                        evidence_type=item.evidence_type,
                        provenance=item.provenance,
                        relevance_sufficient=item.relevance_sufficient,
                        identity_linked=item.identity_linked,
                        revision_linked=item.revision_linked,
                        retrieval_status=status.value,
                        candidate_ref=opaque,
                        observed_revision_ref=revision_ref,
                        truncated=item.truncated,
                    ) for item in excerpts]
                    assessment_allowed = (
                        status in {EvidenceStatus.COMPLETE, EvidenceStatus.PARTIAL}
                        and sufficient
                        and not truncated
                        and not sensitive
                        and context_status == "ready"
                    )
                    if context_status != "ready":
                        flags.append("context_incomplete")
                    for item in excerpts:
                        if item.content:
                            pair_safe_evidence[pair_id].append(item)
                    record = {
                        "task": task,
                        "pair_id": pair_id,
                        "repetition": repetition,
                        "arm": arm_name,
                        "rank": rank,
                        "slot": slot,
                        "stable_id": stable_id,
                        "opaque": opaque,
                        "arm_hash": arm_hash,
                        "arm_revision": arm_revision,
                        "observed_hash": observed_hash,
                        "observed_revision": observed_revision,
                        "revision_ref": revision_ref,
                        "status": status,
                        "flags": tuple(sorted(set(flags))),
                        "provenance": attachment.provenance,
                        "observed_at": observed_at,
                        "retrieval_started": retrieval_started,
                        "retrieval_completed": retrieval_completed,
                        "retrieval_deadline": retrieval_deadline,
                        "excerpts": tuple(excerpts),
                        "assessment_allowed": assessment_allowed,
                        "context_status": context_status,
                        "context_reasons": context_reasons,
                        "crosswalk_source": crosswalk_source,
                    }
                    artifact_occurrences[opaque].append(record)
                    candidate_crosswalk.append({
                        "opaque_candidate_id": opaque,
                        "task_id": task_id,
                        "pair_id": pair_id,
                        "repetition": repetition,
                        "arm": arm_name.value,
                        "rank": rank,
                        "stable_id": stable_id,
                        "source_slug": _get(slot, "source_slug"),
                        "source_url": _safe_source_url(crosswalk_source.get("source_url")) if crosswalk_source else None,
                        "arm_content_hash": arm_hash,
                        "arm_revision": arm_revision,
                        "observed_content_hash": observed_hash,
                        "observed_revision": observed_revision,
                        "observed_revision_ref": revision_ref,
                        "evaluator_status_flags": tuple(sorted(set((*flags, status.value)))),
                    })

        candidate_artifacts: list[ReviewerEvidenceArtifact] = []
        used_packet_keys: set[str] = set()
        for opaque, records in sorted(artifact_occurrences.items()):
            identities = {(item["task"].task_id, item["stable_id"], item["arm_hash"], item["arm_revision"]) for item in records}
            if len(identities) != 1:
                raise EvidencePacketError("one opaque candidate reference maps to conflicting identities or revisions")
            task = records[0]["task"]
            safe_records = [item for item in records if item["assessment_allowed"]]
            chosen = sorted(safe_records or records, key=lambda item: (item["status"] not in {EvidenceStatus.COMPLETE, EvidenceStatus.PARTIAL}, item["pair_id"]))[0]
            chosen_status = chosen["status"]
            assessment_allowed = chosen["assessment_allowed"]
            context_status = chosen["context_status"]
            context_reasons = chosen["context_reasons"]
            excerpts = list(chosen["excerpts"])
            identifiers = [item["stable_id"] for item in records]
            identifiers.extend(str(_get(item["slot"], "source_slug") or "") for item in records)
            if chosen["crosswalk_source"]:
                identifiers.append(str(chosen["crosswalk_source"].get("source_url") or ""))
            # Packet text is built only from the task's approved reviewer fields and selected excerpts.
            try:
                packet = build_candidate_relevance_packet(
                    task,
                    approved_context=approved_context_by_task[task.task_id],
                    evidence=excerpts,
                    packet_key_factory=lambda value=opaque: value,
                    identifiers_to_mask=identifiers,
                    evidence_status=chosen_status.value,
                    assessment_allowed=assessment_allowed,
                    context_status=context_status,
                    context_reasons=context_reasons,
                    evidence_reasons=tuple(sorted({
                        "evidence_unavailable_for_review"
                        if flag == "protected_text_withheld"
                        else flag
                        for flag in chosen["flags"]
                    })),
                )
            except (ValueError, TypeError) as exc:
                raise EvidencePacketError("candidate reviewer packet failed the blinded boundary") from exc
            if any(_has_sensitive_disclosure(text, task) for text in _packet_texts(packet)):
                raise EvidencePacketError("candidate reviewer packet contains protected task disclosure")
            if packet.packet_key in used_packet_keys:
                raise EvidencePacketError("review packet keys are not unique")
            used_packet_keys.add(packet.packet_key)
            artifact = ReviewerEvidenceArtifact(
                schema_version=EVIDENCE_ARTIFACT_SCHEMA,
                opaque_candidate_id=opaque,
                observed_revision_ref=chosen["revision_ref"],
                provenance=chosen["provenance"],
                retrieval_status=chosen_status,
                status_flags=tuple(sorted({
                    *(flag for item in records for flag in item["flags"] if flag != "protected_text_withheld"),
                    *(item["status"].value for item in records),
                })),
                observed_at_utc=chosen["observed_at"],
                retrieval_started_at_utc=chosen["retrieval_started"],
                retrieval_completed_at_utc=chosen["retrieval_completed"],
                retrieval_deadline_at_utc=chosen["retrieval_deadline"],
                packet=packet,
            )
            _verify_reviewer_artifact(artifact.to_dict(), task)
            candidate_artifacts.append(artifact)

        # The same task/revision packet key can occur in several pairs. Candidate labels
        # are revision-level; each slot retains its own timed evidence gate in the metric.
        decision_packets: list[ReviewerPacket] = []
        decision_crosswalk: list[dict[str, Any]] = []
        decision_gates: list[dict[str, Any]] = []
        decision_key_used = set(used_packet_keys)
        criteria_map = decision_criteria_by_task or {}
        for pair in pairs:
            pair_id = str(_get(pair, "pair_id"))
            task_id = str(_get(pair, "task_id"))
            task = task_by_id[task_id]
            scout = _arm_record(pair, ArmName.SKILLSCOUT)
            context_status, _context_reasons = context_by_task[task_id]
            if context_status != "ready":
                decision_gates.append({"pair_id": pair_id, "task_id": task_id, "status": "indeterminate_context_incomplete"})
                continue
            if pair_id in pair_protected or has_protected_audit_disclosure(task.project_context):
                decision_gates.append({"pair_id": pair_id, "task_id": task_id, "status": "indeterminate_protected_evidence_withheld"})
                continue
            criteria = criteria_map.get(task_id)
            if not criteria or not isinstance(criteria, (tuple, list)) or not all(isinstance(item, str) for item in criteria):
                decision_gates.append({"pair_id": pair_id, "task_id": task_id, "status": "indeterminate_decision_criteria_unapproved"})
                continue
            if not _get(scout, "primary_response_valid", False) or _get(scout, "decision") is None:
                decision_gates.append({"pair_id": pair_id, "task_id": task_id, "status": "indeterminate_no_valid_decision"})
                continue
            key = _new_reference(self._decision_packet_key_factory, forbidden=(task_id,))
            while key in decision_key_used:
                key = _new_reference(self._decision_packet_key_factory, forbidden=(task_id,))
            decision_key_used.add(key)
            evidence = _deduplicate_decision_evidence(pair_safe_evidence.get(pair_id, ()))
            decision_allowed = pair_id in assessable_pair_ids and bool(evidence)
            packet_status = (
                EvidenceStatus.COMPLETE.value
                if decision_allowed
                else EvidenceStatus.UNVERIFIED.value if evidence else EvidenceStatus.MISSING.value
            )
            try:
                packet = build_decision_conformity_packet(
                    task,
                    approved_context=approved_context_by_task[task_id],
                    evidence=evidence,
                    packet_key_factory=lambda value=key: value,
                    decision_criteria=criteria,
                    evidence_status=packet_status,
                    assessment_allowed=decision_allowed,
                    evidence_reasons=(() if decision_allowed else ("decision_evidence_not_assessable",)),
                )
            except (ValueError, TypeError) as exc:
                raise EvidencePacketError("decision reviewer packet failed the blinded boundary") from exc
            if any(_has_sensitive_disclosure(text, task) for text in _packet_texts(packet)):
                raise EvidencePacketError("decision reviewer packet contains protected task disclosure")
            decision_packets.append(packet)
            decision_crosswalk.append({
                "opaque_decision_packet_key": key,
                "pair_id": pair_id,
                "task_id": task_id,
                "repetition": int(_get(pair, "repetition")),
                "arm": ArmName.SKILLSCOUT.value,
            })

        return ReviewPacketBundle(
            candidate_artifacts=tuple(candidate_artifacts),
            decision_packets=tuple(decision_packets),
            candidate_crosswalk=tuple(candidate_crosswalk),
            decision_crosswalk=tuple(decision_crosswalk),
            decision_gates=tuple(decision_gates),
            protected_evidence=tuple(protected_evidence_rows),
            catalog_mode=catalog_mode,
        )


def build_review_artifacts_from_run(
    run_directory: str | Path,
    *,
    reviewer_path: str | Path,
    restricted_crosswalk_path: str | Path,
    evidence_source_path: str | Path | None = None,
    approved_context_by_task: Mapping[str, str] | None = None,
    decision_criteria_by_task: Mapping[str, Sequence[str]] | None = None,
    decision_assessable_pairs: Iterable[str] = (),
    protected_decision_pairs: Iterable[str] = (),
    pipeline: EvidencePacketPipeline | None = None,
) -> tuple[ReviewPacketBundle, Path, Path]:
    """Load a persisted offline run and produce its separated reviewer artifacts."""
    from skillscout.evaluation.store import RunStoreError, read_run_bundle

    try:
        run = read_run_bundle(run_directory)
    except (OSError, RunStoreError) as exc:
        raise EvidencePacketError("cannot load a validated offline evaluation run") from exc
    dataset = load_dataset()
    tasks = dataset.eligible_tasks
    errors = validate_records(run.pairs, tasks, dataset_sha256=run.manifest.get("dataset_sha256", ""))
    if errors:
        raise EvidencePacketError("persisted run records failed fixed-schedule validation")
    if approved_context_by_task is None:
        # Empty means no additional project facts were approved for reviewer use.
        contexts = {task.task_id: "" for task in tasks}
    else:
        contexts = dict(approved_context_by_task)
    source_artifact = load_evidence_source_artifact(evidence_source_path) if evidence_source_path is not None else None
    attachments = dict(source_artifact.attachments) if source_artifact is not None else {}
    occupied: set[tuple[str, str, int]] = set()
    for pair in run.pairs:
        pair_id = str(pair.get("pair_id"))
        arms = pair.get("arms", {})
        if not isinstance(arms, Mapping):
            raise EvidencePacketError("persisted run arm records are malformed")
        for arm_name, arm_record in arms.items():
            arm = _enum_text(arm_name)
            if arm not in {item.value for item in ArmName} or not isinstance(arm_record, Mapping):
                raise EvidencePacketError("persisted run arm records are malformed")
            for slot in arm_record.get("slots", ()):
                if not isinstance(slot, Mapping):
                    raise EvidencePacketError("persisted run candidate slots are malformed")
                if slot.get("status") == SlotStatus.OCCUPIED.value:
                    rank = slot.get("rank")
                    if not isinstance(rank, int) or isinstance(rank, bool):
                        raise EvidencePacketError("persisted candidate occurrence has no valid rank")
                    occupied.add((pair_id, arm, rank))
    if not set(attachments).issubset(occupied):
        raise EvidencePacketError("evidence source refers to a candidate occurrence absent from the run")
    reviewer_parent = Path(reviewer_path).parent
    restricted_parent = Path(restricted_crosswalk_path).parent
    sensitive_directories = [Path(run_directory)]
    if evidence_source_path is not None:
        sensitive_directories.append(Path(evidence_source_path).parent)
    if any(
        _paths_overlap(output_parent, sensitive_directory)
        for output_parent in (reviewer_parent, restricted_parent)
        for sensitive_directory in sensitive_directories
    ):
        raise EvidencePacketError("reviewer artifacts must be stored outside the run and evidence-source directories")
    packet_pipeline = pipeline
    if packet_pipeline is None and source_artifact is not None:
        packet_pipeline = EvidencePacketPipeline(
            key_factory=_seeded_reference_factory(source_artifact.identifier_seed, "candidate"),
            revision_ref_factory=_seeded_reference_factory(source_artifact.identifier_seed, "revision"),
            decision_packet_key_factory=_seeded_reference_factory(source_artifact.identifier_seed, "decision"),
        )
    if packet_pipeline is None:
        packet_pipeline = EvidencePacketPipeline()
    bundle = packet_pipeline.build(
        run.pairs,
        tasks,
        approved_context_by_task=contexts,
        attachments=attachments,
        candidate_crosswalk_entries=run.candidate_crosswalk,
        decision_criteria_by_task=decision_criteria_by_task,
        decision_assessable_pairs=decision_assessable_pairs,
        protected_decision_pairs=protected_decision_pairs,
        catalog_mode=run.manifest.get("catalog_mode", "offline"),
    )
    public_path, restricted_path = write_review_packet_artifacts(
        bundle,
        reviewer_path,
        restricted_crosswalk_path,
        catalog_mode=run.manifest.get("catalog_mode", "offline"),
    )
    return bundle, public_path, restricted_path


def _deduplicate_decision_evidence(items: Iterable[EvidenceExcerpt]) -> tuple[EvidenceExcerpt, ...]:
    unique: dict[tuple[str | None, str, str], EvidenceExcerpt] = {}
    for item in items:
        key = (item.candidate_ref, item.content, item.evidence_type)
        unique.setdefault(key, item)
    return tuple(unique[key] for key in sorted(unique, key=lambda item: (item[0] or "", item[2], item[1])))


def _verify_reviewer_artifact(value: Mapping[str, Any], task: EvaluationTask | None = None) -> None:
    if set(value) != _ARTIFACT_FIELDS:
        raise EvidencePacketError("evidence artifact fields do not match the declared schema")
    if value.get("schema_version") != EVIDENCE_ARTIFACT_SCHEMA:
        raise EvidencePacketError("unsupported evidence artifact schema")
    candidate_key = value.get("opaque_candidate_id")
    if not isinstance(candidate_key, str) or not _OPAQUE_RE.fullmatch(candidate_key):
        raise EvidencePacketError("evidence artifact candidate reference is malformed")
    if value.get("provenance") not in _PROVENANCE or value.get("retrieval_status") not in {item.value for item in EvidenceStatus}:
        raise EvidencePacketError("evidence artifact status or provenance is malformed")
    if not isinstance(value.get("status_flags"), list) or any(
        not isinstance(flag, str) or flag not in _STATUS_FLAGS for flag in value["status_flags"]
    ):
        raise EvidencePacketError("evidence artifact flags are malformed")
    for flag in value["status_flags"]:
        if not isinstance(flag, str) or not re.fullmatch(r"[a-z_]{1,64}", flag):
            raise EvidencePacketError("evidence artifact flags are malformed")
    for key in (
        "observed_at_utc", "retrieval_started_at_utc", "retrieval_completed_at_utc", "retrieval_deadline_at_utc"
    ):
        if not _utc_is_valid(value.get(key)):
            raise EvidencePacketError("evidence artifact timestamp is malformed")
    revision_ref = value.get("observed_revision_ref")
    if revision_ref is not None and (not isinstance(revision_ref, str) or not _OPAQUE_RE.fullmatch(revision_ref)):
        raise EvidencePacketError("evidence artifact revision reference is malformed")
    packet_data = value.get("packet")
    if not isinstance(packet_data, Mapping) or set(packet_data) != _PACKET_FIELDS:
        raise EvidencePacketError("embedded reviewer packet fields do not match the declared schema")
    packet = _parse_packet(packet_data)
    if packet.packet_key != candidate_key or packet.kind is not ReviewKind.CANDIDATE_RELEVANCE:
        raise EvidencePacketError("candidate artifact and reviewer packet identity do not match")
    if task is not None:
        for text_value in _packet_texts(packet):
            if _has_sensitive_disclosure(text_value, task):
                raise EvidencePacketError("reviewer artifact contains protected task disclosure")
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if has_credential_like_text(serialized) or has_protected_audit_disclosure(serialized):
        raise EvidencePacketError("reviewer artifact contains protected text")


def _packet_texts(packet: ReviewerPacket) -> Iterable[str]:
    yield packet.task_text
    yield packet.approved_context
    yield from packet.criteria
    yield from packet.context_reasons
    yield from packet.evidence_reasons
    for item in packet.evidence:
        yield item.content


def _verify_packet_boundary_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).casefold()
            if name in {
                "arm", "arm_name", "system", "system_identity", "decision", "original_decision",
                "expected_decision", "expected_label", "category", "task_category", "rank", "score",
                "scores", "popularity", "install_count", "safety_finding", "audit_status", "scanner_finding",
                "stable_id", "source_url", "task_id", "fixture_description", "crosswalk", "candidate_id",
            }:
                raise EvidencePacketError("reviewer artifact contains a prohibited field")
            _verify_packet_boundary_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _verify_packet_boundary_fields(item)
    elif isinstance(value, str):
        if has_credential_like_text(value) or has_protected_audit_disclosure(value) or _SENSITIVE_FIELD_RE.search(value) or _DISCLOSED_DECISION_RE.search(value):
            raise EvidencePacketError("reviewer artifact contains protected text")


def apply_reviews_and_derive_metrics(
    bundle: ReviewPacketBundle,
    pairs: Sequence[PairRecord | Mapping[str, Any]],
    tasks: Sequence[EvaluationTask],
    ledger: ReviewLedger,
    *,
    task_gates_approved: Mapping[str, bool] | bool = False,
) -> dict[str, Any]:
    """Apply only locked blinded reviews, then derive the existing metric plan."""
    if bundle.dataset_sha256 != DATASET_SHA256 or bundle.catalog_mode not in {"offline", "mocked"}:
        raise EvidencePacketError("review bundle cohort or mode is invalid")
    packets_by_key = {packet.packet_key: packet for packet in bundle.reviewer_packets}
    for event in ledger.events:
        packet = packets_by_key.get(event.packet_key)
        if packet is None or packet.kind is not event.kind or packet.assessment_allowed != event.packet_assessment_allowed:
            raise EvidencePacketError("review ledger event does not match a packet in this bundle")
        if not packet.assessment_allowed and (event.assessable or event.action not in {"unknown", "unassessable", "unresolved"}):
            raise EvidencePacketError("review ledger exceeds the packet evidence gate")
    apply_candidate_review_labels(pairs, ledger)
    decision_keys = {
        str(item["pair_id"]): str(item["opaque_decision_packet_key"])
        for item in bundle.decision_crosswalk
    }
    outcomes = decision_outcomes_by_pair(
        pairs,
        ledger,
        decision_keys,
        task_gates_approved=task_gates_approved,
    )
    report = compute_metrics(pairs, tasks, decision_outcomes=outcomes)
    return {"decision_outcomes": outcomes, "metrics": report}


def _ensure_private_parent(path: Path) -> None:
    parent = path.parent
    if not parent.exists():
        parent.mkdir(mode=0o700, parents=True)
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or parent.is_symlink() or stat.S_IMODE(info.st_mode) & 0o077:
        raise EvidencePacketError("artifact parent directory must be a private real directory")


def _paths_overlap(first: Path, second: Path) -> bool:
    a = first.resolve(strict=False)
    b = second.resolve(strict=False)
    return a == b or a in b.parents or b in a.parents


def _unlink_if_owned(path: Path, device: int, inode: int) -> None:
    try:
        current = path.lstat()
        if current.st_dev == device and current.st_ino == inode and stat.S_ISREG(current.st_mode):
            path.unlink()
    except OSError:
        return


def _write_new_private(path: Path, payload: bytes) -> tuple[int, int]:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    created = True
    info = os.fstat(descriptor)
    try:
        if not stat.S_ISREG(info.st_mode):
            raise EvidencePacketError("artifact target must be a regular file")
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise EvidencePacketError("artifact write did not complete")
            offset += written
    except Exception:
        if created:
            _unlink_if_owned(path, info.st_dev, info.st_ino)
        raise
    finally:
        os.close(descriptor)
    return info.st_dev, info.st_ino


def write_review_packet_artifacts(
    bundle: ReviewPacketBundle,
    reviewer_path: str | Path,
    restricted_crosswalk_path: str | Path,
    *,
    catalog_mode: str = "offline",
) -> tuple[Path, Path]:
    """Write deterministic reviewer packets and a separate mode-0600 crosswalk."""
    if catalog_mode not in {"offline", "mocked"} or bundle.catalog_mode not in {"offline", "mocked"}:
        raise EvidencePacketError("live review artifact persistence is disabled")
    public_path = Path(reviewer_path)
    restricted_path = Path(restricted_crosswalk_path)
    try:
        public_parent = public_path.parent.resolve(strict=False)
        restricted_parent = restricted_path.parent.resolve(strict=False)
        if public_parent == restricted_parent or public_parent in restricted_parent.parents or restricted_parent in public_parent.parents:
            raise EvidencePacketError("reviewer packets and restricted crosswalk require separate directories")
    except OSError as exc:
        raise EvidencePacketError("artifact paths are invalid") from exc
    _ensure_private_parent(public_path)
    _ensure_private_parent(restricted_path)
    if public_path.exists() or public_path.is_symlink() or restricted_path.exists() or restricted_path.is_symlink():
        raise EvidencePacketError("refusing to overwrite an existing evidence artifact")
    public_payload = bundle.public_payload()
    restricted_payload = bundle.restricted_payload()
    if (
        len(public_payload["candidate_artifacts"]) > MAX_CANDIDATE_OCCURRENCES
        or len(public_payload["decision_packets"]) > MAX_DECISION_PACKETS
        or len(restricted_payload["candidate_entries"]) > MAX_CANDIDATE_OCCURRENCES
        or len(restricted_payload["decision_entries"]) > MAX_DECISION_PACKETS
        or len(restricted_payload["decision_gates"]) > PLANNED_PAIR_COUNT
        or len(restricted_payload["protected_evidence"]) > MAX_PROTECTED_EVIDENCE_ROWS
    ):
        raise EvidencePacketError("evidence artifact bundle exceeds the fixed cohort bounds")
    current_tasks = load_dataset().eligible_tasks
    try:
        _verify_packet_boundary_fields(public_payload)
    except EvidencePacketError as exc:
        raise EvidencePacketError("reviewer packet bundle failed the blinded boundary") from exc
    for row in public_payload["candidate_artifacts"]:
        _verify_reviewer_artifact(row)
        packet = _parse_packet(row["packet"])
        if any(_has_sensitive_disclosure(text, task) for task in current_tasks for text in _packet_texts(packet)):
            raise EvidencePacketError("reviewer packet bundle contains protected task disclosure")
    for row in public_payload["decision_packets"]:
        packet = _parse_packet(row)
        if packet.kind is not ReviewKind.DECISION_CONFORMITY:
            raise EvidencePacketError("decision artifact contains the wrong review kind")
        if any(_has_sensitive_disclosure(text, task) for task in current_tasks for text in _packet_texts(packet)):
            raise EvidencePacketError("reviewer packet bundle contains protected task disclosure")
    protected_entries = restricted_payload["protected_evidence"]
    if not isinstance(protected_entries, list) or any(
        not isinstance(item, Mapping)
        or set(item) != _PROTECTED_EVIDENCE_ROW_FIELDS
        or not isinstance(item.get("content"), str)
        or len(item["content"]) > MAX_EXCERPT_CHARS
        for item in protected_entries
    ):
        raise EvidencePacketError("restricted evidence schema is malformed")
    non_evidence_restricted = {
        key: value for key, value in restricted_payload.items() if key != "protected_evidence"
    }
    restricted_text = json.dumps(non_evidence_restricted, ensure_ascii=False, sort_keys=True)
    if has_credential_like_text(restricted_text) or has_protected_audit_disclosure(restricted_text):
        raise EvidencePacketError("restricted crosswalk contains credential or protected audit text")
    if any(has_credential_like_text(item["content"]) for item in protected_entries):
        raise EvidencePacketError("restricted evidence contains credential-like text")
    public_bytes = _canonical(_with_integrity(public_payload))
    restricted_bytes = _canonical(_with_integrity(restricted_payload))
    if len(public_bytes) > MAX_ARTIFACT_BYTES or len(restricted_bytes) > MAX_ARTIFACT_BYTES:
        raise EvidencePacketError("evidence artifact exceeds the file size bound")
    created_paths: list[tuple[Path, int, int]] = []
    try:
        dev, ino = _write_new_private(public_path, public_bytes)
        created_paths.append((public_path, dev, ino))
        dev, ino = _write_new_private(restricted_path, restricted_bytes)
        created_paths.append((restricted_path, dev, ino))
    except FileExistsError as exc:
        for created_path, device, inode in created_paths:
            _unlink_if_owned(created_path, device, inode)
        raise EvidencePacketError("refusing to overwrite an existing evidence artifact") from exc
    except Exception:
        for created_path, device, inode in created_paths:
            _unlink_if_owned(created_path, device, inode)
        raise
    return public_path, restricted_path


@dataclass(frozen=True, slots=True)
class LoadedReviewArtifacts:
    candidate_packets: tuple[ReviewerEvidenceArtifact, ...]
    decision_packets: tuple[ReviewerPacket, ...]
    dataset_sha256: str
    catalog_mode: str

    @property
    def reviewer_packets(self) -> tuple[ReviewerPacket, ...]:
        return tuple(item.packet for item in self.candidate_packets) + self.decision_packets


def _read_private_file(path: Path, *, require_private_parent: bool = False) -> bytes:
    try:
        parent_info = path.parent.lstat()
    except OSError as exc:
        raise EvidencePacketError("cannot read evidence artifact") from exc
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or path.parent.is_symlink()
        or (require_private_parent and stat.S_IMODE(parent_info.st_mode) & 0o077)
    ):
        raise EvidencePacketError("evidence artifact access boundary is invalid")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise EvidencePacketError("cannot read evidence artifact") from exc
    try:
        file_info = os.fstat(descriptor)
        if not stat.S_ISREG(file_info.st_mode) or stat.S_IMODE(file_info.st_mode) & 0o077:
            raise EvidencePacketError("evidence artifact access boundary is invalid")
        if file_info.st_size > MAX_ARTIFACT_BYTES:
            raise EvidencePacketError("evidence artifact exceeds the file size bound")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, MAX_ARTIFACT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_ARTIFACT_BYTES:
                raise EvidencePacketError("evidence artifact exceeds the file size bound")
        return b"".join(chunks)
    except OSError as exc:
        raise EvidencePacketError("cannot read evidence artifact") from exc
    finally:
        os.close(descriptor)


def _load_json(path: Path, *, restricted: bool = False) -> dict[str, Any]:
    raw = _read_private_file(path, require_private_parent=restricted)
    try:
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidencePacketError("evidence artifact is malformed JSON") from exc
    if not isinstance(decoded, dict):
        raise EvidencePacketError("evidence artifact root must be a JSON object")
    _verify_integrity(decoded)
    return decoded


def load_review_packet_artifacts(
    path: str | Path,
    *,
    tasks: Sequence[EvaluationTask] | None = None,
) -> LoadedReviewArtifacts:
    """Load, verify and revalidate only reviewer-visible artifacts."""
    value = _load_json(Path(path))
    if set(value) != _BUNDLE_FIELDS or value.get("schema_version") != REVIEW_BUNDLE_SCHEMA:
        raise EvidencePacketError("review packet bundle schema is malformed or unsupported")
    if value.get("dataset_sha256") != DATASET_SHA256 or value.get("catalog_mode") not in {"offline", "mocked"}:
        raise EvidencePacketError("review packet bundle cohort or mode is invalid")
    if not isinstance(value.get("candidate_artifacts"), list) or not isinstance(value.get("decision_packets"), list):
        raise EvidencePacketError("review packet collections are malformed")
    if len(value["candidate_artifacts"]) > MAX_CANDIDATE_OCCURRENCES or len(value["decision_packets"]) > MAX_DECISION_PACKETS:
        raise EvidencePacketError("review packet collection exceeds the fixed cohort bounds")
    canonical_tasks = load_dataset().eligible_tasks
    task_values = tuple(canonical_tasks if tasks is None else tasks)
    if task_values != tuple(canonical_tasks):
        raise EvidencePacketError("review packet validation requires the unchanged eligible task dataset")
    task_by_id = {task.task_id: task for task in canonical_tasks}
    artifacts: list[ReviewerEvidenceArtifact] = []
    packets: list[ReviewerPacket] = []
    for row in value["candidate_artifacts"]:
        if not isinstance(row, Mapping):
            raise EvidencePacketError("candidate evidence artifact is malformed")
        _verify_reviewer_artifact(row)
        packet = _parse_packet(row["packet"])
        # A reviewer artifact never contains task_id. Validate against all task-specific protected data.
        for task in task_by_id.values():
            if any(_has_sensitive_disclosure(text, task) for text in _packet_texts(packet)):
                raise EvidencePacketError("reviewer artifact contains protected task disclosure")
        artifact = ReviewerEvidenceArtifact(
            schema_version=row["schema_version"],
            opaque_candidate_id=row["opaque_candidate_id"],
            observed_revision_ref=row["observed_revision_ref"],
            provenance=row["provenance"],
            retrieval_status=EvidenceStatus(row["retrieval_status"]),
            status_flags=tuple(row["status_flags"]),
            observed_at_utc=row["observed_at_utc"],
            retrieval_started_at_utc=row["retrieval_started_at_utc"],
            retrieval_completed_at_utc=row["retrieval_completed_at_utc"],
            retrieval_deadline_at_utc=row["retrieval_deadline_at_utc"],
            packet=packet,
        )
        artifacts.append(artifact)
        packets.append(packet)
    decision_packets: list[ReviewerPacket] = []
    for row in value["decision_packets"]:
        if not isinstance(row, Mapping):
            raise EvidencePacketError("decision reviewer packet is malformed")
        packet = _parse_packet(row)
        if packet.kind is not ReviewKind.DECISION_CONFORMITY:
            raise EvidencePacketError("decision packet has the wrong review kind")
        for task in task_by_id.values():
            if any(_has_sensitive_disclosure(text, task) for text in _packet_texts(packet)):
                raise EvidencePacketError("reviewer artifact contains protected task disclosure")
        decision_packets.append(packet)
        packets.append(packet)
    keys = [packet.packet_key for packet in packets]
    if len(keys) != len(set(keys)):
        raise EvidencePacketError("review packet bundle contains duplicate keys")
    try:
        prepare_review_order(packets, seed=0)
    except ValueError as exc:
        raise EvidencePacketError("review packet bundle failed blind-packet validation") from exc
    return LoadedReviewArtifacts(tuple(artifacts), tuple(decision_packets), value["dataset_sha256"], value["catalog_mode"])


def load_restricted_packet_crosswalk(
    path: str | Path,
    *,
    authorization_reference: str,
) -> dict[str, Any]:
    """Read evaluator linkage only with an explicit operator authorization reference."""
    if not isinstance(authorization_reference, str) or not authorization_reference.strip():
        raise EvidencePacketError("restricted crosswalk access requires an authorization reference")
    value = _load_json(Path(path), restricted=True)
    if set(value) != _CROSSWALK_FIELDS or value.get("schema_version") != RESTRICTED_CROSSWALK_SCHEMA:
        raise EvidencePacketError("restricted crosswalk schema is malformed or unsupported")
    if value.get("dataset_sha256") != DATASET_SHA256:
        raise EvidencePacketError("restricted crosswalk dataset hash is invalid")
    for key in ("candidate_entries", "decision_entries", "decision_gates", "protected_evidence"):
        if not isinstance(value.get(key), list) or any(not isinstance(item, dict) for item in value[key]):
            raise EvidencePacketError("restricted crosswalk collections are malformed")
    if (
        len(value["candidate_entries"]) > MAX_CANDIDATE_OCCURRENCES
        or len(value["decision_entries"]) > MAX_DECISION_PACKETS
        or len(value["decision_gates"]) > PLANNED_PAIR_COUNT
        or len(value["protected_evidence"]) > MAX_PROTECTED_EVIDENCE_ROWS
    ):
        raise EvidencePacketError("restricted crosswalk exceeds the fixed cohort bounds")
    task_ids = {task.task_id for task in load_dataset().eligible_tasks}
    planned = {
        f"p{plan.pair_number:03d}-t{plan.task_index:02d}-r{plan.repetition}": plan
        for plan in build_schedule(load_dataset().eligible_tasks)
    }
    candidate_keys: set[str] = set()
    candidate_identity: dict[str, tuple[Any, ...]] = {}
    candidate_occurrences: set[tuple[str, str, str]] = set()
    for item in value["candidate_entries"]:
        if set(item) != _CANDIDATE_CROSSWALK_ROW_FIELDS:
            raise EvidencePacketError("restricted candidate crosswalk row is malformed")
        if (
            not isinstance(item.get("opaque_candidate_id"), str)
            or not _OPAQUE_RE.fullmatch(item["opaque_candidate_id"])
            or item.get("task_id") not in task_ids
            or item.get("arm") not in {arm.value for arm in ArmName}
            or item.get("pair_id") not in planned
            or planned[item["pair_id"]].task_id != item.get("task_id")
            or planned[item["pair_id"]].repetition != item.get("repetition")
            or not isinstance(item.get("repetition"), int)
            or item["repetition"] not in {1, 2, 3}
            or not isinstance(item.get("rank"), int)
            or item["rank"] not in {1, 2, 3}
            or not isinstance(item.get("stable_id"), str)
            or not item["stable_id"]
        ):
            raise EvidencePacketError("restricted candidate crosswalk row is malformed")
        for text_value in (item.get("stable_id"), item.get("source_slug"), item.get("source_url"), item.get("arm_revision"), item.get("observed_revision")):
            if text_value is not None and (not isinstance(text_value, str) or has_credential_like_text(text_value) or has_protected_audit_disclosure(text_value)):
                raise EvidencePacketError("restricted candidate crosswalk contains prohibited text")
        for digest in (item.get("arm_content_hash"), item.get("observed_content_hash")):
            if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise EvidencePacketError("restricted candidate crosswalk hash is malformed")
        revision_ref = item.get("observed_revision_ref")
        if revision_ref is not None and (not isinstance(revision_ref, str) or not _OPAQUE_RE.fullmatch(revision_ref)):
            raise EvidencePacketError("restricted candidate revision reference is malformed")
        flags = item.get("evaluator_status_flags")
        if not isinstance(flags, list) or any(not isinstance(flag, str) or flag not in _STATUS_FLAGS for flag in flags):
            raise EvidencePacketError("restricted candidate status flags are malformed")
        if flags != sorted(set(flags)):
            raise EvidencePacketError("restricted candidate status flags are not canonical")
        if item.get("source_url") != _safe_source_url(item.get("source_url")):
            raise EvidencePacketError("restricted candidate URL is not normalized")
        identity = (item["task_id"], item["stable_id"], item.get("arm_content_hash"), item.get("arm_revision"))
        existing_identity = candidate_identity.setdefault(item["opaque_candidate_id"], identity)
        if existing_identity != identity:
            raise EvidencePacketError("one opaque candidate ID maps to conflicting source identities")
        occurrence_key = (item["pair_id"], item["arm"], str(item["rank"]))
        if occurrence_key in candidate_occurrences:
            raise EvidencePacketError("restricted crosswalk repeats a candidate occurrence")
        candidate_occurrences.add(occurrence_key)
        candidate_keys.add(item["opaque_candidate_id"])
    decision_keys: set[str] = set()
    decision_pairs: set[str] = set()
    for item in value["decision_entries"]:
        if set(item) != _DECISION_CROSSWALK_ROW_FIELDS:
            raise EvidencePacketError("restricted decision crosswalk row is malformed")
        key = item.get("opaque_decision_packet_key")
        if (
            not isinstance(key, str)
            or not _OPAQUE_RE.fullmatch(key)
            or key in candidate_keys
            or key in decision_keys
            or item.get("task_id") not in task_ids
            or item.get("pair_id") not in planned
            or planned[item["pair_id"]].task_id != item.get("task_id")
            or planned[item["pair_id"]].repetition != item.get("repetition")
            or item.get("arm") != ArmName.SKILLSCOUT.value
            or item.get("pair_id") in decision_pairs
            or not isinstance(item.get("repetition"), int)
        ):
            raise EvidencePacketError("restricted decision crosswalk row is malformed")
        decision_keys.add(key)
        decision_pairs.add(item["pair_id"])
    for item in value["decision_gates"]:
        if (
            set(item) != _DECISION_GATE_ROW_FIELDS
            or item.get("task_id") not in task_ids
            or item.get("pair_id") not in planned
            or planned[item["pair_id"]].task_id != item.get("task_id")
            or planned[item["pair_id"]].repetition != item.get("repetition", planned[item["pair_id"]].repetition)
        ):
            raise EvidencePacketError("restricted decision gate row is malformed")
        if item.get("status") not in {
            "indeterminate_protected_evidence_withheld",
            "indeterminate_decision_criteria_unapproved",
            "indeterminate_no_valid_decision",
            "indeterminate_context_incomplete",
        }:
            raise EvidencePacketError("restricted decision gate status is malformed")
    for item in value["protected_evidence"]:
        if set(item) != _PROTECTED_EVIDENCE_ROW_FIELDS:
            raise EvidencePacketError("restricted evidence row is malformed")
        if (
            item.get("opaque_candidate_id") not in candidate_keys
            or item.get("task_id") not in task_ids
            or item.get("pair_id") not in planned
            or planned[item["pair_id"]].task_id != item.get("task_id")
            or planned[item["pair_id"]].repetition != item.get("repetition")
            or item.get("arm") not in {arm.value for arm in ArmName}
            or not isinstance(item.get("rank"), int)
            or item.get("rank") not in {1, 2, 3}
            or item.get("evidence_type") not in _PROTECTED_EVIDENCE_TYPES
            or item.get("provenance") not in _PROVENANCE
            or not isinstance(item.get("content"), str)
            or len(item["content"]) > MAX_EXCERPT_CHARS
            or has_credential_like_text(item["content"])
            or not _utc_is_valid(item.get("observed_at_utc"))
            or not _utc_is_valid(item.get("retrieved_at_utc"))
        ):
            raise EvidencePacketError("restricted evidence row is malformed")
    return {key: value[key] for key in ("candidate_entries", "decision_entries", "decision_gates", "protected_evidence")}


def load_review_ledger(
    packets: ReviewPacketBundle | LoadedReviewArtifacts,
    packet_event_path: str | Path,
) -> ReviewLedger:
    """Reload and validate a private append-only event history against reviewer packets."""
    raw = _read_private_file(Path(packet_event_path), require_private_parent=True)
    records: list[dict[str, Any]] = []
    allowed_fields = {
        "event_id", "packet_key", "kind", "phase", "reviewer_pseudonym", "action",
        "assessable", "rationale", "evidence_basis", "blinding_compromised", "source_recognized",
        "unavoidable_exposure", "timestamp_utc", "replaces_event_id", "packet_assessment_allowed",
    }
    legacy_fields = allowed_fields - {"packet_assessment_allowed"}
    try:
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line, object_pairs_hook=_no_duplicate_keys)
            if not isinstance(item, dict) or frozenset(item) not in {frozenset(allowed_fields), frozenset(legacy_fields)}:
                raise EvidencePacketError("review ledger event schema is malformed")
            if has_credential_like_text(item.get("rationale", "")) or has_protected_audit_disclosure(item.get("rationale", "")):
                raise EvidencePacketError("review ledger contains protected rationale text")
            records.append(item)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidencePacketError("review ledger is malformed JSONL") from exc
    try:
        ledger = ReviewLedger.from_records(records)
    except ValueError as exc:
        raise EvidencePacketError("review ledger history is invalid") from exc
    packet_by_key = {packet.packet_key: packet for packet in packets.reviewer_packets}
    for event in ledger.events:
        packet = packet_by_key.get(event.packet_key)
        if packet is None or packet.kind is not event.kind or packet.assessment_allowed != event.packet_assessment_allowed:
            raise EvidencePacketError("review ledger event does not match a packet in this bundle")
        if not packet.assessment_allowed and (
            event.assessable or event.action not in {"unknown", "unassessable", "unresolved"}
        ):
            raise EvidencePacketError("review ledger exceeds the packet evidence gate")
    return ledger


def _validate_packet_bundle_matches_run(
    loaded: LoadedReviewArtifacts,
    restricted: Mapping[str, Any],
    pairs: Sequence[Mapping[str, Any]],
) -> None:
    expected: dict[tuple[str, str, int], tuple[Any, ...]] = {}
    pair_ids: set[str] = set()
    for pair in pairs:
        pair_id = pair.get("pair_id")
        task_id = pair.get("task_id")
        repetition = pair.get("repetition")
        if not isinstance(pair_id, str) or not isinstance(task_id, str):
            raise EvidencePacketError("persisted pair identity is malformed")
        pair_ids.add(pair_id)
        for arm, arm_record in pair.get("arms", {}).items():
            for slot in arm_record.get("slots", ()):
                if slot.get("status") != SlotStatus.OCCUPIED.value:
                    continue
                occurrence = (pair_id, str(arm), slot.get("rank"))
                expected[occurrence] = (
                    slot.get("opaque_candidate_key"), task_id, repetition, slot.get("stable_id"),
                    slot.get("arm_content_hash"), slot.get("arm_revision"),
                )
    observed: dict[tuple[str, str, int], tuple[Any, ...]] = {}
    for entry in restricted.get("candidate_entries", ()):
        occurrence = (entry.get("pair_id"), entry.get("arm"), entry.get("rank"))
        observed[occurrence] = (
            entry.get("opaque_candidate_id"), entry.get("task_id"), entry.get("repetition"),
            entry.get("stable_id"), entry.get("arm_content_hash"), entry.get("arm_revision"),
        )
    if observed != expected:
        raise EvidencePacketError("review candidate crosswalk does not match the persisted evaluation run")
    expected_candidate_keys = {item[0] for item in expected.values()}
    artifact_candidate_keys = {item.opaque_candidate_id for item in loaded.candidate_packets}
    if None in expected_candidate_keys or expected_candidate_keys != artifact_candidate_keys:
        raise EvidencePacketError("review candidate artifacts do not match the persisted run candidates")

    decision_packets = {packet.packet_key for packet in loaded.decision_packets}
    decision_entries = restricted.get("decision_entries", ())
    mapped_pairs: set[str] = set()
    mapped_keys: set[str] = set()
    for entry in decision_entries:
        pair_id = entry.get("pair_id")
        pair = next((item for item in pairs if item.get("pair_id") == pair_id), None)
        key = entry.get("opaque_decision_packet_key")
        if (
            pair is None
            or pair.get("task_id") != entry.get("task_id")
            or pair.get("repetition") != entry.get("repetition")
            or entry.get("arm") != ArmName.SKILLSCOUT.value
            or pair_id in mapped_pairs
            or key in mapped_keys
            or key not in decision_packets
        ):
            raise EvidencePacketError("review decision crosswalk does not match its packet bundle or run")
        mapped_pairs.add(pair_id)
        mapped_keys.add(key)
    gated_pairs = {item.get("pair_id") for item in restricted.get("decision_gates", ())}
    if (
        mapped_keys != decision_packets
        or mapped_pairs.intersection(gated_pairs)
        or mapped_pairs.union(gated_pairs) != pair_ids
    ):
        raise EvidencePacketError("review decision packets and indeterminate gates do not cover the fixed schedule")


def apply_persisted_reviews_and_derive_metrics(
    run_directory: str | Path,
    *,
    reviewer_artifact_path: str | Path,
    restricted_crosswalk_path: str | Path,
    packet_event_path: str | Path,
    authorization_reference: str,
) -> dict[str, Any]:
    """Join persisted blinded reviews to a validated run and derive protocol metrics."""
    from skillscout.evaluation.store import RunStoreError, read_run_bundle

    try:
        run = read_run_bundle(run_directory)
    except (OSError, RunStoreError) as exc:
        raise EvidencePacketError("cannot load a validated offline evaluation run") from exc
    tasks = load_dataset().eligible_tasks
    errors = validate_records(run.pairs, tasks, dataset_sha256=run.manifest.get("dataset_sha256", ""))
    if errors:
        raise EvidencePacketError("persisted run records failed fixed-schedule validation")
    loaded = load_review_packet_artifacts(reviewer_artifact_path, tasks=tasks)
    restricted = load_restricted_packet_crosswalk(
        restricted_crosswalk_path,
        authorization_reference=authorization_reference,
    )
    _validate_packet_bundle_matches_run(loaded, restricted, run.pairs)
    ledger = load_review_ledger(loaded, packet_event_path)
    bundle = ReviewPacketBundle(
        candidate_artifacts=loaded.candidate_packets,
        decision_packets=loaded.decision_packets,
        candidate_crosswalk=tuple(restricted["candidate_entries"]),
        decision_crosswalk=tuple(restricted["decision_entries"]),
        decision_gates=tuple(restricted["decision_gates"]),
        protected_evidence=tuple(restricted["protected_evidence"]),
        catalog_mode=loaded.catalog_mode,
        dataset_sha256=loaded.dataset_sha256,
    )
    return apply_reviews_and_derive_metrics(
        bundle,
        run.pairs,
        tasks,
        ledger,
        task_gates_approved=run.manifest.get("decision_gates_approved_by_task") or False,
    )


def persist_review_ledger(
    bundle: ReviewPacketBundle | LoadedReviewArtifacts,
    ledger: ReviewLedger,
    packet_event_path: str | Path,
) -> None:
    """Persist only events tied to packet keys in this bundle, retaining initial judgments."""
    allowed = {packet.packet_key for packet in bundle.reviewer_packets}
    if any(event.packet_key not in allowed for event in ledger.events):
        raise EvidencePacketError("review ledger contains an event outside this packet bundle")
    try:
        ReviewLedger.from_records(ledger.events)
    except ValueError as exc:
        raise EvidencePacketError("review ledger history is invalid") from exc
    for event in ledger.events:
        if has_credential_like_text(event.rationale) or has_protected_audit_disclosure(event.rationale):
            raise EvidencePacketError("review event contains protected text")
    for packet in bundle.reviewer_packets:
        for event in ledger.events:
            if event.packet_key == packet.packet_key and not packet.assessment_allowed:
                if event.assessable or event.action not in {"unknown", "unassessable", "unresolved"}:
                    raise EvidencePacketError("review ledger exceeds the packet evidence gate")
    event_path = Path(packet_event_path)
    _ensure_private_parent(event_path)
    payload = b"".join(_canonical(event.to_dict()) for event in ledger.events)
    try:
        _write_new_private(event_path, payload)
    except FileExistsError as exc:
        raise EvidencePacketError("refusing to overwrite existing review ledger") from exc

