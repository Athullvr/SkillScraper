"""Blinded review packet construction and append-only independent judgments.

Packets are built from explicit allow-lists. The restricted evaluator crosswalk is
never accepted by the packet builders, so arm, rank, score, expected labels, and
protected audit fields cannot leak through an accidental whole-record dump.
"""

from __future__ import annotations

import json
import os
import random
import re
import stat
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from skillscout.evaluation.fixtures import has_credential_like_text, has_protected_audit_disclosure, redact_credential_like_text
from skillscout.evaluation.models import (
    ArmStatus,
    Decision,
    DecisionConformity,
    RelevanceLabel,
    ReviewResolution,
)
from skillscout.evaluation.protocol import ArmName, EvaluationTask, criteria_for_candidate_review


class ReviewError(ValueError):
    """A review packet or judgment violates the blinded review contract."""


class ReviewKind(StrEnum):
    CANDIDATE_RELEVANCE = "candidate_relevance"
    DECISION_CONFORMITY = "decision_conformity"


class ReviewPhase(StrEnum):
    INITIAL = "initial"
    REPLACEMENT = "replacement"
    ADJUDICATOR_INITIAL = "adjudicator_initial"
    ADJUDICATION = "adjudication"


@dataclass(frozen=True, slots=True)
class EvidenceExcerpt:
    """Candidate/task evidence allowed in a reviewer packet after sanitization."""

    content: str
    evidence_type: str = "candidate_detail"
    provenance: str = "retrospective_evaluator_evidence"
    relevance_sufficient: bool | None = None
    identity_linked: bool | None = None
    revision_linked: bool | None = None
    retrieval_status: str = "complete"
    candidate_ref: str | None = None
    observed_revision_ref: str | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ReviewerPacket:
    packet_key: str
    kind: ReviewKind
    task_text: str
    criteria: tuple[str, ...]
    approved_context: str
    evidence: tuple[EvidenceExcerpt, ...]
    decision_options: tuple[str, ...] = ()
    schema_version: str = "skillscout-review-packet-v2"
    evidence_status: str = "complete"
    assessment_allowed: bool = True
    context_status: str = "ready"
    context_reasons: tuple[str, ...] = ()
    evidence_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_key": self.packet_key,
            "kind": self.kind.value,
            "task_text": self.task_text,
            "criteria": list(self.criteria),
            "approved_context": self.approved_context,
            "evidence": [asdict(item) for item in self.evidence],
            "decision_options": list(self.decision_options),
            "schema_version": self.schema_version,
            "evidence_status": self.evidence_status,
            "assessment_allowed": self.assessment_allowed,
            "context_status": self.context_status,
            "context_reasons": list(self.context_reasons),
            "evidence_reasons": list(self.evidence_reasons),
        }


@dataclass(frozen=True, slots=True)
class ReviewOrder:
    seed: int
    packet_keys: tuple[str, ...]
    packets: tuple[ReviewerPacket, ...]


_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_PROTECTED_FIELD_TEXT = re.compile(
    r"(?i)\b(?:arm|system[_ -]?identity|expected[_ -]?(?:decision|label|outcome)|"
    r"human[_ -]?relevance[_ -]?judgment|task[_ -]?(?:id|category)|rank|score|scores|"
    r"popularity|install[_ -]?count|safety(?:[_ -]?(?:status|finding|result|verdict))?|"
    r"audit(?:[_ -]?(?:status|finding|result|verdict))?|scanner(?:[_ -]?(?:status|finding|result|verdict)))\b\s*[:=]"
)
_PROTECTED_FINDING_TEXT = re.compile(r"(?i)\b(?:protected\s+)?(?:safety|audit|scanner)\s+(?:finding|status|result|verdict)\b")
_SYSTEM_IDENTITY_TEXT = re.compile(r"(?i)\b(?:SkillScout(?![-_][A-Za-z0-9])|ordinary[ _-]+search|skills\.sh)\b")
_RANKING_METADATA_TEXT = re.compile(
    r"(?i)\b(?:rank(?:ed|ing)?|result\s+position|score|popularity|install(?:ation)?\s+count|"
    r"download\s+count|search\s+order)\b(?:\s*(?:was|is|of|=|:|#)\s*[^\s,.;]+)?"
)
_FORBIDDEN_KEYS = {
    "arm", "arm_name", "system", "system_identity", "decision", "original_decision",
    "expected_decision", "expected_label", "category", "task_category", "rank",
    "score", "scores", "popularity", "install_count", "safety", "safety_finding",
    "audit", "audit_status", "scanner", "scanner_finding", "candidate_id",
    "stable_id", "source_url", "task_id", "fixture_description", "crosswalk",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _assert_no_protected_disclosure(text: str) -> None:
    if has_protected_audit_disclosure(text):
        raise ReviewError("protected audit-state disclosure is not allowed in reviewer packets")


def _mask_identifiers(text: str, identifiers: Iterable[str]) -> str:
    result = redact_credential_like_text(text)
    for identifier in sorted({value for value in identifiers if value}, key=len, reverse=True):
        result = re.sub(re.escape(identifier), "[candidate identifier masked]", result, flags=re.IGNORECASE)
    result = _URL.sub("[source URL masked]", result)
    _assert_no_protected_disclosure(result)
    return result


def _task_private_values(task: EvaluationTask) -> tuple[str, ...]:
    values: set[str] = {task.task_id, task.category, task.expected_decision}
    judgment = task.human_relevance_judgment

    def visit(value: Any, key: str = "") -> None:
        if key == "criteria":
            return
        if isinstance(value, Mapping):
            for nested_key, nested in value.items():
                visit(nested, str(nested_key))
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested, key)
        elif isinstance(value, str) and len(value.strip()) >= 3:
            values.add(value.strip())

    visit(judgment)
    return tuple(sorted((value for value in values if value), key=len, reverse=True))


