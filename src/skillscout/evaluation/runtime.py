"""Process-isolated adapter for real SkillScout and catalog operations.

The adapter is intentionally not invoked by the offline CLI. It bridges the
production application operations to the recorder callback interfaces while
keeping blocking HTTP work outside the recorder process.
"""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

from skillscout.evaluation.models import (
    CandidateObservation,
    Decision,
    InvocationFailure,
    InvocationNotStarted,
    InvocationReturn,
    LinkageStatus,
    PrimaryResponse,
    TerminationStatus,
)
from skillscout.evaluation.protocol import ArmName
from skillscout.evaluation.recorder import InvocationHandlers, LookupObservation, LookupRequest

_MAX_WORKER_RESPONSE_BYTES = 1_000_000
_SAFE_CATEGORY = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")


class RuntimeAdapter:
    """Adapt production operations to the recorder with hard local timeouts.

    Each operation runs in a fresh process. A timeout kills the worker process
    group, records upstream termination as ``not_confirmed``, and quarantines
    this adapter. Killing a client process cannot prove that the remote service
    cancelled work, so the recorder's review-stop rule still applies.
    """

    def __init__(
        self,
        *,
        worker_command: Sequence[str] | None = None,
        terminate_grace_s: float = 0.25,
    ) -> None:
        if not math.isfinite(terminate_grace_s) or terminate_grace_s < 0:
            raise ValueError("terminate_grace_s must be a finite non-negative number")
        self.worker_command = tuple(
            worker_command or (sys.executable, "-m", "skillscout.evaluation.runtime_worker")
        )
        if not self.worker_command or any(not isinstance(part, str) or not part for part in self.worker_command):
            raise ValueError("worker_command must be a non-empty sequence of command arguments")
        self.terminate_grace_s = terminate_grace_s
        self._lock = threading.Lock()
        self._quarantined = False
        self._last_worker_exitcode: int | None = None
        self._last_local_termination_confirmed: bool | None = None
        self.handlers = InvocationHandlers(
            skillscout=self.invoke_skillscout,
            ordinary_search=self.invoke_ordinary_search,
            deadline_aware=frozenset(ArmName),
            production_runtime=True,
        )
        self.lookup = self.lookup_evidence

    @property
    def quarantined(self) -> bool:
        return self._quarantined

    @property
    def last_worker_exitcode(self) -> int | None:
        return self._last_worker_exitcode

    @property
    def last_local_termination_confirmed(self) -> bool | None:
        """Whether the last local worker cleanup was observed complete."""
        return self._last_local_termination_confirmed

    def invoke_skillscout(
        self,
        payload: dict[str, object],
        timeout_s: int,
        *,
        not_after_monotonic_s: float | None = None,
        invocation_deadline_monotonic_s: float | None = None,
    ) -> InvocationReturn:
        self._require_keys(payload, {"task", "project_path"}, "SkillScout")
        result = self._execute(
            "skillscout",
            payload,
            timeout_s=float(timeout_s),
            dispatch_deadline=not_after_monotonic_s,
            deadline_inclusive=True,
            invocation_deadline=invocation_deadline_monotonic_s,
        )
        try:
            adapted = _invocation_from_result(result)
        except InvocationFailure:
            self._quarantined = True
            raise
        if adapted.termination_status in {TerminationStatus.NOT_CONFIRMED, TerminationStatus.UNKNOWN}:
            self._quarantined = True
        return adapted

    def invoke_ordinary_search(
        self,
        payload: dict[str, object],
        timeout_s: int,
        *,
        not_after_monotonic_s: float | None = None,
        invocation_deadline_monotonic_s: float | None = None,
    ) -> InvocationReturn:
        self._require_keys(payload, {"query", "limit"}, "ordinary-search")
        if payload.get("limit") != 3:
            raise ValueError("ordinary-search arm must request exactly three results")
        result = self._execute(
            "ordinary_search",
            payload,
            timeout_s=float(timeout_s),
            dispatch_deadline=not_after_monotonic_s,
            deadline_inclusive=True,
            invocation_deadline=invocation_deadline_monotonic_s,
        )
        try:
            adapted = _invocation_from_result(result)
        except InvocationFailure:
            self._quarantined = True
            raise
        if adapted.termination_status in {TerminationStatus.NOT_CONFIRMED, TerminationStatus.UNKNOWN}:
            self._quarantined = True
        return adapted

    def lookup_evidence(self, request: LookupRequest) -> LookupObservation:
        if request.kind not in {"detail", "audit"}:
            raise ValueError("lookup kind must be detail or audit")
        if request.timeout_cap_s is None or request.timeout_cap_s <= 0:
            raise InvocationNotStarted("lookup_deadline_expired_before_dispatch")
        deadline = request.deadline_monotonic_s
        if deadline is not None and time.monotonic() >= deadline:
            raise InvocationNotStarted("lookup_deadline_expired_before_dispatch")
        payload = {
            "arm": request.arm.value,
            "candidate_id": request.candidate_id,
            "rank": request.rank,
            "kind": request.kind,
            "arm_content_hash": request.arm_content_hash,
            "arm_revision": request.arm_revision,
        }
        result = self._execute(
            f"{request.kind}_lookup",
            payload,
            timeout_s=float(request.timeout_cap_s),
            lookup_deadline=deadline,
        )
        try:
            adapted = _lookup_from_result(result)
        except InvocationFailure:
            self._quarantined = True
            raise
        if adapted.termination_status in {TerminationStatus.NOT_CONFIRMED, TerminationStatus.UNKNOWN}:
            self._quarantined = True
        return adapted

    def _execute(
        self,
        operation: str,
        payload: Mapping[str, object],
        *,
        timeout_s: float,
        dispatch_deadline: float | None = None,
        deadline_inclusive: bool = False,
        invocation_deadline: float | None = None,
        lookup_deadline: float | None = None,
    ) -> dict[str, Any]:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise InvocationNotStarted(f"{operation}_deadline_expired_before_dispatch")
        now = time.monotonic()
        if dispatch_deadline is not None and (
            now > dispatch_deadline if deadline_inclusive else now >= dispatch_deadline
        ):
            raise InvocationNotStarted("pairing_deadline_expired_before_second_arm_start")
        if lookup_deadline is not None and now >= lookup_deadline:
            raise InvocationNotStarted("lookup_deadline_expired_before_dispatch")
        if invocation_deadline is not None and now >= invocation_deadline:
            raise InvocationFailure(
                "invocation_timeout",
                termination_status=TerminationStatus.CONFIRMED,
                termination_evidence="worker was not dispatched before the invocation cap; no request was launched",
                harness_outcome="harness_timeout",
            )
        if not self._lock.acquire(blocking=False):
            self._quarantined = True
            raise InvocationFailure(
                "runtime_adapter_busy",
                termination_status=TerminationStatus.UNKNOWN,
                termination_evidence="another runtime worker is still active; no overlapping worker was dispatched",
            )
        process: subprocess.Popen[bytes] | None = None
        try:
            if self._quarantined:
                raise InvocationFailure(
                    "runtime_adapter_quarantined",
                    termination_status=TerminationStatus.NOT_CONFIRMED,
                    termination_evidence="adapter is quarantined after an earlier unconfirmed operation",
                )
            now = time.monotonic()
            if dispatch_deadline is not None and (
                now > dispatch_deadline if deadline_inclusive else now >= dispatch_deadline
            ):
                raise InvocationNotStarted("pairing_deadline_expired_before_second_arm_start")
            if lookup_deadline is not None and now >= lookup_deadline:
                raise InvocationNotStarted("lookup_deadline_expired_before_dispatch")
            if invocation_deadline is not None and now >= invocation_deadline:
                raise InvocationFailure(
                    "invocation_timeout",
                    termination_status=TerminationStatus.CONFIRMED,
                    termination_evidence="worker was not dispatched before the invocation cap; no request was launched",
                    harness_outcome="harness_timeout",
                )
            if operation.endswith("_lookup") and lookup_deadline is not None:
                timeout_s = min(timeout_s, lookup_deadline - now)
            if timeout_s <= 0:
                raise InvocationNotStarted(f"{operation}_deadline_expired_before_dispatch")

            worker_deadline = now + timeout_s
            if dispatch_deadline is not None:
                worker_deadline = min(worker_deadline, dispatch_deadline)
            if invocation_deadline is not None:
                worker_deadline = min(worker_deadline, invocation_deadline)
            if lookup_deadline is not None:
                worker_deadline = min(worker_deadline, lookup_deadline)
            worker_request = {
                "operation": operation,
                "payload": dict(payload),
                "dispatch_deadline_monotonic_s": dispatch_deadline,
                "invocation_deadline_monotonic_s": invocation_deadline,
                "lookup_deadline_monotonic_s": lookup_deadline,
                "worker_deadline_monotonic_s": worker_deadline,
            }
            encoded = json.dumps(worker_request, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            kwargs: dict[str, Any] = {"start_new_session": os.name == "posix"}
            try:
                process = subprocess.Popen(
                    self.worker_command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    **kwargs,
                )
            except OSError as exc:
                self._last_local_termination_confirmed = None
                raise InvocationFailure(
                    "harness_start_failure",
                    termination_status=TerminationStatus.CONFIRMED,
                    termination_evidence=f"worker did not start ({type(exc).__name__}); no operation was dispatched",
                    harness_outcome="harness_failure",
                ) from None

            remaining = max(0.0, worker_deadline - time.monotonic())
            try:
                stdout, _ = process.communicate(input=encoded, timeout=remaining)
            except subprocess.TimeoutExpired:
                # If the worker has already exited, preserve its late terminal
                # response; the recorder will mark it supplemental by elapsed time.
                if process.poll() is not None:
                    stdout, _ = process.communicate()
                else:
                    local_stopped = self._terminate_worker(process)
                    self._last_worker_exitcode = process.poll()
                    self._last_local_termination_confirmed = local_stopped
                    self._quarantined = True
                    raise InvocationFailure(
                        "lookup_timeout" if operation.endswith("_lookup") else "invocation_timeout",
                        termination_status=TerminationStatus.NOT_CONFIRMED,
                        termination_evidence=(
                            "harness timeout; isolated local worker termination "
                            + ("confirmed" if local_stopped else "not confirmed")
                            + "; upstream request cancellation is not observable; adapter quarantined"
                        ),
                        harness_outcome="harness_timeout",
                    ) from None
            self._last_worker_exitcode = process.returncode
            if process.returncode != 0:
                self._quarantined = True
                raise InvocationFailure(
                    "worker_exit_failure",
                    termination_status=TerminationStatus.UNKNOWN,
                    termination_evidence="runtime worker exited unsuccessfully; upstream request termination is not known",
                    harness_outcome="harness_failure",
                )
            if len(stdout) > _MAX_WORKER_RESPONSE_BYTES:
                self._quarantined = True
                raise InvocationFailure(
                    "worker_response_too_large",
                    termination_status=TerminationStatus.UNKNOWN,
                    termination_evidence="worker exited, but its response exceeded the allowed bound",
                    harness_outcome="harness_failure",
                )
            try:
                decoded = json.loads(stdout.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._quarantined = True
                raise InvocationFailure(
                    "worker_protocol_error",
                    termination_status=TerminationStatus.UNKNOWN,
                    termination_evidence="worker exited without a valid structured response",
                    harness_outcome="harness_failure",
                ) from None
            if not isinstance(decoded, dict):
                self._quarantined = True
                raise InvocationFailure(
                    "worker_protocol_error",
                    termination_status=TerminationStatus.UNKNOWN,
                    termination_evidence="worker response was not an object",
                    harness_outcome="harness_failure",
                )
            status = decoded.get("status")
            if status == "not_started":
                raise InvocationNotStarted(_safe_category(decoded.get("category")))
            if status == "error":
                category = _safe_category(decoded.get("category"))
                termination = _termination_status(decoded.get("termination_status"))
                if termination in {TerminationStatus.NOT_CONFIRMED, TerminationStatus.UNKNOWN}:
                    self._quarantined = True
                raise InvocationFailure(
                    category,
                    termination_status=termination,
                    termination_evidence=_safe_worker_evidence(decoded.get("termination_evidence")),
                    harness_outcome="terminal_error",
                )
            if status != "ok" or not isinstance(decoded.get("result"), dict):
                self._quarantined = True
                raise InvocationFailure(
                    "worker_protocol_error",
                    termination_status=TerminationStatus.UNKNOWN,
                    termination_evidence="worker response did not match the runtime protocol",
                    harness_outcome="harness_failure",
                )
            result = decoded["result"]
            if result.get("termination_status") != TerminationStatus.CONFIRMED.value:
                self._quarantined = True
            return result
        except InvocationFailure:
            raise
        except InvocationNotStarted:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            self._quarantined = True
            local_stopped = True if process is None else self._terminate_worker(process)
            self._last_local_termination_confirmed = local_stopped
            raise InvocationFailure(
                "harness_failure",
                termination_status=TerminationStatus.UNKNOWN,
                termination_evidence=f"runtime worker I/O failed ({type(exc).__name__}); adapter quarantined",
                harness_outcome="harness_failure",
            ) from None
        finally:
            if process is not None:
                if process.poll() is None:
                    self._terminate_worker(process)
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        stream.close()
            self._lock.release()

    def _terminate_worker(self, process: subprocess.Popen[bytes]) -> bool:
        if process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=self.terminate_grace_s)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=max(self.terminate_grace_s, 0.1))
                except subprocess.TimeoutExpired:
                    return False
        try:
            process.communicate()
        except (OSError, ValueError):
            pass
        return process.poll() is not None

    @staticmethod
    def _require_keys(payload: Mapping[str, object], required: set[str], arm: str) -> None:
        if set(payload) != required:
            raise ValueError(f"{arm} payload does not match the approved system-input boundary")


