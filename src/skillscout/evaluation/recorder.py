"""Offline-first sequential pair recorder with fail-closed deadline handling."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from skillscout.evaluation.models import (
    ArmRecord,
    ArmStatus,
    CandidateObservation,
    CandidateSlot,
    Decision,
    InvocationFailure,
    InvocationReturn,
    LinkageStatus,
    LookupRecord,
    LookupStatus,
    PairRecord,
    PairStatus,
    PrimaryResponse,
    SlotStatus,
    TerminationStatus,
)
from skillscout.evaluation.crosswalk import CandidateCrosswalk
from skillscout.evaluation.protocol import (
    ArmName,
    DATASET_SHA256,
    INVOCATION_TIMEOUT_SECONDS,
    LOOKUP_BUDGET_SECONDS,
    PAIRING_WINDOW_SECONDS,
    PairPlan,
    SLOTS_PER_ARM,
    EvaluationTask,
    build_schedule,
    ordinary_search_input,
    skillscout_input,
    load_dataset,
)
from skillscout.evaluation.fixtures import (
    has_credential_like_text,
    materialize_opaque_contexts,
    redact_credential_like_text,
    verify_fixture_pack,
)


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def utcnow(self) -> datetime: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def utcnow(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class LookupRequest:
    arm: ArmName
    candidate_id: str
    rank: int
    kind: str
    arm_content_hash: str | None
    arm_revision: str | None
    timeout_cap_s: float | None = None


@dataclass(frozen=True, slots=True)
class LookupObservation:
    success: bool
    returned_candidate_id: str | None = None
    content_hash: str | None = None
    revision: str | None = None
    http_status: int | None = None
    error_category: str | None = None
    identity_linkage: LinkageStatus = LinkageStatus.NOT_APPLICABLE
    revision_linkage: LinkageStatus = LinkageStatus.NOT_APPLICABLE
    detail_sufficient_for_relevance: bool | None = None
    audit_status: str | None = None
    audit_matches_revision: bool | None = None
    response_metadata: dict[str, str] = field(default_factory=dict)
    termination_status: TerminationStatus = TerminationStatus.CONFIRMED
    termination_evidence: str = "lookup callback returned a terminal observation"


PrimaryInvoker = Callable[[dict[str, object], int], InvocationReturn]
EvidenceLookup = Callable[[LookupRequest], LookupObservation]


@dataclass(frozen=True, slots=True)
class InvocationHandlers:
    skillscout: PrimaryInvoker
    ordinary_search: PrimaryInvoker

    def for_arm(self, arm: ArmName) -> PrimaryInvoker:
        return self.skillscout if arm is ArmName.SKILLSCOUT else self.ordinary_search


class RunModeError(ValueError):
    """The core recorder refuses to start a live-catalog collection."""


def utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("UTC audit timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


_SAFE_RESPONSE_HEADERS = {"date", "etag", "request-id", "x-request-id"}


def _safe_text(value: str | None, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    return redact_credential_like_text(str(value))[:limit]


def _safe_metadata(value: Mapping[str, str]) -> dict[str, str]:
    return {
        str(key): _safe_text(str(item), limit=200) or ""
        for key, item in value.items()
        if str(key).casefold() in _SAFE_RESPONSE_HEADERS
    }


def _safe_error_category(value: str) -> str:
    value = str(value)
    if has_credential_like_text(value):
        return "sensitive_error_category_redacted"
    return value[:100] if re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value) else "unspecified_error"


def _supplemental_response(response: PrimaryResponse) -> dict[str, object]:
    """Retain bounded late metadata without making it a primary arm result."""
    if not isinstance(response, PrimaryResponse):
        return {"malformed": True}
    if not isinstance(response.candidates, (tuple, list)):
        return {"malformed": True, "decision": None}
    if any(
        not isinstance(candidate, CandidateObservation)
        or not isinstance(candidate.stable_id, str)
        or any(
            has_credential_like_text(str(value or ""))
            for value in (
                candidate.stable_id,
                str(candidate.source_slug or ""),
                str(candidate.source_url or ""),
                str(candidate.arm_content_hash or ""),
                str(candidate.arm_revision or ""),
            )
        )
        or any(
            len(str(value or "")) > 2048
            for value in (
                candidate.stable_id,
                candidate.source_slug,
                candidate.source_url,
                candidate.arm_content_hash,
                candidate.arm_revision,
            )
        )
        for candidate in response.candidates[:SLOTS_PER_ARM]
    ):
        return {
            "malformed": True,
            "candidate_metadata_redacted": True,
            "decision": response.decision.value if isinstance(response.decision, Decision) else None,
            "partial": bool(response.partial),
            "metadata": _safe_metadata(response.metadata) if isinstance(response.metadata, Mapping) else {},
        }
    return {
        "decision": response.decision.value if isinstance(response.decision, Decision) else None,
        "partial": bool(response.partial),
        "candidates": [
            {
                "stable_id": candidate.stable_id,
                "source_slug": candidate.source_slug,
                "rank": candidate.rank,
                "content_hash": candidate.arm_content_hash,
                "revision": candidate.arm_revision,
            }
            for candidate in response.candidates[:SLOTS_PER_ARM]
            if isinstance(candidate, CandidateObservation)
        ],
        "metadata": _safe_metadata(response.metadata) if isinstance(response.metadata, Mapping) else {},
    }


def _empty_slots(status: SlotStatus) -> list[CandidateSlot]:
    return [CandidateSlot(position=i, status=status) for i in range(1, SLOTS_PER_ARM + 1)]


class EvaluationRecorder:
    """Record the fixed schedule using injected, offline/mock invocation callbacks.

    The callbacks receive only arm-specific system inputs and a timeout cap. They
    must enforce the cap at their process/transport boundary and report termination
    evidence. This module contains no catalog client or live-run adapter.
    """

    def __init__(
        self,
        handlers: InvocationHandlers,
        *,
        lookup: EvidenceLookup | None = None,
        clock: Clock | None = None,
        invocation_timeout_s: int = INVOCATION_TIMEOUT_SECONDS,
        pairing_window_s: int = PAIRING_WINDOW_SECONDS,
        lookup_budget_s: int = LOOKUP_BUDGET_SECONDS,
        crosswalk: CandidateCrosswalk | None = None,
    ) -> None:
        self.handlers = handlers
        self.lookup = lookup
        self.clock = clock or SystemClock()
        self.invocation_timeout_s = invocation_timeout_s
        self.pairing_window_s = pairing_window_s
        self.lookup_budget_s = lookup_budget_s
        self._provided_crosswalk = crosswalk
        self.crosswalk = crosswalk or CandidateCrosswalk()
        if min(invocation_timeout_s, pairing_window_s, lookup_budget_s) <= 0:
            raise ValueError("protocol deadlines must be positive")

    def run_offline(
        self,
        tasks: tuple[EvaluationTask, ...] | list[EvaluationTask],
        context_paths: Mapping[str, str | Path],
        context_hashes: Mapping[str, str],
        *,
        dataset_sha256: str = DATASET_SHA256,
        catalog_mode: str = "offline",
        run_id: str = "offline-run",
        blocked_task_ids: set[str] | frozenset[str] = frozenset(),
    ) -> list[PairRecord]:
        """Run callbacks only in offline/mocked mode; live collection is rejected."""
        if catalog_mode not in {"offline", "mocked"}:
            raise RunModeError("live catalog collection is disabled in this milestone")
        if dataset_sha256 != DATASET_SHA256:
            raise ValueError("recorder refuses a dataset hash other than the approved v1 hash")
        if set(context_paths) != {task.task_id for task in tasks}:
            raise ValueError("opaque context paths must cover exactly the eligible tasks")
        if set(context_hashes) != {task.task_id for task in tasks}:
            raise ValueError("context hashes must cover exactly the eligible tasks")
        canonical_tasks = load_dataset().eligible_tasks
        if [(item.task_id, item.task_text, item.eligible_index) for item in tasks] != [
            (item.task_id, item.task_text, item.eligible_index) for item in canonical_tasks
        ]:
            raise ValueError("recorder tasks must exactly match the approved eligible dataset in order")
        task_ids = {task.task_id for task in tasks}
        if not set(blocked_task_ids).issubset(task_ids):
            raise ValueError("blocked task IDs must be drawn from the eligible dataset")
        for task in tasks:
            path_parts = {part.casefold() for part in Path(context_paths[task.task_id]).parts}
            if task.task_id.casefold() in path_parts or task.task_id.casefold() in str(context_paths[task.task_id]).casefold():
                raise ValueError("system-facing project paths must not disclose evaluator task IDs")
            if not re.fullmatch(r"[0-9a-f]{64}", context_hashes[task.task_id]):
                raise ValueError("each context manifest hash must be lowercase SHA-256")

        if self._provided_crosswalk is None:
            self.crosswalk = CandidateCrosswalk()

        task_by_id = {task.task_id: task for task in tasks}
        records: list[PairRecord] = []
        stop_reason: str | None = None
        for plan in build_schedule(tasks):
            task = task_by_id[plan.task_id]
            if stop_reason is not None:
                records.append(
                    self._uninvoked_pair(
                        plan,
                        context_hashes[plan.task_id],
                        PairStatus.NOT_RUN,
                        stop_reason,
                        run_id,
                        dataset_sha256,
                    )
                )
                continue
            if plan.task_id in blocked_task_ids:
                records.append(
                    self._uninvoked_pair(
                        plan,
                        context_hashes[plan.task_id],
                        PairStatus.BLOCKED,
                        "context_incomplete_by_protocol",
                        run_id,
                        dataset_sha256,
                    )
                )
                continue
            pair = self.record_pair(
                plan,
                task,
                context_paths[plan.task_id],
                context_hashes[plan.task_id],
                run_id=run_id,
                dataset_sha256=dataset_sha256,
            )
            records.append(pair)
            if pair.run_stop_required:
                stop_reason = pair.run_stop_reason or "review_required_after_unconfirmed_termination"
        return records

    def run_with_pinned_fixtures(
        self,
        contexts_root: str | Path,
        tasks: tuple[EvaluationTask, ...] | list[EvaluationTask],
        *,
        run_id: str = "offline-run",
        dataset_sha256: str = DATASET_SHA256,
        temporary_root: str | Path | None = None,
    ) -> list[PairRecord]:
        """Verify and materialize approved local fixtures before offline callbacks."""
        verified = verify_fixture_pack(contexts_root, tasks, dataset_sha256=dataset_sha256)
        hashes = {item["task_id"]: item["manifest_sha256"] for item in verified["fixtures"]}
        blocked = {
            item["task_id"]
            for item in verified["fixtures"]
            if item["context_status"] == "incomplete_by_protocol"
        }
        with materialize_opaque_contexts(contexts_root, tasks, temporary_root=temporary_root) as paths:
            return self.run_offline(
                tasks,
                paths,
                hashes,
                dataset_sha256=dataset_sha256,
                catalog_mode="offline",
                run_id=run_id,
                blocked_task_ids=blocked,
            )

    def record_pair(
        self,
        plan: PairPlan,
        task: EvaluationTask,
        opaque_project_path: str | Path,
        context_manifest_sha256: str,
        *,
        run_id: str,
        dataset_sha256: str = DATASET_SHA256,
    ) -> PairRecord:
        if task.task_id != plan.task_id or task.eligible_index != plan.task_index:
            raise ValueError("pair plan does not match evaluator task")
        arms: dict[ArmName, ArmRecord] = {}
        for arm in ArmName:
            arms[arm] = ArmRecord(
                arm=arm,
                status=ArmStatus.NOT_RUN,
                invocation_timeout_s=self.invocation_timeout_s,
                underlying_termination=TerminationStatus.UNKNOWN,
                slots=_empty_slots(SlotStatus.NOT_RUN),
            )

        first_arm, second_arm = plan.arm_order
        pair_id = f"p{plan.pair_number:03d}-t{plan.task_index:02d}-r{plan.repetition}"
        first_record = self._invoke(
            first_arm,
            task.task_text,
            opaque_project_path,
            task_id=task.task_id,
            repetition=plan.repetition,
            pair_id=pair_id,
        )
        arms[first_arm] = first_record
        first_start = first_record.invocation_start_monotonic_s
        first_start_utc = first_record.invocation_start_utc
        first_end = first_record.invocation_end_monotonic_s
        first_end_utc = first_record.invocation_end_utc
        second_record: ArmRecord | None = None
        second_start: float | None = None
        second_start_utc: str | None = None

        must_stop = self._requires_review_stop(first_record)
        status_reason: str | None = None
        deviations: list[str] = []
        if must_stop:
            status_reason = "review_required_after_unconfirmed_termination"
            second_record = ArmRecord(
                arm=second_arm,
                status=ArmStatus.NOT_RUN,
                invocation_timeout_s=self.invocation_timeout_s,
                error_category=status_reason,
                underlying_termination=TerminationStatus.UNKNOWN,
                slots=_empty_slots(SlotStatus.NOT_RUN),
            )
            arms[second_arm] = second_record
        else:
            assert first_start is not None
            dispatch_deadline = first_start + self.pairing_window_s
            if self.clock.monotonic() > dispatch_deadline:
                second_record = ArmRecord(
                    arm=second_arm,
                    status=ArmStatus.NOT_RUN,
                    invocation_timeout_s=self.invocation_timeout_s,
                    error_category="pairing_deadline_expired_before_dispatch",
                    underlying_termination=TerminationStatus.UNKNOWN,
                    slots=_empty_slots(SlotStatus.NOT_RUN),
                )
                arms[second_arm] = second_record
                status_reason = "pairing_deadline_expired_before_second_arm_start"
            else:
                second_record = self._invoke(
                    second_arm,
                    task.task_text,
                    opaque_project_path,
                    not_after=dispatch_deadline,
                    task_id=task.task_id,
                    repetition=plan.repetition,
                    pair_id=pair_id,
                )
                arms[second_arm] = second_record
                second_start = second_record.invocation_start_monotonic_s
                second_start_utc = second_record.invocation_start_utc
                if second_record.status is ArmStatus.NOT_RUN:
                    status_reason = "pairing_deadline_expired_before_second_arm_start"
                if second_start is not None and second_start > dispatch_deadline:
                    deviations.append("second_arm_started_after_pairing_deadline")
                    status_reason = "second_arm_started_after_pairing_deadline"
                if self._requires_review_stop(second_record):
                    must_stop = True
                    status_reason = "review_required_after_unconfirmed_termination"

        pairing_interval = None
        pairing_within: bool | None = None
        if first_start is not None and second_start is not None:
            pairing_interval = second_start - first_start
            pairing_within = pairing_interval <= self.pairing_window_s
        pair_status = self._pair_status(first_record, second_record, pairing_within)

        pair = PairRecord(
            schema_version="skillscout-evaluation-v1",
            run_id=run_id,
            dataset_sha256=dataset_sha256,
            pair_number=plan.pair_number,
            pair_id=pair_id,
            task_id=plan.task_id,
            task_index=plan.task_index,
            repetition=plan.repetition,
            arm_order=plan.arm_order,
            context_manifest_sha256=context_manifest_sha256,
            status=pair_status,
            status_reason=status_reason,
            first_invocation_start_monotonic_s=first_start,
            second_invocation_start_monotonic_s=second_start,
            first_invocation_start_utc=first_start_utc,
            second_invocation_start_utc=second_start_utc,
            pairing_interval_s=pairing_interval,
            pairing_within_window=pairing_within,
            arms=arms,
            run_stop_required=must_stop,
            run_stop_reason=status_reason if must_stop else None,
            deviation_codes=deviations,
        )
        if second_record is not None:
            self._pair_wait(first_record, second_record)
        if self._both_invoked_terminal(pair):
            self._run_lookups(pair)
        self._compute_evidence_ready(pair)
        return pair

    def _invoke(
        self,
        arm: ArmName,
        task_text: str,
        project_path: str | Path,
        *,
        not_after: float | None = None,
        task_id: str,
        repetition: int,
        pair_id: str,
    ) -> ArmRecord:
        request = (
            skillscout_input(task_text, project_path)
            if arm is ArmName.SKILLSCOUT
            else ordinary_search_input(task_text)
        )
        start_utc = utc_text(self.clock.utcnow())
        start = self.clock.monotonic()
        if not_after is not None and start > not_after:
            return ArmRecord(
                arm=arm,
                status=ArmStatus.NOT_RUN,
                invocation_timeout_s=self.invocation_timeout_s,
                error_category="pairing_deadline_expired_before_dispatch",
                underlying_termination=TerminationStatus.UNKNOWN,
                slots=_empty_slots(SlotStatus.NOT_RUN),
            )
        try:
            returned = self.handlers.for_arm(arm)(request, self.invocation_timeout_s)
            if not isinstance(returned, InvocationReturn):
                raise TypeError("runner must return InvocationReturn")
            if not isinstance(returned.termination_status, TerminationStatus):
                raise TypeError("runner returned an invalid termination status")
        except InvocationFailure as exc:
            end = self.clock.monotonic()
            status = ArmStatus.FAILED
            timeout = exc.category in {"invocation_timeout", "harness_timeout", "timeout"} or (
                "timeout" in (exc.harness_outcome or "").casefold()
            )
            return ArmRecord(
                arm=arm,
                status=status,
                invocation_timeout_s=self.invocation_timeout_s,
                invocation_start_monotonic_s=start,
                invocation_end_monotonic_s=end,
                invocation_start_utc=start_utc,
                invocation_end_utc=utc_text(self.clock.utcnow()),
                response_latency_s=max(0.0, end - start),
                harness_outcome="timeout" if timeout else ("harness_failure" if "harness" in exc.category else "terminal_error"),
                underlying_termination=(
                    exc.termination_status
                    if isinstance(exc.termination_status, TerminationStatus)
                    else TerminationStatus.UNKNOWN
                ),
                termination_evidence=_safe_text(exc.termination_evidence) or None,
                error_category="invocation_timeout" if timeout else _safe_error_category(exc.category),
                decision=None,
                primary_response_valid=False,
                slots=_empty_slots(SlotStatus.FAILED),
            )
        except Exception as exc:  # noqa: BLE001 - preserve unexpected harness errors without fabricating output.
            end = self.clock.monotonic()
            return ArmRecord(
                arm=arm,
                status=ArmStatus.FAILED,
                invocation_timeout_s=self.invocation_timeout_s,
                invocation_start_monotonic_s=start,
                invocation_end_monotonic_s=end,
                invocation_start_utc=start_utc,
                invocation_end_utc=utc_text(self.clock.utcnow()),
                response_latency_s=max(0.0, end - start),
                harness_outcome="harness_failure",
                underlying_termination=TerminationStatus.UNKNOWN,
                error_category=f"harness_failure:{type(exc).__name__}",
                decision=None,
                primary_response_valid=False,
                slots=_empty_slots(SlotStatus.FAILED),
            )

        end = self.clock.monotonic()
        end_utc = utc_text(self.clock.utcnow())
        elapsed = end - start
        if elapsed < 0:
            return ArmRecord(
                arm=arm,
                status=ArmStatus.FAILED,
                invocation_timeout_s=self.invocation_timeout_s,
                invocation_start_monotonic_s=start,
                invocation_end_monotonic_s=end,
                invocation_start_utc=start_utc,
                invocation_end_utc=end_utc,
                response_latency_s=None,
                harness_outcome="harness_failure",
                underlying_termination=TerminationStatus.UNKNOWN,
                error_category="harness_failure:monotonic_clock_regression",
                slots=_empty_slots(SlotStatus.FAILED),
            )
        if elapsed > self.invocation_timeout_s:
            return ArmRecord(
                arm=arm,
                status=ArmStatus.FAILED,
                invocation_timeout_s=self.invocation_timeout_s,
                invocation_start_monotonic_s=start,
                invocation_end_monotonic_s=end,
                invocation_start_utc=start_utc,
                invocation_end_utc=end_utc,
                response_latency_s=elapsed,
                harness_outcome="timeout",
                underlying_termination=returned.termination_status,
                termination_evidence=_safe_text(returned.termination_evidence),
                error_category="invocation_timeout",
                decision=None,
                primary_response_valid=False,
                late_primary_response=True,
                supplemental_late_response=_supplemental_response(returned.response),
                slots=_empty_slots(SlotStatus.FAILED),
            )
        response = returned.response
        malformed = self._response_error(arm, response)
        if malformed is not None:
            return ArmRecord(
                arm=arm,
                status=ArmStatus.FAILED,
                invocation_timeout_s=self.invocation_timeout_s,
                invocation_start_monotonic_s=start,
                invocation_end_monotonic_s=end,
                invocation_start_utc=start_utc,
                invocation_end_utc=end_utc,
                response_latency_s=elapsed,
                harness_outcome="response_validation_failure",
                underlying_termination=returned.termination_status,
                termination_evidence=returned.termination_evidence,
                error_category=malformed,
                decision=None,
                primary_response_valid=False,
                slots=_empty_slots(SlotStatus.FAILED),
            )
        arm_status = ArmStatus.PARTIAL if response.partial else ArmStatus.COMPLETE
        slots = self._slots_from_response(
            response,
            arm=arm,
            pair_id=pair_id,
            task_id=task_id,
            repetition=repetition,
        )
        return ArmRecord(
            arm=arm,
            status=arm_status,
            invocation_timeout_s=self.invocation_timeout_s,
            invocation_start_monotonic_s=start,
            invocation_end_monotonic_s=end,
            invocation_start_utc=start_utc,
            invocation_end_utc=end_utc,
            response_latency_s=elapsed,
            harness_outcome="returned",
            underlying_termination=returned.termination_status,
            termination_evidence=_safe_text(returned.termination_evidence),
            decision=response.decision if arm is ArmName.SKILLSCOUT else None,
            primary_response_valid=True,
            internal_attempt_count=response.internal_attempt_count,
            returned_metadata=_safe_metadata(response.metadata),
            slots=slots,
        )

    @staticmethod
    def _response_error(arm: ArmName, response: PrimaryResponse) -> str | None:
        if not isinstance(response, PrimaryResponse):
            return "malformed_response"
        if not isinstance(response.candidates, (tuple, list)) or not all(
            isinstance(candidate, CandidateObservation) for candidate in response.candidates
        ):
            return "malformed_candidate_list"
        if not isinstance(response.partial, bool) or not isinstance(response.metadata, Mapping):
            return "malformed_response_metadata"
        if response.decision is not None and not isinstance(response.decision, Decision):
            return "malformed_decision"
        if len(response.candidates) > SLOTS_PER_ARM:
            return "malformed_response_more_than_three_candidates"
        if arm is ArmName.ORDINARY_SEARCH and response.decision is not None:
            return "baseline_decision_must_be_null"
        for position, candidate in enumerate(response.candidates, start=1):
            if (
                not isinstance(candidate.stable_id, str)
                or not candidate.stable_id
                or not isinstance(candidate.rank, int)
                or isinstance(candidate.rank, bool)
                or candidate.rank != position
            ):
                return "malformed_candidate_identity_or_order"
            optional_values = (
                candidate.source_slug,
                candidate.source_url,
                candidate.arm_content_hash,
                candidate.arm_revision,
            )
            if any(value is not None and not isinstance(value, str) for value in optional_values):
                return "malformed_candidate_metadata_type"
            values = (
                candidate.stable_id,
                candidate.source_slug or "",
                candidate.source_url or "",
                candidate.arm_content_hash or "",
                candidate.arm_revision or "",
            )
            if any(len(value) > 2048 for value in values):
                return "malformed_candidate_metadata_length"
            if any(has_credential_like_text(value) for value in values):
                return "sensitive_candidate_metadata_redacted"
        if response.internal_attempt_count is not None and (
            not isinstance(response.internal_attempt_count, int)
            or isinstance(response.internal_attempt_count, bool)
            or response.internal_attempt_count < 1
        ):
            return "invalid_attempt_count"
        return None

    def _slots_from_response(
        self,
        response: PrimaryResponse,
        *,
        arm: ArmName,
        pair_id: str,
        task_id: str,
        repetition: int,
    ) -> list[CandidateSlot]:
        slots: list[CandidateSlot] = []
        for position in range(1, SLOTS_PER_ARM + 1):
            if position <= len(response.candidates):
                item = response.candidates[position - 1]
                opaque_key = self.crosswalk.register(
                    pair_id=pair_id,
                    task_id=task_id,
                    repetition=repetition,
                    arm=arm,
                    rank=item.rank,
                    stable_id=item.stable_id,
                    source_url=item.source_url,
                    content_hash=item.arm_content_hash,
                    revision=item.arm_revision,
                )
                slots.append(
                    CandidateSlot(
                        position=position,
                        status=SlotStatus.OCCUPIED,
                        stable_id=item.stable_id,
                        source_slug=item.source_slug,
                        rank=item.rank,
                        arm_content_hash=item.arm_content_hash,
                        arm_revision=item.arm_revision,
                        opaque_candidate_key=opaque_key,
                    )
                )
            else:
                slots.append(
                    CandidateSlot(
                        position=position,
                        status=SlotStatus.UNKNOWN if response.partial else SlotStatus.EMPTY,
                    )
                )
        return slots

    @staticmethod
    def _requires_review_stop(record: ArmRecord) -> bool:
        return (
            record.status is ArmStatus.FAILED
            and record.invocation_start_monotonic_s is not None
            and record.underlying_termination in {TerminationStatus.NOT_CONFIRMED, TerminationStatus.UNKNOWN}
        )

    def _pair_status(
        self,
        first: ArmRecord,
        second: ArmRecord,
        within_window: bool | None,
    ) -> PairStatus:
        if first.status is ArmStatus.BLOCKED and second.status is ArmStatus.BLOCKED:
            return PairStatus.BLOCKED
        if first.status is ArmStatus.NOT_RUN and second.status is ArmStatus.NOT_RUN:
            return PairStatus.NOT_RUN
        if (
            first.status is ArmStatus.FAILED
            and second.status is ArmStatus.FAILED
            and first.primary_response_valid is False
            and second.primary_response_valid is False
            and first.invocation_start_monotonic_s is not None
            and second.invocation_start_monotonic_s is not None
        ):
            return PairStatus.FAILED
        if (
            first.status is ArmStatus.COMPLETE
            and second.status is ArmStatus.COMPLETE
            and within_window is True
        ):
            return PairStatus.COMPLETE
        return PairStatus.PARTIAL

    @staticmethod
    def _pair_wait(first: ArmRecord, second: ArmRecord) -> None:
        if first.invocation_end_monotonic_s is None or second.invocation_end_monotonic_s is None:
            return
        if first.invocation_end_monotonic_s <= second.invocation_start_monotonic_s:
            first.pair_wait_s = max(0.0, second.invocation_end_monotonic_s - first.invocation_end_monotonic_s)
        elif second.invocation_end_monotonic_s <= first.invocation_start_monotonic_s:
            second.pair_wait_s = max(0.0, first.invocation_end_monotonic_s - second.invocation_end_monotonic_s)

    @staticmethod
    def _both_invoked_terminal(pair: PairRecord) -> bool:
        return all(
            record.invocation_start_monotonic_s is not None
            and record.invocation_end_monotonic_s is not None
            for record in pair.arms.values()
        )

    def _run_lookups(self, pair: PairRecord) -> None:
        # The shared budget begins when lookup work can actually begin, after both
        # terminal primary states. Do not anchor it to an earlier response end.
        budget_start = self.clock.monotonic()
        budget_start_utc = utc_text(self.clock.utcnow())
        deadline = budget_start + self.lookup_budget_s
        start_dt = datetime.fromisoformat(budget_start_utc.replace("Z", "+00:00"))
        deadline_utc = utc_text(start_dt + timedelta(seconds=self.lookup_budget_s))
        pair.lookup_budget_start_monotonic_s = budget_start
        pair.lookup_deadline_monotonic_s = deadline
        pair.lookup_budget_start_utc = budget_start_utc
        pair.lookup_deadline_utc = deadline_utc
        if self.lookup is None:
            for record in pair.arms.values():
                for slot in record.slots:
                    if slot.status is SlotStatus.OCCUPIED:
                        slot.detail = LookupRecord(kind="detail", status=LookupStatus.FAILED, error_category="lookup_handler_unavailable")
                        slot.audit = LookupRecord(kind="audit", status=LookupStatus.FAILED, error_category="lookup_handler_unavailable")
            return

        review_stop = False
        last_lookup_time = budget_start
        for arm in pair.arm_order:
            arm_record = pair.arms[arm]
            for slot in arm_record.slots:
                if slot.status is not SlotStatus.OCCUPIED:
                    continue
                candidate_id = slot.stable_id
                assert candidate_id is not None and slot.rank is not None
                for kind in ("detail", "audit"):
                    if review_stop:
                        lookup_record = LookupRecord(
                            kind=kind,
                            status=LookupStatus.NOT_STARTED_AFTER_REVIEW_STOP,
                            error_category="review_required_after_unconfirmed_termination",
                        )
                    elif self.clock.monotonic() >= deadline:
                        lookup_record = LookupRecord(
                            kind=kind,
                            status=LookupStatus.NOT_STARTED_AFTER_DEADLINE,
                            error_category="shared_lookup_deadline_reached",
                        )
                    else:
                        lookup_record = self._do_lookup(
                            LookupRequest(
                                arm=arm,
                                candidate_id=candidate_id,
                                rank=slot.rank,
                                kind=kind,
                                arm_content_hash=slot.arm_content_hash,
                                arm_revision=slot.arm_revision,
                            ),
                            deadline,
                        )
                        if (
                            lookup_record.termination_status
                            in {TerminationStatus.NOT_CONFIRMED, TerminationStatus.UNKNOWN}
                            and (lookup_record.timeout or lookup_record.status is LookupStatus.FAILED)
                        ):
                            review_stop = True
                            pair.run_stop_required = True
                            pair.run_stop_reason = "review_required_after_unconfirmed_lookup_termination"
                    if kind == "detail":
                        slot.detail = lookup_record
                    else:
                        slot.audit = lookup_record
                    if lookup_record.start_monotonic_s is not None and lookup_record.end_monotonic_s is not None:
                        last_lookup_time = max(last_lookup_time, lookup_record.end_monotonic_s)
                        arm_record.evidence_retrieval_duration_s += max(
                            0.0, lookup_record.end_monotonic_s - lookup_record.start_monotonic_s
                        )
        pair.evidence_retrieval_duration_s = max(0.0, last_lookup_time - budget_start)

    def _do_lookup(self, request: LookupRequest, deadline: float) -> LookupRecord:
        assert self.lookup is not None
        start_utc = utc_text(self.clock.utcnow())
        start = self.clock.monotonic()
        if start >= deadline:
            return LookupRecord(
                kind=request.kind,
                status=LookupStatus.NOT_STARTED_AFTER_DEADLINE,
                error_category="shared_lookup_deadline_reached",
            )
        try:
            result = self.lookup(replace(request, timeout_cap_s=deadline - start))
            if not isinstance(result, LookupObservation):
                raise TypeError("lookup handler must return LookupObservation")
            if not isinstance(result.termination_status, TerminationStatus):
                raise TypeError("lookup handler returned an invalid termination status")
            if (
                not isinstance(result.success, bool)
                or not isinstance(result.identity_linkage, LinkageStatus)
                or not isinstance(result.revision_linkage, LinkageStatus)
                or not isinstance(result.response_metadata, Mapping)
                or any(
                    value is not None and not isinstance(value, str)
                    for value in (
                        result.returned_candidate_id,
                        result.content_hash,
                        result.revision,
                        result.error_category,
                        result.audit_status,
                    )
                )
                or any(
                    value is not None and not isinstance(value, bool)
                    for value in (result.audit_matches_revision, result.detail_sufficient_for_relevance)
                )
                or (
                    result.http_status is not None
                    and (not isinstance(result.http_status, int) or isinstance(result.http_status, bool))
                )
            ):
                raise TypeError("lookup handler returned malformed observation fields")
        except InvocationFailure as exc:
            end = self.clock.monotonic()
            timed_out = exc.category in {"lookup_timeout", "harness_timeout", "timeout"}
            return LookupRecord(
                kind=request.kind,
                status=LookupStatus.FAILED,
                attempt_count=1,
                start_monotonic_s=start,
                end_monotonic_s=end,
                start_utc=start_utc,
                end_utc=utc_text(self.clock.utcnow()),
                error_category=_safe_error_category(exc.category),
                timeout=timed_out,
                termination_status=(
                    exc.termination_status
                    if isinstance(exc.termination_status, TerminationStatus)
                    else TerminationStatus.UNKNOWN
                ),
                termination_evidence=_safe_text(exc.termination_evidence) or None,
                late=end > deadline,
            )
        except Exception as exc:  # noqa: BLE001 - keep failure separate from unknown evidence.
            end = self.clock.monotonic()
            return LookupRecord(
                kind=request.kind,
                status=LookupStatus.FAILED,
                attempt_count=1,
                start_monotonic_s=start,
                end_monotonic_s=end,
                start_utc=start_utc,
                end_utc=utc_text(self.clock.utcnow()),
                error_category=f"lookup_harness_failure:{type(exc).__name__}",
                termination_status=TerminationStatus.UNKNOWN,
                late=end > deadline,
            )
        end = self.clock.monotonic()
        late = end > deadline
        sensitive_values = (
            str(result.returned_candidate_id or ""),
            str(result.content_hash or ""),
            str(result.revision or ""),
            str(result.audit_status or ""),
            *[str(value) for value in result.response_metadata.values()],
        )
        if any(has_credential_like_text(value) for value in sensitive_values):
            return LookupRecord(
                kind=request.kind,
                status=LookupStatus.FAILED,
                attempt_count=1,
                start_monotonic_s=start,
                end_monotonic_s=end,
                start_utc=start_utc,
                end_utc=utc_text(self.clock.utcnow()),
                error_category="sensitive_lookup_metadata_redacted",
                termination_status=result.termination_status,
                termination_evidence=_safe_text(result.termination_evidence),
                late=late,
            )
        successful = result.success
        if result.returned_candidate_id is None:
            identity_linkage = LinkageStatus.UNVERIFIED
        elif result.returned_candidate_id != request.candidate_id:
            identity_linkage = LinkageStatus.MISMATCH
        else:
            identity_linkage = LinkageStatus.MATCHED
        revision_linkage = result.revision_linkage
        if request.kind == "detail":
            observed_revisions = []
            if request.arm_content_hash:
                observed_revisions.append((request.arm_content_hash, result.content_hash))
            if request.arm_revision:
                observed_revisions.append((request.arm_revision, result.revision))
            if not observed_revisions:
                revision_linkage = LinkageStatus.NOT_EXPOSED
            elif any(observed is None for _expected, observed in observed_revisions):
                revision_linkage = LinkageStatus.UNVERIFIED
            elif any(expected != observed for expected, observed in observed_revisions):
                revision_linkage = LinkageStatus.MISMATCH
            else:
                revision_linkage = LinkageStatus.MATCHED
        return LookupRecord(
            kind=request.kind,
            status=LookupStatus.AVAILABLE if successful else LookupStatus.FAILED,
            attempt_count=1,
            start_monotonic_s=start,
            end_monotonic_s=end,
            start_utc=start_utc,
            end_utc=utc_text(self.clock.utcnow()),
            http_status=result.http_status,
            error_category=_safe_error_category(result.error_category) if result.error_category else None,
            termination_status=result.termination_status,
            termination_evidence=_safe_text(result.termination_evidence),
            late=late,
            returned_candidate_id=result.returned_candidate_id,
            content_hash=result.content_hash,
            revision=result.revision,
            identity_linkage=identity_linkage,
            revision_linkage=revision_linkage,
            audit_status=result.audit_status,
            audit_matches_revision=result.audit_matches_revision,
            detail_sufficient_for_relevance=(
                result.detail_sufficient_for_relevance if request.kind == "detail" else None
            ),
            response_metadata=_safe_metadata(result.response_metadata),
        )

    def _compute_evidence_ready(self, pair: PairRecord) -> None:
        for arm_record in pair.arms.values():
            if arm_record.status is not ArmStatus.COMPLETE or not arm_record.primary_response_valid:
                arm_record.tud_status = "incomplete"
                continue
            occupied = [slot for slot in arm_record.slots if slot.status is SlotStatus.OCCUPIED]
            if not occupied:
                arm_record.evidence_ready_monotonic_s = arm_record.invocation_end_monotonic_s
                arm_record.evidence_ready_utc = arm_record.invocation_end_utc
                arm_record.tud_s = arm_record.response_latency_s
                arm_record.tud_status = "complete"
                continue
            ready_slots = all(
                slot.detail is not None
                and slot.detail.status is LookupStatus.AVAILABLE
                and not slot.detail.late
                and slot.detail.identity_linkage is LinkageStatus.MATCHED
                and slot.detail.returned_candidate_id == slot.stable_id
                and (
                    slot.detail.revision_linkage is LinkageStatus.MATCHED
                    if slot.arm_content_hash or slot.arm_revision
                    else slot.detail.revision_linkage is LinkageStatus.NOT_EXPOSED
                )
                and (not slot.arm_content_hash or slot.detail.content_hash == slot.arm_content_hash)
                and (not slot.arm_revision or slot.detail.revision == slot.arm_revision)
                and slot.detail.detail_sufficient_for_relevance is True
                for slot in occupied
            )
            if ready_slots:
                arm_record.evidence_ready_monotonic_s = max(
                    [arm_record.invocation_end_monotonic_s or 0.0]
                    + [slot.detail.end_monotonic_s or 0.0 for slot in occupied if slot.detail]
                )
                ready_detail = max(
                    (slot.detail for slot in occupied if slot.detail),
                    key=lambda item: item.end_monotonic_s or 0.0,
                )
                arm_record.evidence_ready_utc = ready_detail.end_utc
                arm_record.tud_s = max(
                    0.0,
                    arm_record.evidence_ready_monotonic_s
                    - (arm_record.invocation_start_monotonic_s or arm_record.evidence_ready_monotonic_s),
                )
                arm_record.tud_status = "complete"
            else:
                arm_record.tud_status = "censored_or_incomplete"

        if any(record.invocation_start_monotonic_s is None for record in pair.arms.values()):
            pair.pair_tud_status = "not_assessable"
            return
        if all(record.tud_status == "complete" for record in pair.arms.values()):
            pair.pair_evidence_ready_monotonic_s = max(
                record.evidence_ready_monotonic_s or 0.0 for record in pair.arms.values()
            )
            pair.pair_tud_s = max(
                0.0,
                pair.pair_evidence_ready_monotonic_s
                - (pair.first_invocation_start_monotonic_s or pair.pair_evidence_ready_monotonic_s),
            )
            pair.pair_tud_status = "complete"
        else:
            pair.pair_tud_status = "censored_or_incomplete"

    def _uninvoked_pair(
        self,
        plan: PairPlan,
        context_hash: str,
        status: PairStatus,
        reason: str,
        run_id: str,
        dataset_sha256: str,
    ) -> PairRecord:
        arms = {
            arm: ArmRecord(
                arm=arm,
                status=ArmStatus.NOT_RUN if status is PairStatus.NOT_RUN else ArmStatus.BLOCKED,
                invocation_timeout_s=self.invocation_timeout_s,
                error_category=reason,
                underlying_termination=TerminationStatus.UNKNOWN,
                slots=_empty_slots(SlotStatus.NOT_RUN if status is PairStatus.NOT_RUN else SlotStatus.BLOCKED),
            )
            for arm in ArmName
        }
        return PairRecord(
            schema_version="skillscout-evaluation-v1",
            run_id=run_id,
            dataset_sha256=dataset_sha256,
            pair_number=plan.pair_number,
            pair_id=f"p{plan.pair_number:03d}-t{plan.task_index:02d}-r{plan.repetition}",
            task_id=plan.task_id,
            task_index=plan.task_index,
            repetition=plan.repetition,
            arm_order=plan.arm_order,
            context_manifest_sha256=context_hash,
            status=status,
            status_reason=reason,
            first_invocation_start_monotonic_s=None,
            second_invocation_start_monotonic_s=None,
            first_invocation_start_utc=None,
            second_invocation_start_utc=None,
            pairing_interval_s=None,
            pairing_within_window=None,
            arms=arms,
            pair_tud_status="not_assessable",
        )


def _finite_or_none(value: float | None) -> bool:
    return value is None or math.isfinite(value)