def _assert_task_private_values_hidden(packet: ReviewerPacket, task: EvaluationTask) -> None:
    texts = (
        packet.task_text,
        packet.approved_context,
        *packet.criteria,
        *packet.context_reasons,
        *packet.evidence_reasons,
        *(item.content for item in packet.evidence),
    )
    for private_value in _task_private_values(task):
        if any(private_value.casefold() in text.casefold() for text in texts):
            raise ReviewError("reviewer packet contains evaluator-only task data")


def _allowed_evidence(
    items: Iterable[EvidenceExcerpt],
    *,
    identifiers_to_mask: Iterable[str] = (),
) -> tuple[EvidenceExcerpt, ...]:
    sanitized: list[EvidenceExcerpt] = []
    source_items = tuple(items)
    if len(source_items) > 8:
        raise ReviewError("reviewer packet contains too many evidence excerpts")
    for item in source_items:
        if not isinstance(item, EvidenceExcerpt):
            raise ReviewError("evidence must use the explicit EvidenceExcerpt schema")
        if not isinstance(item.evidence_type, str) or not isinstance(item.content, str):
            raise ReviewError("evidence text fields are malformed")
        evidence_type = item.evidence_type.casefold()
        if any(marker in evidence_type for marker in ("audit", "safety", "scanner", "protected")):
            continue
        if evidence_type not in {"candidate_detail", "candidate_readme", "compatibility", "integration", "task_context", "other"}:
            raise ReviewError("unsupported evidence type")
        if not isinstance(item.provenance, str) or item.provenance not in {"decision_time_arm_evidence", "retrospective_evaluator_evidence", "task_context"}:
            raise ReviewError("unsupported evidence provenance")
        if any(value is not None and not isinstance(value, bool) for value in (
            item.relevance_sufficient, item.identity_linked, item.revision_linked
        )) or not isinstance(item.truncated, bool):
            raise ReviewError("evidence linkage fields are malformed")
        content = _mask_identifiers(item.content, identifiers_to_mask)
        if len(content) > 2000:
            content = content[:2000]
        retrieval_status = item.retrieval_status
        if not isinstance(retrieval_status, str) or retrieval_status not in {"complete", "partial", "missing", "stale", "mismatched", "late", "unverified", "failed"}:
            raise ReviewError("unsupported evidence retrieval status")
        if item.candidate_ref is not None and (not isinstance(item.candidate_ref, str) or not _is_opaque_reference(item.candidate_ref)):
            raise ReviewError("candidate reference must be opaque")
        if item.observed_revision_ref is not None and (not isinstance(item.observed_revision_ref, str) or not _is_opaque_reference(item.observed_revision_ref)):
            raise ReviewError("revision reference must be opaque")
        sanitized.append(
            EvidenceExcerpt(
                content=content,
                evidence_type=evidence_type,
                provenance=item.provenance,
                relevance_sufficient=item.relevance_sufficient,
                identity_linked=item.identity_linked,
                revision_linked=item.revision_linked,
                retrieval_status=retrieval_status,
                candidate_ref=item.candidate_ref,
                observed_revision_ref=item.observed_revision_ref,
                truncated=item.truncated or len(item.content) > 2000,
            )
        )
    if sum(len(item.content) for item in sanitized) > 8000:
        raise ReviewError("reviewer packet evidence exceeds the total text limit")
    return tuple(sanitized)


def _is_opaque_reference(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value))