def _invocation_from_result(result: Mapping[str, Any]) -> InvocationReturn:
    try:
        decision_value = result.get("decision")
        decision = Decision(decision_value) if decision_value is not None else None
        candidates = tuple(
            CandidateObservation(
                stable_id=item["stable_id"],
                source_slug=item.get("source_slug"),
                rank=item["rank"],
                arm_content_hash=item.get("content_hash"),
                arm_revision=item.get("revision"),
                source_url=item.get("source_url"),
            )
            for item in result.get("candidates", [])
        )
        response = PrimaryResponse(
            candidates=candidates,
            decision=decision,
            partial=result.get("partial", False),
            internal_attempt_count=result.get("internal_attempt_count"),
        )
        termination = _termination_status(result.get("termination_status"))
        evidence = _safe_worker_evidence(result.get("termination_evidence"))
    except (KeyError, TypeError, ValueError):
        raise InvocationFailure(
            "worker_protocol_error",
            termination_status=TerminationStatus.UNKNOWN,
            termination_evidence="runtime worker returned malformed primary result fields",
            harness_outcome="harness_failure",
        ) from None
    return InvocationReturn(response, termination, evidence)


def _lookup_from_result(result: Mapping[str, Any]) -> LookupObservation:
    try:
        return LookupObservation(
            success=result["success"],
            returned_candidate_id=result.get("returned_candidate_id"),
            content_hash=result.get("content_hash"),
            revision=result.get("revision"),
            http_status=result.get("http_status"),
            error_category=result.get("error_category"),
            identity_linkage=LinkageStatus(result.get("identity_linkage", "not_applicable")),
            revision_linkage=LinkageStatus(result.get("revision_linkage", "not_applicable")),
            detail_sufficient_for_relevance=result.get("detail_sufficient_for_relevance"),
            audit_status=result.get("audit_status"),
            audit_matches_revision=result.get("audit_matches_revision"),
            response_metadata=result.get("response_metadata", {}),
            termination_status=_termination_status(result.get("termination_status")),
            termination_evidence=_safe_worker_evidence(result.get("termination_evidence")),
        )
    except (KeyError, TypeError, ValueError):
        raise InvocationFailure(
            "worker_protocol_error",
            termination_status=TerminationStatus.UNKNOWN,
            termination_evidence="runtime worker returned malformed lookup fields",
            harness_outcome="harness_failure",
        ) from None


def _termination_status(value: object) -> TerminationStatus:
    try:
        return TerminationStatus(value)
    except (TypeError, ValueError):
        return TerminationStatus.UNKNOWN


def _safe_category(value: object) -> str:
    text = str(value or "runtime_operation_error")
    return text[:100] if 1 <= len(text) <= 100 and all(char in _SAFE_CATEGORY for char in text) else "runtime_operation_error"


def _safe_worker_evidence(value: object) -> str:
    text = str(value or "runtime operation returned no termination evidence")
    # Worker messages are deliberately bounded and never include exception text.
    return text[:300]
