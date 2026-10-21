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
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable

from skillscout.evaluation.fixtures import PROTECTED_AUDIT_TEXT
from skillscout.evaluation.models import Decision, DecisionConformity, RelevanceLabel, ReviewResolution
from skillscout.evaluation.protocol import EvaluationTask, criteria_for_candidate_review


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


@dataclass(frozen=True, slots=True)
class ReviewerPacket:
    packet_key: str
    kind: ReviewKind
    task_text: str
    criteria: tuple[str, ...]
    approved_context: str
    evidence: tuple[EvidenceExcerpt, ...]
    decision_options: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_key": self.packet_key,
            "kind": self.kind.value,
            "task_text": self.task_text,
            "criteria": list(self.criteria),
            "approved_context": self.approved_context,
            "evidence": [asdict(item) for item in self.evidence],
            "decision_options": list(self.decision_options),
        }


_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_PROTECTED_EQUIVALENT = re.compile(
    r"(?is)(?:no|without|missing|lacks?|unverified|not\s+verified).{0,100}"
    r"(?:revision[- ]bound|revision[- ]specific|current\s+revision).{0,100}"
    r"(?:external\s+)?audit\s+evidence"
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


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _assert_no_protected_disclosure(text: str) -> None:
    normalized = _normalize(text)
    if any(fragment in normalized for fragment in PROTECTED_AUDIT_TEXT) or _PROTECTED_EQUIVALENT.search(text):
        raise ReviewError("protected audit-state disclosure is not allowed in reviewer packets")


def _mask_identifiers(text: str, identifiers: Iterable[str]) -> str:
    result = text
    for identifier in sorted({value for value in identifiers if value}, key=len, reverse=True):
        result = re.sub(re.escape(identifier), "[candidate identifier masked]", result, flags=re.IGNORECASE)
    result = _URL.sub("[source URL masked]", result)
    _assert_no_protected_disclosure(result)
    return result


def _allowed_evidence(
    items: Iterable[EvidenceExcerpt],
    *,
    identifiers_to_mask: Iterable[str] = (),
) -> tuple[EvidenceExcerpt, ...]:
    sanitized: list[EvidenceExcerpt] = []
    for item in items:
        if not isinstance(item, EvidenceExcerpt):
            raise ReviewError("evidence must use the explicit EvidenceExcerpt schema")
        if item.evidence_type.casefold() in {"audit", "safety", "scanner", "protected_finding"}:
            continue
        if item.provenance not in {"decision_time_arm_evidence", "retrospective_evaluator_evidence", "task_context"}:
            raise ReviewError(f"unsupported evidence provenance: {item.provenance}")
        content = _mask_identifiers(item.content, identifiers_to_mask)
        sanitized.append(
            EvidenceExcerpt(
                content=content,
                evidence_type=item.evidence_type,
                provenance=item.provenance,
                relevance_sufficient=item.relevance_sufficient,
                identity_linked=item.identity_linked,
                revision_linked=item.revision_linked,
            )
        )
    return tuple(sanitized)


def _verify_packet_boundary(packet: ReviewerPacket) -> None:
    serialized = packet.to_dict()
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.casefold() in _FORBIDDEN_KEYS:
                    raise ReviewError(f"forbidden reviewer packet field: {key}")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            _assert_no_protected_disclosure(value)
    visit(serialized)


def build_candidate_relevance_packet(
    task: EvaluationTask,
    *,
    approved_context: str,
    evidence: Iterable[EvidenceExcerpt],
    packet_key_factory: Callable[[], str] | None = None,
    identifiers_to_mask: Iterable[str] = (),
) -> ReviewerPacket:
    """Build a candidate-only packet with criteria but no evaluator labels."""
    packet = ReviewerPacket(
        packet_key=(packet_key_factory or (lambda: str(uuid.uuid4())))(),
        kind=ReviewKind.CANDIDATE_RELEVANCE,
        task_text=task.task_text,
        criteria=criteria_for_candidate_review(task),
        approved_context=_mask_identifiers(approved_context, identifiers_to_mask),
        evidence=_allowed_evidence(evidence, identifiers_to_mask=identifiers_to_mask),
    )
    _verify_packet_boundary(packet)
    return packet


def build_decision_conformity_packet(
    task: EvaluationTask,
    *,
    approved_context: str,
    evidence: Iterable[EvidenceExcerpt],
    packet_key_factory: Callable[[], str] | None = None,
    identifiers_to_mask: Iterable[str] = (),
) -> ReviewerPacket:
    """Build a task/action assessment packet without the original action or labels."""
    packet = ReviewerPacket(
        packet_key=(packet_key_factory or (lambda: str(uuid.uuid4())))(),
        kind=ReviewKind.DECISION_CONFORMITY,
        task_text=task.task_text,
        criteria=(),
        approved_context=_mask_identifiers(approved_context, identifiers_to_mask),
        evidence=_allowed_evidence(evidence, identifiers_to_mask=identifiers_to_mask),
        decision_options=tuple(decision.value for decision in Decision),
    )
    _verify_packet_boundary(packet)
    return packet


def shuffle_packets(packets: Iterable[ReviewerPacket], *, seed: int) -> tuple[ReviewerPacket, ...]:
    """Return a reproducible, recorded-seed review order without exposing the crosswalk."""
    result = list(packets)
    random.Random(seed).shuffle(result)
    return tuple(result)


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
        if len(self._active_initial(packet.packet_key)) != 2:
            raise ReviewError("adjudicator must lock after both initial reviews exist")
        return self._append(packet, phase=ReviewPhase.ADJUDICATOR_INITIAL, **kwargs)

    def lock_replacement(self, packet: ReviewerPacket, *, replaces_event_id: str, **kwargs: Any) -> ReviewEvent:
        source = next((event for event in self._events if event.event_id == replaces_event_id), None)
        if source is None or source.packet_key != packet.packet_key or source.phase is not ReviewPhase.INITIAL:
            raise ReviewError("replacement must reference an initial assessment for this packet")
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
        prior = [event for event in self._events if event.packet_key == packet.packet_key]
        independent = self._active_initial(packet.packet_key)
        adjudicator_initial = [event for event in prior if event.phase is ReviewPhase.ADJUDICATOR_INITIAL]
        if len(independent) != 2 or len(adjudicator_initial) != 1:
            raise ReviewError("adjudication requires two locked initial reviews and the adjudicator's locked initial review")
        if adjudicator_initial[0].reviewer_pseudonym != kwargs.get("reviewer_pseudonym"):
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
        if not reviewer_pseudonym.strip():
            raise ReviewError("reviewer pseudonym must be non-empty")
        if not rationale.strip():
            raise ReviewError("each locked assessment requires a rationale")
        if evidence_basis not in {"decision_time", "retrospective", "mixed", "insufficient"}:
            raise ReviewError("evidence_basis must distinguish decision-time and retrospective evidence")
        _assert_no_protected_disclosure(rationale)
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
            unavoidable_exposure=tuple(unavoidable_exposure),
            timestamp_utc=self._clock(),
            replaces_event_id=replaces_event_id,
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

    def persist_append_only(self, path: str | Path, event: ReviewEvent) -> None:
        """Append one locked event without replacing prior reviewer judgments."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)


def _valid_action(kind: ReviewKind, action: str, assessable: bool, phase: ReviewPhase) -> bool:
    if phase is ReviewPhase.ADJUDICATION and action == "unresolved" and not assessable:
        return True
    if kind is ReviewKind.CANDIDATE_RELEVANCE:
        return action in {item.value for item in RelevanceLabel} and (action != "unknown" or not assessable)
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