def _verify_packet_boundary(packet: ReviewerPacket) -> None:
    serialized = packet.to_dict()
    if packet.schema_version != "skillscout-review-packet-v2":
        raise ReviewError("unsupported reviewer packet schema")
    if packet.evidence_status not in {"complete", "partial", "missing", "stale", "mismatched", "late", "unverified", "failed"}:
        raise ReviewError("unsupported packet evidence status")
    if not isinstance(packet.assessment_allowed, bool):
        raise ReviewError("packet assessability gate must be boolean")
    if packet.context_status not in {"ready", "incomplete_by_protocol", "unverified"}:
        raise ReviewError("unsupported packet context status")
    if not all(isinstance(item, str) for item in packet.context_reasons):
        raise ReviewError("packet context reasons must be text")
    if not all(isinstance(item, str) for item in packet.evidence_reasons):
        raise ReviewError("packet evidence reasons must be text")
    if packet.context_status == "ready" and packet.context_reasons:
        raise ReviewError("ready packet context cannot contain blocking reasons")
    if packet.assessment_allowed and packet.evidence_status not in {"complete", "partial"}:
        raise ReviewError("packet assessability conflicts with its evidence status")
    if packet.assessment_allowed and packet.context_status != "ready":
        raise ReviewError("incomplete or unverified task context cannot pass the packet assessability gate")
    if packet.assessment_allowed and any(item.truncated for item in packet.evidence):
        raise ReviewError("truncated evidence cannot pass the packet assessability gate")

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.casefold() in _FORBIDDEN_KEYS:
                    raise ReviewError("reviewer packet contains a prohibited field")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            _assert_no_protected_disclosure(value)
            if (
                has_credential_like_text(value)
                or _URL.search(value)
                or _PROTECTED_FIELD_TEXT.search(value)
                or _PROTECTED_FINDING_TEXT.search(value)
                or _SYSTEM_IDENTITY_TEXT.search(value)
                or _RANKING_METADATA_TEXT.search(value)
            ):
                raise ReviewError("protected metadata or a source URL is not allowed in a reviewer packet")
            if len(value) > 12000:
                raise ReviewError("reviewer packet text exceeds the schema limit")
    visit(serialized)


def build_candidate_relevance_packet(
    task: EvaluationTask,
    *,
    approved_context: str,
    evidence: Iterable[EvidenceExcerpt],
    packet_key_factory: Callable[[], str] | None = None,
    identifiers_to_mask: Iterable[str] = (),
    evidence_status: str = "complete",
    assessment_allowed: bool = True,
    context_status: str = "ready",
    context_reasons: Iterable[str] = (),
    evidence_reasons: Iterable[str] = (),
) -> ReviewerPacket:
    """Build a candidate-only packet with criteria but no evaluator labels."""
    packet_key = (packet_key_factory or (lambda: str(uuid.uuid4())))()
    if not isinstance(packet_key, str) or not packet_key.strip() or task.task_id.casefold() in packet_key.casefold():
        raise ReviewError("review packet key must be non-empty and must not disclose the task ID")
    identifiers_to_mask = (*identifiers_to_mask, task.task_id)
    packet_evidence = _allowed_evidence(evidence, identifiers_to_mask=identifiers_to_mask)
    if any(item.truncated for item in packet_evidence):
        assessment_allowed = False
        if evidence_status == "complete":
            evidence_status = "partial"
    packet = ReviewerPacket(
        packet_key=packet_key,
        kind=ReviewKind.CANDIDATE_RELEVANCE,
        task_text=_mask_identifiers(task.task_text, identifiers_to_mask),
        criteria=tuple(
            _mask_identifiers(item, identifiers_to_mask)
            for item in criteria_for_candidate_review(task)
        ),
        approved_context=_mask_identifiers(approved_context, identifiers_to_mask),
        evidence=packet_evidence,
        evidence_status=evidence_status,
        assessment_allowed=assessment_allowed,
        context_status=context_status,
        context_reasons=tuple(context_reasons),
        evidence_reasons=tuple(evidence_reasons),
    )
    _assert_task_private_values_hidden(packet, task)
    _verify_packet_boundary(packet)
    return packet


