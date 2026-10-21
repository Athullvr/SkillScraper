"""Offline validation for complete planned runs and protocol-critical invariants."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from skillscout.evaluation.models import ArmStatus, PairStatus, SlotStatus, TerminationStatus
from skillscout.evaluation.protocol import (
    ArmName,
    DATASET_SHA256,
    INVOCATION_TIMEOUT_SECONDS,
    LOOKUP_BUDGET_SECONDS,
    PAIRING_WINDOW_SECONDS,
    PLANNED_PAIR_COUNT,
    SLOTS_PER_ARM,
    EvaluationTask,
    build_schedule,
)


def _get(record: Any, key: str, default: Any = None) -> Any:
    return record.get(key, default) if isinstance(record, Mapping) else getattr(record, key, default)


def _value(value: Any) -> str | None:
    return None if value is None else str(getattr(value, "value", value))


def _arm(pair: Any, arm_name: ArmName) -> Any:
    arms = _get(pair, "arms", {})
    return arms.get(arm_name, arms.get(arm_name.value))


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _review_stop_required(arm: Any) -> bool:
    category = str(_get(arm, "error_category") or "")
    harness = str(_get(arm, "harness_outcome") or "")
    termination = _value(_get(arm, "underlying_termination"))
    return (
        _value(_get(arm, "status")) == ArmStatus.FAILED.value
        and (category == "invocation_timeout" or harness == "harness_failure" or category.startswith("harness_failure"))
        and termination in {TerminationStatus.NOT_CONFIRMED.value, TerminationStatus.UNKNOWN.value}
    )


def validate_records(
    pairs: Sequence[Any],
    tasks: Sequence[EvaluationTask],
    *,
    dataset_sha256: str = DATASET_SHA256,
) -> list[str]:
    """Return all validation errors; an empty list means structurally valid data."""
    errors: list[str] = []
    if dataset_sha256 != DATASET_SHA256:
        errors.append("dataset hash does not equal the approved v1 hash")
    if len(pairs) != PLANNED_PAIR_COUNT:
        errors.append(f"expected {PLANNED_PAIR_COUNT} planned pair records, found {len(pairs)}")
        return errors
    try:
        schedule = build_schedule(tasks)
    except Exception as exc:  # noqa: BLE001 - convert bad inputs into validation findings.
        errors.append(f"cannot construct approved schedule: {type(exc).__name__}: {exc}")
        return errors

    first_review_stop_pair: int | None = None
    for expected, pair in zip(schedule, pairs, strict=True):
        pair_number = _get(pair, "pair_number")
        label = f"pair[{expected.pair_number}]"
        expected_id = f"p{expected.pair_number:03d}-t{expected.task_index:02d}-r{expected.repetition}"
        if pair_number != expected.pair_number:
            errors.append(f"{label}: pair number/order mismatch")
        if _get(pair, "pair_id") != expected_id:
            errors.append(f"{label}: pair ID mismatch")
        if _get(pair, "task_id") != expected.task_id or _get(pair, "task_index") != expected.task_index:
            errors.append(f"{label}: task identity/index differs from fixed schedule")
        if _get(pair, "repetition") != expected.repetition:
            errors.append(f"{label}: repetition differs from fixed schedule")
        if tuple(_value(value) for value in _get(pair, "arm_order", ())) != tuple(x.value for x in expected.arm_order):
            errors.append(f"{label}: prescribed arm order mismatch")
        if _get(pair, "dataset_sha256") != DATASET_SHA256:
            errors.append(f"{label}: dataset hash mismatch")
        if not re.fullmatch(r"[0-9a-f]{64}", str(_get(pair, "context_manifest_sha256") or "")):
            errors.append(f"{label}: context manifest hash is not lowercase SHA-256")

        arms = {name: _arm(pair, name) for name in ArmName}
        if any(value is None for value in arms.values()):
            errors.append(f"{label}: missing arm record")
            continue
        starts: dict[ArmName, float | None] = {}
        ends: dict[ArmName, float | None] = {}
        for arm_name, arm in arms.items():
            arm_label = f"{label}/{arm_name.value}"
            status = _value(_get(arm, "status"))
            starts[arm_name] = _get(arm, "invocation_start_monotonic_s")
            ends[arm_name] = _get(arm, "invocation_end_monotonic_s")
            start, end = starts[arm_name], ends[arm_name]
            start_utc = _get(arm, "invocation_start_utc")
            end_utc = _get(arm, "invocation_end_utc")
            if _get(arm, "invocation_timeout_s") != INVOCATION_TIMEOUT_SECONDS:
                errors.append(f"{arm_label}: invocation cap differs from 240 seconds")
            if (start is None) != (end is None):
                errors.append(f"{arm_label}: invocation start/end presence is inconsistent")
            if start is None:
                if start_utc is not None or end_utc is not None:
                    errors.append(f"{arm_label}: uninvoked arm must not have invented timestamps")
                if status not in {ArmStatus.NOT_RUN.value, ArmStatus.BLOCKED.value}:
                    errors.append(f"{arm_label}: non-invoked arm has status {status}")
            else:
                if not _num(start) or not _num(end) or float(end) < float(start):
                    errors.append(f"{arm_label}: invalid monotonic invocation interval")
                if not start_utc or not end_utc:
                    errors.append(f"{arm_label}: invoked arm is missing UTC audit timestamps")
                latency = _get(arm, "response_latency_s")
                if latency is not None and (not _num(latency) or float(latency) < 0):
                    errors.append(f"{arm_label}: invalid response latency")
                if status == ArmStatus.COMPLETE.value and _num(latency) and float(latency) > INVOCATION_TIMEOUT_SECONDS:
                    errors.append(f"{arm_label}: complete response exceeded the invocation cap")
            if _get(arm, "primary_response_valid", False):
                if status not in {ArmStatus.COMPLETE.value, ArmStatus.PARTIAL.value} or start is None or end is None:
                    errors.append(f"{arm_label}: valid primary response lacks a valid invoked state")
                if _value(_get(arm, "arm")) != arm_name.value:
                    errors.append(f"{arm_label}: arm name mismatch")
                latency = _get(arm, "response_latency_s")
                if not _num(latency) or float(latency) > INVOCATION_TIMEOUT_SECONDS:
                    errors.append(f"{arm_label}: primary response is not within the 240-second cap")
            elif _get(arm, "decision") is not None:
                errors.append(f"{arm_label}: decision is fabricated without a valid primary response")
            if arm_name is ArmName.ORDINARY_SEARCH and _get(arm, "decision") is not None:
                errors.append(f"{arm_label}: ordinary-search decision must be null")
            attempts = _get(arm, "internal_attempt_count")
            if attempts is not None:
                if not isinstance(attempts, int) or attempts < 1:
                    errors.append(f"{arm_label}: invalid observed attempt count")
                if arm_name is ArmName.ORDINARY_SEARCH and attempts != 1:
                    errors.append(f"{arm_label}: ordinary-search baseline must make one primary attempt")
                if arm_name is ArmName.SKILLSCOUT and attempts > 3:
                    errors.append(f"{arm_label}: observed SkillScout attempts exceed documented retry cap")

            slots = list(_get(arm, "slots", ()))
            if len(slots) != SLOTS_PER_ARM or [int(_get(slot, "position", -1)) for slot in slots] != [1, 2, 3]:
                errors.append(f"{arm_label}: must record exactly slots 1, 2, and 3")
                continue
            slot_statuses = [_value(_get(slot, "status")) for slot in slots]
            if status == ArmStatus.COMPLETE.value and any(x not in {SlotStatus.OCCUPIED.value, SlotStatus.EMPTY.value} for x in slot_statuses):
                errors.append(f"{arm_label}: complete response has a non-occupied/non-empty slot")
            if status == ArmStatus.PARTIAL.value and _get(arm, "primary_response_valid", False):
                if any(x == SlotStatus.EMPTY.value for x in slot_statuses):
                    errors.append(f"{arm_label}: missing positions in partial response must remain unknown")
            if status == ArmStatus.FAILED.value and _get(arm, "primary_response_valid", False):
                errors.append(f"{arm_label}: failed invocation cannot have a valid primary result")
            if status == ArmStatus.NOT_RUN.value and any(x != SlotStatus.NOT_RUN.value for x in slot_statuses):
                errors.append(f"{arm_label}: not-run arm slots must remain not_run")
            if status == ArmStatus.BLOCKED.value and any(x != SlotStatus.BLOCKED.value for x in slot_statuses):
                errors.append(f"{arm_label}: blocked arm slots must remain blocked")

            if _review_stop_required(arm):
                if first_review_stop_pair is None:
                    first_review_stop_pair = expected.pair_number
                second = expected.arm_order[1] if expected.arm_order[0] is arm_name else None
                if second is not None and _value(_get(arms[second], "status")) != ArmStatus.NOT_RUN.value:
                    errors.append(f"{label}: unconfirmed termination must stop the second invocation")

            if _get(arm, "error_category") == "invocation_timeout" or _get(arm, "harness_outcome") == "timeout":
                if status != ArmStatus.FAILED.value or _get(arm, "primary_response_valid", False):
                    errors.append(f"{arm_label}: timeout must be failed/incomplete, never a successful decision")

            for slot in slots:
                for key in ("detail", "audit"):
                    lookup = _get(slot, key)
                    if lookup is None:
                        continue
                    if _get(lookup, "attempt_count", 0) > 1:
                        errors.append(f"{arm_label}/slot{_get(slot, 'position')}/{key}: lookup retried")
                    lookup_start = _get(lookup, "start_monotonic_s")
                    lookup_end = _get(lookup, "end_monotonic_s")
                    if lookup_start is not None:
                        if not _num(lookup_start) or not _num(lookup_end) or lookup_end < lookup_start:
                            errors.append(f"{arm_label}/slot{_get(slot, 'position')}/{key}: invalid lookup interval")
                        deadline = _get(pair, "lookup_deadline_monotonic_s")
                        if deadline is None or lookup_start >= deadline:
                            errors.append(f"{arm_label}/slot{_get(slot, 'position')}/{key}: lookup started at/after deadline")
                        if lookup_end > deadline and not _get(lookup, "late", False):
                            errors.append(f"{arm_label}/slot{_get(slot, 'position')}/{key}: late response not marked supplemental")
                        if lookup_end <= deadline and _get(lookup, "late", False):
                            errors.append(f"{arm_label}/slot{_get(slot, 'position')}/{key}: response marked late before deadline")
        invoked = [name for name in ArmName if starts[name] is not None]
        if len(invoked) < 2:
            for arm in arms.values():
                for slot in _get(arm, "slots", ()):
                    if _get(slot, "detail") is not None or _get(slot, "audit") is not None:
                        errors.append(f"{label}: pair-level lookups are forbidden when an arm was not invoked")
                        break
        elif _get(pair, "lookup_budget_start_monotonic_s") is not None:
            lookup_start = _get(pair, "lookup_budget_start_monotonic_s")
            lookup_deadline = _get(pair, "lookup_deadline_monotonic_s")
            terminal_max = max(ends[name] for name in ArmName if ends[name] is not None)
            if not _num(lookup_start) or lookup_start < terminal_max:
                errors.append(f"{label}: shared lookup budget starts before both terminal primary states")
            if not _num(lookup_deadline) or abs(float(lookup_deadline) - float(lookup_start) - LOOKUP_BUDGET_SECONDS) > 1e-6:
                errors.append(f"{label}: shared lookup deadline is not exactly 600 seconds")

        first_arm, second_arm = expected.arm_order
        first_start, second_start = starts[first_arm], starts[second_arm]
        if first_start is None and second_start is not None:
            errors.append(f"{label}: second arm cannot start without the first arm")
        if first_start is not None and second_start is not None:
            interval = float(second_start) - float(first_start)
            if abs((_get(pair, "pairing_interval_s") or 0.0) - interval) > 1e-6:
                errors.append(f"{label}: recorded pairing interval differs from starts")
            within = interval <= PAIRING_WINDOW_SECONDS
            if _get(pair, "pairing_within_window") is not within:
                errors.append(f"{label}: pairing-window flag disagrees with monotonic starts")
            if interval > PAIRING_WINDOW_SECONDS:
                errors.append(f"{label}: second invocation started after the 300-second deadline")
        elif _get(pair, "pairing_interval_s") is not None or _get(pair, "pairing_within_window") is not None:
            errors.append(f"{label}: absent invocation must not have a fabricated pairing interval")

        statuses = [_value(_get(arms[arm], "status")) for arm in ArmName]
        pair_status = _value(_get(pair, "status"))
        if statuses == [ArmStatus.NOT_RUN.value, ArmStatus.NOT_RUN.value] and pair_status != PairStatus.NOT_RUN.value:
            errors.append(f"{label}: two not-run arms require pair status not_run")
        if statuses == [ArmStatus.BLOCKED.value, ArmStatus.BLOCKED.value] and pair_status != PairStatus.BLOCKED.value:
            errors.append(f"{label}: two blocked arms require pair status blocked")
        if (
            all(status == ArmStatus.FAILED.value for status in statuses)
            and all(value is not None for value in starts.values())
            and pair_status != PairStatus.FAILED.value
        ):
            errors.append(f"{label}: both invoked failed arms require pair status failed")
        if statuses == [ArmStatus.COMPLETE.value, ArmStatus.COMPLETE.value] and _get(pair, "pairing_within_window") is True:
            if pair_status != PairStatus.COMPLETE.value:
                errors.append(f"{label}: two complete timely arms require pair status complete")
        elif pair_status == PairStatus.COMPLETE.value:
            errors.append(f"{label}: complete pair status lacks two complete timely results")

        stop_flag = _get(pair, "run_stop_required", False)
        if stop_flag and first_review_stop_pair is None:
            first_review_stop_pair = expected.pair_number

    if first_review_stop_pair is not None:
        for pair in pairs[first_review_stop_pair:]:
            pair_number = _get(pair, "pair_number")
            for arm_name in ArmName:
                if _get(_arm(pair, arm_name), "invocation_start_monotonic_s") is not None:
                    errors.append(f"pair[{pair_number}]: invocation occurred after run stop pending review")
    return errors


def validate_document(document: Mapping[str, Any], tasks: Sequence[EvaluationTask]) -> list[str]:
    pairs = document.get("pairs")
    if not isinstance(pairs, list):
        return ["run document must contain a pairs array"]
    return validate_records(pairs, tasks, dataset_sha256=document.get("dataset_sha256", ""))