def build_decision_conformity_packet(
    task: EvaluationTask,
    *,
    approved_context: str,
    evidence: Iterable[EvidenceExcerpt],
    packet_key_factory: Callable[[], str] | None = None,
    identifiers_to_mask: Iterable[str] = (),
    decision_criteria: Iterable[str] = (),
    evidence_status: str = "complete",
    assessment_allowed: bool = True,
    context_status: str = "ready",
    context_reasons: Iterable[str] = (),
    evidence_reasons: Iterable[str] = (),
) -> ReviewerPacket:
    """Build a task/action assessment packet without the original action or labels."""
    packet_key = (packet_key_factory or (lambda: str(uuid.uuid4())))()
    if not isinstance(packet_key, str) or not packet_key.strip() or task.task_id.casefold() in packet_key.casefold():
        raise ReviewError("review packet key must be non-empty and must not disclose the task ID")
    identifiers_to_mask = (*identifiers_to_mask, task.task_id)
    packet_evidence = _allowed_evidence(evidence, identifiers_to_mask=identifiers_to_mask)
    if any(item.truncated for item in packet_evidence):
        assessment_allowed = False
        if evidence_status == "complete":
            evidence_status = "partial"
    packet = ReviewerPacket(
        packet_key=packet_key,
        kind=ReviewKind.DECISION_CONFORMITY,
        task_text=_mask_identifiers(task.task_text, identifiers_to_mask),
        criteria=tuple(_mask_identifiers(item, identifiers_to_mask) for item in decision_criteria),
        approved_context=_mask_identifiers(approved_context, identifiers_to_mask),
        evidence=packet_evidence,
        decision_options=tuple(decision.value for decision in Decision),
        evidence_status=evidence_status,
        assessment_allowed=assessment_allowed,
        context_status=context_status,
        context_reasons=tuple(context_reasons),
        evidence_reasons=tuple(evidence_reasons),
    )
    _assert_task_private_values_hidden(packet, task)
    _verify_packet_boundary(packet)
    return packet


def shuffle_packets(packets: Iterable[ReviewerPacket], *, seed: int) -> tuple[ReviewerPacket, ...]:
    """Return a reproducible, recorded-seed review order without exposing the crosswalk."""
    return prepare_review_order(packets, seed=seed).packets


def prepare_review_order(packets: Iterable[ReviewerPacket], *, seed: int) -> ReviewOrder:
    values = list(packets)
    keys = [packet.packet_key for packet in values]
    if len(set(keys)) != len(keys):
        raise ReviewError("review packet keys must be unique within a shuffled review batch")
    random.Random(seed).shuffle(values)
    return ReviewOrder(seed=seed, packet_keys=tuple(item.packet_key for item in values), packets=tuple(values))


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    event_id: str
    packet_key: str
    kind: ReviewKind
    phase: ReviewPhase
    reviewer_pseudonym: str
    action: str
    assessable: bool
    rationale: str
    evidence_basis: str
    blinding_compromised: bool
    source_recognized: bool
    unavoidable_exposure: tuple[str, ...]
    timestamp_utc: str
    replaces_event_id: str | None = None
    packet_assessment_allowed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "kind": self.kind.value,
            "phase": self.phase.value,
            "unavoidable_exposure": list(self.unavoidable_exposure),
        }


class ReviewLedger:
    """Append-only review workflow; initial judgments are never overwritten."""

    def __init__(self, *, clock: Callable[[], str] = _utc_now) -> None:
        self._events: list[ReviewEvent] = []
        self._clock = clock

    @property
    def events(self) -> tuple[ReviewEvent, ...]:
        return tuple(self._events)

    @classmethod
    def from_records(cls, records: Iterable[ReviewEvent | Mapping[str, Any]]) -> ReviewLedger:
        """Rehydrate and validate persisted append-only reviewer events."""
        ledger = cls()
        events: list[ReviewEvent] = []
        for record in records:
            if isinstance(record, ReviewEvent):
                event = record
            else:
                try:
                    event = ReviewEvent(
                        event_id=str(record["event_id"]),
                        packet_key=str(record["packet_key"]),
                        kind=ReviewKind(record["kind"]),
                        phase=ReviewPhase(record["phase"]),
                        reviewer_pseudonym=str(record["reviewer_pseudonym"]),
                        action=str(record["action"]),
                        assessable=record["assessable"],
                        rationale=str(record["rationale"]),
                        evidence_basis=str(record["evidence_basis"]),
                        blinding_compromised=record["blinding_compromised"],
                        source_recognized=record["source_recognized"],
                        unavoidable_exposure=tuple(record["unavoidable_exposure"]),
                        timestamp_utc=str(record["timestamp_utc"]),
                        replaces_event_id=record.get("replaces_event_id"),
                        packet_assessment_allowed=record.get("packet_assessment_allowed", True),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ReviewError("persisted review event is malformed") from exc
            events.append(event)
        ledger._events = events
        ledger._validate_history()
        return ledger

    def _validate_history(self) -> None:
        event_ids: set[str] = set()
        for index, event in enumerate(self._events):
            if not event.event_id or event.event_id in event_ids or not event.packet_key:
                raise ReviewError("review event IDs and packet keys must be unique and non-empty")
            event_ids.add(event.event_id)
            if not isinstance(event.assessable, bool) or not isinstance(event.blinding_compromised, bool):
                raise ReviewError("review assessability and blinding fields must be booleans")
            if not isinstance(event.packet_assessment_allowed, bool):
                raise ReviewError("persisted packet evidence gate is malformed")
            if not isinstance(event.source_recognized, bool) or not event.reviewer_pseudonym.strip():
                raise ReviewError("review recognition and reviewer fields are malformed")
            if not event.rationale.strip() or event.evidence_basis not in {"decision_time", "retrospective", "mixed", "insufficient"}:
                raise ReviewError("review rationale or evidence provenance is malformed")
            _assert_no_protected_disclosure(event.rationale)
            if any(not isinstance(value, str) for value in event.unavoidable_exposure):
                raise ReviewError("reviewer exposure entries must be text")
            for value in event.unavoidable_exposure:
                _assert_no_protected_disclosure(value)
            try:
                timestamp = datetime.fromisoformat(event.timestamp_utc.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ReviewError("review event timestamp is not valid ISO-8601") from exc
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ReviewError("review event timestamp must include a UTC offset")
            earlier = self._events[:index]
            same_packet = [item for item in earlier if item.packet_key == event.packet_key]
            if any(item.kind is not event.kind for item in same_packet):
                raise ReviewError("a packet key cannot mix candidate and decision review kinds")
            if not _valid_action(event.kind, event.action, event.assessable, event.phase):
                raise ReviewError("persisted review event has an invalid action/assessability pair")
            if not event.packet_assessment_allowed and (
                event.assessable or event.action not in {"unknown", "unassessable", "unresolved"}
            ):
                raise ReviewError("persisted assessment exceeds the packet evidence gate")
            if event.phase in {ReviewPhase.INITIAL, ReviewPhase.REPLACEMENT, ReviewPhase.ADJUDICATOR_INITIAL}:
                if any(item.reviewer_pseudonym == event.reviewer_pseudonym for item in same_packet):
                    raise ReviewError("persisted independent assessments reuse a reviewer")
            if event.phase is ReviewPhase.INITIAL:
                if sum(item.phase is ReviewPhase.INITIAL for item in same_packet) >= 2 or any(
                    item.phase in {ReviewPhase.ADJUDICATOR_INITIAL, ReviewPhase.ADJUDICATION}
                    for item in same_packet
                ):
                    raise ReviewError("persisted initial review order is invalid")
            elif event.phase is ReviewPhase.REPLACEMENT:
                source = next((item for item in same_packet if item.event_id == event.replaces_event_id), None)
                if (
                    source is None
                    or source.phase not in {ReviewPhase.INITIAL, ReviewPhase.ADJUDICATOR_INITIAL}
                    or not source.blinding_compromised
                    or any(item.replaces_event_id == source.event_id for item in same_packet)
                ):
                    raise ReviewError("persisted replacement does not repair one compromised assessment")
                if source.phase is ReviewPhase.INITIAL and any(
                    item.phase in {ReviewPhase.ADJUDICATOR_INITIAL, ReviewPhase.ADJUDICATION}
                    for item in same_packet
                ):
                    raise ReviewError("initial review replacement occurred after adjudication began")
                if source.phase is ReviewPhase.ADJUDICATOR_INITIAL and any(
                    item.phase is ReviewPhase.ADJUDICATION for item in same_packet
                ):
                    raise ReviewError("adjudicator replacement occurred after adjudication was locked")
            elif event.phase is ReviewPhase.ADJUDICATOR_INITIAL:
                prior_ledger = ReviewLedger()
                prior_ledger._events = earlier
                initial = prior_ledger._active_initial(event.packet_key)
                if (
                    len(initial) != 2
                    or initial[0].action == initial[1].action
                    or any(item.phase is ReviewPhase.ADJUDICATOR_INITIAL for item in same_packet)
                ):
                    raise ReviewError("adjudicator initial assessment requires two disagreeing locked reviews")
            else:
                prior_ledger = ReviewLedger()
                prior_ledger._events = earlier
                initial = prior_ledger._active_initial(event.packet_key)
                adjudicator = prior_ledger._active_adjudicator_initial(event.packet_key)
                if (
                    len(initial) != 2
                    or initial[0].action == initial[1].action
                    or adjudicator is None
                    or adjudicator.reviewer_pseudonym != event.reviewer_pseudonym
                    or any(item.phase is ReviewPhase.ADJUDICATION for item in same_packet)
                ):
                    raise ReviewError("persisted adjudication order or reviewer is invalid")

    def lock_initial(
        self,
        packet: ReviewerPacket,
        *,
        reviewer_pseudonym: str,
        action: str,
        assessable: bool,
        rationale: str,
        evidence_basis: str,
        blinding_compromised: bool = False,
        source_recognized: bool = False,
        unavoidable_exposure: tuple[str, ...] = (),
    ) -> ReviewEvent:
        return self._append(
            packet,
            reviewer_pseudonym=reviewer_pseudonym,
            action=action,
            assessable=assessable,
            rationale=rationale,
            evidence_basis=evidence_basis,
            phase=ReviewPhase.INITIAL,
            blinding_compromised=blinding_compromised,
            source_recognized=source_recognized,
            unavoidable_exposure=unavoidable_exposure,
        )

    def lock_adjudicator_initial(self, packet: ReviewerPacket, **kwargs: Any) -> ReviewEvent:
        initial = self._active_initial(packet.packet_key)
        if len(initial) != 2 or initial[0].action == initial[1].action:
            raise ReviewError("adjudicator initial review requires two disagreeing locked assessments")
        return self._append(packet, phase=ReviewPhase.ADJUDICATOR_INITIAL, **kwargs)

    def lock_replacement(self, packet: ReviewerPacket, *, replaces_event_id: str, **kwargs: Any) -> ReviewEvent:
        source = next((event for event in self._events if event.event_id == replaces_event_id), None)
        if (
            source is None
            or source.packet_key != packet.packet_key
            or source.phase not in {ReviewPhase.INITIAL, ReviewPhase.ADJUDICATOR_INITIAL}
        ):
            raise ReviewError("replacement must reference an initial or adjudicator assessment for this packet")
        if source.phase is ReviewPhase.INITIAL and any(
            event.packet_key == packet.packet_key
            and event.phase in {ReviewPhase.ADJUDICATOR_INITIAL, ReviewPhase.ADJUDICATION}
            for event in self._events
        ):
            raise ReviewError("replace a compromised initial assessment before adjudication begins")
        if source.phase is ReviewPhase.ADJUDICATOR_INITIAL and any(
            event.packet_key == packet.packet_key and event.phase is ReviewPhase.ADJUDICATION
            for event in self._events
        ):
            raise ReviewError("replace a compromised adjudicator assessment before adjudication is locked")
        if not source.blinding_compromised:
            raise ReviewError("only a compromised initial assessment can be replaced")
        if any(event.replaces_event_id == replaces_event_id for event in self._events):
            raise ReviewError("a compromised initial assessment can have only one replacement")
        return self._append(
            packet,
            phase=ReviewPhase.REPLACEMENT,
            replaces_event_id=replaces_event_id,
            **kwargs,
        )

    def append_adjudication(self, packet: ReviewerPacket, **kwargs: Any) -> ReviewEvent:
        independent = self._active_initial(packet.packet_key)
        adjudicator_initial = self._active_adjudicator_initial(packet.packet_key)
        if (
            len(independent) != 2
            or independent[0].action == independent[1].action
            or adjudicator_initial is None
        ):
            raise ReviewError("adjudication requires two disagreeing reviews and the adjudicator's locked initial review")
        if adjudicator_initial.reviewer_pseudonym != kwargs.get("reviewer_pseudonym"):
            raise ReviewError("adjudication must be recorded by the reviewer who locked the third independent assessment")
        return self._append(packet, phase=ReviewPhase.ADJUDICATION, **kwargs)

    def _append(
        self,
        packet: ReviewerPacket,
        *,
        reviewer_pseudonym: str,
        action: str,
        assessable: bool,
        rationale: str,
        evidence_basis: str,
        phase: ReviewPhase,
        replaces_event_id: str | None = None,
        blinding_compromised: bool = False,
        source_recognized: bool = False,
        unavoidable_exposure: tuple[str, ...] = (),
    ) -> ReviewEvent:
        _verify_packet_boundary(packet)
        if not isinstance(reviewer_pseudonym, str) or not reviewer_pseudonym.strip():
            raise ReviewError("reviewer pseudonym must be non-empty")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ReviewError("each locked assessment requires a rationale")
        if evidence_basis not in {"decision_time", "retrospective", "mixed", "insufficient"}:
            raise ReviewError("evidence_basis must distinguish decision-time and retrospective evidence")
        rationale = redact_credential_like_text(rationale)
        _assert_no_protected_disclosure(rationale)
        exposure = tuple(redact_credential_like_text(str(value)) for value in unavoidable_exposure)
        for value in exposure:
            _assert_no_protected_disclosure(value)
        existing = [event for event in self._events if event.packet_key == packet.packet_key]
        if phase in {ReviewPhase.INITIAL, ReviewPhase.REPLACEMENT, ReviewPhase.ADJUDICATOR_INITIAL}:
            if any(event.reviewer_pseudonym == reviewer_pseudonym for event in existing):
                raise ReviewError("a reviewer may not submit more than one independent assessment for a packet")
            expected = 2 if phase is ReviewPhase.INITIAL else 0
            if phase is ReviewPhase.INITIAL and sum(event.phase is phase for event in existing) >= expected:
                raise ReviewError("exactly two initial independent assessments are allowed")
            if phase is ReviewPhase.ADJUDICATOR_INITIAL and sum(event.phase is phase for event in existing) >= 1:
                raise ReviewError("exactly one adjudicator initial assessment is allowed")
        elif phase is ReviewPhase.ADJUDICATION:
            if any(event.phase is phase for event in existing):
                raise ReviewError("adjudication is append-only and may be recorded only once")
        if not _valid_action(packet.kind, action, assessable, phase):
            raise ReviewError(f"invalid {packet.kind.value} assessment action: {action!r}")
        if not packet.assessment_allowed and (
            assessable or action not in {"unknown", "unassessable", "unresolved"}
        ):
            raise ReviewError("assessment exceeds the packet evidence gate")
        event = ReviewEvent(
            event_id=str(uuid.uuid4()),
            packet_key=packet.packet_key,
            kind=packet.kind,
            phase=phase,
            reviewer_pseudonym=reviewer_pseudonym,
            action=action,
            assessable=assessable,
            rationale=rationale,
            evidence_basis=evidence_basis,
            blinding_compromised=blinding_compromised,
            source_recognized=source_recognized,
            unavoidable_exposure=exposure,
            timestamp_utc=self._clock(),
            replaces_event_id=replaces_event_id,
            packet_assessment_allowed=packet.assessment_allowed,
        )
        self._events.append(event)
        return event

    def candidate_resolution(self, packet_key: str) -> tuple[ReviewResolution, RelevanceLabel | None]:
        initial = self._active_initial(packet_key)
        if len(initial) != 2:
            return ReviewResolution.UNRESOLVED, None
        first, second = initial
        if first.action == second.action:
            return ReviewResolution.RESOLVED, RelevanceLabel(first.action)
        adjudication = next(
            (event for event in self._events if event.packet_key == packet_key and event.phase is ReviewPhase.ADJUDICATION),
            None,
        )
        if adjudication is None or adjudication.action == "unresolved" or adjudication.blinding_compromised:
            return ReviewResolution.UNRESOLVED, None
        return ReviewResolution.RESOLVED, RelevanceLabel(adjudication.action)

    def decision_action(self, packet_key: str) -> tuple[ReviewResolution, Decision | None, bool]:
        initial = self._active_initial(packet_key)
        if len(initial) != 2:
            return ReviewResolution.UNRESOLVED, None, False
        if initial[0].action == initial[1].action:
            event = initial[0]
            if event.action == "unassessable" or not event.assessable:
                return ReviewResolution.RESOLVED, None, False
            return ReviewResolution.RESOLVED, Decision(event.action), True
        adjudication = next(
            (event for event in self._events if event.packet_key == packet_key and event.phase is ReviewPhase.ADJUDICATION),
            None,
        )
        if adjudication is None or adjudication.action == "unresolved" or adjudication.blinding_compromised:
            return ReviewResolution.UNRESOLVED, None, False
        if adjudication.action == "unassessable" or not adjudication.assessable:
            return ReviewResolution.RESOLVED, None, False
        return ReviewResolution.RESOLVED, Decision(adjudication.action), True

    def _active_initial(self, packet_key: str) -> list[ReviewEvent]:
        originals = [
            event for event in self._events
            if event.packet_key == packet_key and event.phase is ReviewPhase.INITIAL
        ]
        active: list[ReviewEvent] = []
        for original in originals:
            replacement = next(
                (event for event in self._events if event.replaces_event_id == original.event_id),
                None,
            )
            selected = replacement or original
            if selected.blinding_compromised:
                return []
            active.append(selected)
        return active

    def _active_adjudicator_initial(self, packet_key: str) -> ReviewEvent | None:
        original = next(
            (
                event for event in self._events
                if event.packet_key == packet_key and event.phase is ReviewPhase.ADJUDICATOR_INITIAL
            ),
            None,
        )
        if original is None:
            return None
        replacement = next(
            (event for event in self._events if event.replaces_event_id == original.event_id),
            None,
        )
        selected = replacement or original
        return None if selected.blinding_compromised else selected

    def persist_append_only(self, path: str | Path, event: ReviewEvent) -> None:
        """Append one locked event without replacing prior reviewer judgments."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(destination, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ReviewError("review event target must be a regular file")
            os.fchmod(descriptor, 0o600)
            written = 0
            while written < len(payload):
                written += os.write(descriptor, payload[written:])
        finally:
            os.close(descriptor)


def _valid_action(kind: ReviewKind, action: str, assessable: bool, phase: ReviewPhase) -> bool:
    if phase is ReviewPhase.ADJUDICATION and action == "unresolved" and not assessable:
        return True
    if kind is ReviewKind.CANDIDATE_RELEVANCE:
        return (
            action in {RelevanceLabel.RELEVANT.value, RelevanceLabel.IRRELEVANT.value} and assessable
        ) or (action == RelevanceLabel.UNKNOWN.value and not assessable)
    return (action in {item.value for item in Decision} and assessable) or (action == "unassessable" and not assessable)


def decision_conformity_outcome(
    system_decision: Decision | None,
    independently_justified_action: Decision | None,
    *,
    assessable: bool,
    review_resolution: ReviewResolution,
    task_gates_approved: bool,
    arm_valid: bool = True,
) -> DecisionConformity:
    """Compare only after blinded review; unresolved task gates remain indeterminate."""
    if (
        not arm_valid
        or system_decision is None
        or independently_justified_action is None
        or not assessable
        or review_resolution is not ReviewResolution.RESOLVED
        or not task_gates_approved
    ):
        return DecisionConformity.INDETERMINATE
    return (
        DecisionConformity.CORRECT
        if system_decision is independently_justified_action
        else DecisionConformity.INCORRECT
    )


def apply_candidate_review_labels(pairs: Iterable[Any], ledger: ReviewLedger) -> None:
    """Attach resolved candidate labels to restricted slots without changing evidence."""
    for pair in pairs:
        arms = pair.arms if hasattr(pair, "arms") else pair.get("arms", {})
        for arm in arms.values():
            slots = arm.slots if hasattr(arm, "slots") else arm.get("slots", ())
            for slot in slots:
                key = slot.opaque_candidate_key if hasattr(slot, "opaque_candidate_key") else slot.get("opaque_candidate_key")
                if not key:
                    continue
                resolution, label = ledger.candidate_resolution(key)
                if hasattr(slot, "review_resolution"):
                    slot.review_resolution = resolution
                    slot.relevance_label = label
                else:
                    slot["review_resolution"] = resolution.value
                    slot["relevance_label"] = label.value if label else None


def decision_outcomes_by_pair(
    pairs: Iterable[Any],
    ledger: ReviewLedger,
    packet_keys_by_pair: dict[str, str],
    *,
    task_gates_approved: dict[str, bool] | bool = False,
) -> dict[str, DecisionConformity]:
    """Apply blinded action reviews only after lock, then compare evaluator-side decisions."""
    outcomes: dict[str, DecisionConformity] = {}
    for pair in pairs:
        pair_id = pair.pair_id if hasattr(pair, "pair_id") else pair.get("pair_id")
        arms = pair.arms if hasattr(pair, "arms") else pair.get("arms", {})
        scout = arms.get(ArmName.SKILLSCOUT, arms.get(ArmName.SKILLSCOUT.value))
        if scout is None:
            outcomes[str(pair_id)] = DecisionConformity.INDETERMINATE
            continue
        key = packet_keys_by_pair.get(str(pair_id))
        resolution, proposed_action, assessable = (
            ledger.decision_action(key) if key else (ReviewResolution.UNRESOLVED, None, False)
        )
        task_id = pair.task_id if hasattr(pair, "task_id") else pair.get("task_id")
        approved = task_gates_approved.get(str(task_id), False) if isinstance(task_gates_approved, dict) else task_gates_approved
        valid = (
            scout.primary_response_valid if hasattr(scout, "primary_response_valid")
            else scout.get("primary_response_valid", False)
        )
        status = scout.status if hasattr(scout, "status") else scout.get("status")
        status = getattr(status, "value", status)
        system_decision = scout.decision if hasattr(scout, "decision") else scout.get("decision")
        system_decision = Decision(system_decision) if system_decision is not None and not isinstance(system_decision, Decision) else system_decision
        outcomes[str(pair_id)] = decision_conformity_outcome(
            system_decision,
            proposed_action,
            assessable=assessable,
            review_resolution=resolution,
            task_gates_approved=approved,
            arm_valid=bool(valid) and status in {ArmStatus.PARTIAL.value, ArmStatus.COMPLETE.value},
        )
    return outcomes
