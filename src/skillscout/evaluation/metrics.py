"""Protocol-defined metric derivations over restricted run records."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median
from typing import Any, Mapping, Sequence

from skillscout.evaluation.models import ArmStatus, DecisionConformity, LinkageStatus, LookupStatus, RelevanceLabel, ReviewResolution, SlotStatus
from skillscout.evaluation.protocol import (
    ArmName,
    ELIGIBLE_TASK_COUNT,
    PLANNED_PAIR_COUNT,
    PLANNED_SLOTS_PER_ARM,
    SLOTS_PER_ARM,
    INVOCATION_TIMEOUT_SECONDS,
    EvaluationTask,
)
from skillscout.evaluation.validation import validate_records


class MetricInputError(ValueError):
    """Run records cannot support the predeclared cohort estimand."""


def _value(value: Any) -> str | None:
    return None if value is None else str(getattr(value, "value", value))


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _arm(pair: Any, name: ArmName) -> Any:
    arms = _get(pair, "arms")
    if not isinstance(arms, Mapping):
        raise MetricInputError("pair has no arms mapping")
    return arms.get(name, arms.get(name.value))


def _confirmed_relevant(slot: Any, arm: Any) -> bool:
    if _value(_get(slot, "status")) != SlotStatus.OCCUPIED.value:
        return False
    if not _get(arm, "primary_response_valid", False):
        return False
    if not _confirmed_relevant_evidence(slot):
        return False
    return (
        _value(_get(slot, "relevance_label")) == RelevanceLabel.RELEVANT.value
        and _value(_get(slot, "review_resolution")) == ReviewResolution.RESOLVED.value
    )


def _slot_outcome(slot: Any, arm: Any) -> str:
    status = _value(_get(slot, "status"))
    if status == SlotStatus.EMPTY.value:
        return "empty"
    if status == SlotStatus.FAILED.value:
        return "failed"
    if status == SlotStatus.BLOCKED.value:
        return "blocked"
    if status == SlotStatus.NOT_RUN.value:
        return "not_run"
    if status == SlotStatus.UNKNOWN.value:
        return "unknown"
    if status != SlotStatus.OCCUPIED.value:
        return "unknown"
    label = _value(_get(slot, "relevance_label"))
    resolution = _value(_get(slot, "review_resolution"))
    if resolution != ReviewResolution.RESOLVED.value:
        return "unresolved_review"
    if label == RelevanceLabel.RELEVANT.value:
        return "relevant" if _confirmed_relevant(slot, arm) else "unknown"
    if label == RelevanceLabel.IRRELEVANT.value:
        return "irrelevant" if _confirmed_relevant_evidence(slot) else "unknown"
    return "unknown"


def _confirmed_relevant_evidence(slot: Any) -> bool:
    detail = _get(slot, "detail")
    if (
        detail is None
        or _value(_get(detail, "status")) != LookupStatus.AVAILABLE.value
        or _get(detail, "late", False)
        or _value(_get(detail, "identity_linkage")) != LinkageStatus.MATCHED.value
        or _get(detail, "returned_candidate_id") != _get(slot, "stable_id")
        or _get(detail, "detail_sufficient_for_relevance") is not True
    ):
        return False
    arm_hash = _get(slot, "arm_content_hash")
    arm_revision = _get(slot, "arm_revision")
    linkage = _value(_get(detail, "revision_linkage"))
    if arm_hash or arm_revision:
        return bool(
            linkage == LinkageStatus.MATCHED.value
            and (not arm_hash or _get(detail, "content_hash") == arm_hash)
            and (not arm_revision or _get(detail, "revision") == arm_revision)
        )
    return linkage == LinkageStatus.NOT_EXPOSED.value


def _all_occupied_evidence_complete(arm: Any) -> bool:
    if not _get(arm, "primary_response_valid", False):
        return False
    for slot in _get(arm, "slots", ()):
        if _value(_get(slot, "status")) == SlotStatus.OCCUPIED.value and not _confirmed_relevant_evidence(slot):
            return False
    return True


def _sensitivity_eligible(pair: Any) -> bool:
    if _value(_get(pair, "status")) != "complete" or _get(pair, "pairing_within_window") is not True:
        return False
    if _get(pair, "second_invocation_start_monotonic_s") is None:
        return False
    for arm_name in ArmName:
        arm = _arm(pair, arm_name)
        if arm is None or _value(_get(arm, "status")) != ArmStatus.COMPLETE.value:
            return False
        if _get(arm, "response_latency_s") is None or _get(arm, "response_latency_s") > INVOCATION_TIMEOUT_SECONDS:
            return False
        if not _all_occupied_evidence_complete(arm):
            return False
    return True


def _time_summary(
    values: Sequence[float | int | None],
    *,
    planned_n: int | None = None,
) -> dict[str, float | int | None]:
    present = [float(value) for value in values if value is not None]
    return {
        "n": len(present),
        "planned_n": planned_n,
        "missing_or_censored_n": max(0, planned_n - len(present)) if planned_n is not None else None,
        "mean_s": mean(present) if present else None,
        "median_s": median(present) if present else None,
    }


def compute_metrics(
    pairs: Sequence[Any],
    tasks: Sequence[EvaluationTask],
    *,
    decision_outcomes: Mapping[str, DecisionConformity | str] | None = None,
) -> dict[str, Any]:
    """Calculate the fixed-denominator primary estimand and separate summaries.

    ``pairs`` must contain all 42 planned opportunities, including not-run records.
    Candidate-level relevance annotations are expected to have been resolved and
    attached to each slot without altering operational or evidence fields.
    """
    if len(tasks) != ELIGIBLE_TASK_COUNT or len(pairs) != PLANNED_PAIR_COUNT:
        raise MetricInputError("metrics require the fixed 14-task, 42-pair cohort")
    validation_errors = validate_records(pairs, tasks)
    if validation_errors:
        raise MetricInputError("metrics require structurally valid fixed-schedule records: " + validation_errors[0])
    by_task: dict[str, list[Any]] = defaultdict(list)
    for pair in pairs:
        by_task[str(_get(pair, "task_id"))].append(pair)
    if set(by_task) != {task.task_id for task in tasks}:
        raise MetricInputError("pair task IDs do not match the approved eligible cohort")
    for task in tasks:
        repetitions = sorted(_get(pair, "repetition") for pair in by_task[task.task_id])
        if repetitions != [1, 2, 3]:
            raise MetricInputError(f"task {task.task_id} must have exactly repetitions 1, 2, and 3")

    confirmed_by_pair: dict[str, dict[ArmName, int]] = {}
    slot_counts: dict[ArmName, Counter[str]] = {arm: Counter() for arm in ArmName}
    arm_status_counts: dict[ArmName, Counter[str]] = {arm: Counter() for arm in ArmName}
    latency: dict[ArmName, list[float | None]] = {arm: [] for arm in ArmName}
    timing_components: dict[ArmName, dict[str, list[float | None]]] = {
        arm: {name: [] for name in ("evidence_retrieval_duration_s", "pair_wait_s", "tud_s")}
        for arm in ArmName
    }
    timeout_counts: dict[ArmName, Counter[str]] = {arm: Counter() for arm in ArmName}
    tud_status_counts: dict[ArmName, Counter[str]] = {arm: Counter() for arm in ArmName}
    evidence_counts: dict[ArmName, Counter[str]] = {arm: Counter() for arm in ArmName}
    coverage_counts: dict[ArmName, Counter[str]] = {arm: Counter() for arm in ArmName}
    primary_differences: dict[str, list[float]] = defaultdict(list)
    differences_by_repetition: dict[int, dict[str, float]] = defaultdict(dict)
    eligible_differences: dict[str, list[float]] = defaultdict(list)
    eligible_repetitions: Counter[str] = Counter()

    for pair in pairs:
        pair_id = str(_get(pair, "pair_id"))
        task_id = str(_get(pair, "task_id"))
        arm_counts: dict[ArmName, int] = {}
        arm_rates: dict[ArmName, float] = {}
        for arm_name in ArmName:
            arm = _arm(pair, arm_name)
            if arm is None:
                raise MetricInputError(f"pair {pair_id} is missing {arm_name.value}")
            status = _value(_get(arm, "status")) or "unknown"
            arm_status_counts[arm_name][status] += 1
            latency[arm_name].append(_get(arm, "response_latency_s"))
            tud_status_counts[arm_name][str(_get(arm, "tud_status", "unknown"))] += 1
            for metric_name in timing_components[arm_name]:
                timing_components[arm_name][metric_name].append(_get(arm, metric_name))
            timeout = _get(arm, "error_category") == "invocation_timeout" or _get(arm, "harness_outcome") == "timeout"
            timeout_counts[arm_name]["timeout"] += int(timeout)
            timeout_counts[arm_name]["invoked"] += int(_get(arm, "invocation_start_monotonic_s") is not None)
            confirmed = 0
            slot_list = list(_get(arm, "slots", ()))
            if len(slot_list) != SLOTS_PER_ARM:
                raise MetricInputError(f"pair {pair_id} has a non-three-slot {arm_name.value} record")
            for slot in slot_list:
                outcome = _slot_outcome(slot, arm)
                slot_counts[arm_name][outcome] += 1
                if _confirmed_relevant(slot, arm):
                    confirmed += 1
                if _value(_get(slot, "status")) == SlotStatus.OCCUPIED.value:
                    if _get(arm, "primary_response_valid", False):
                        evidence_counts[arm_name]["occupied_valid_primary"] += 1
                    if _confirmed_relevant_evidence(slot):
                        evidence_counts[arm_name]["evidence_available"] += 1
                    else:
                        detail = _get(slot, "detail")
                        if detail is None:
                            evidence_counts[arm_name]["missing"] += 1
                        elif _get(detail, "late", False):
                            evidence_counts[arm_name]["late"] += 1
                        elif _value(_get(detail, "status")) == LookupStatus.FAILED.value:
                            evidence_counts[arm_name]["failed"] += 1
                        elif _value(_get(detail, "identity_linkage")) == LinkageStatus.MISMATCH.value:
                            evidence_counts[arm_name]["mismatched"] += 1
                        elif _value(_get(detail, "identity_linkage")) != LinkageStatus.MATCHED.value:
                            evidence_counts[arm_name]["unlinked_or_unknown"] += 1
                        else:
                            evidence_counts[arm_name]["insufficient_or_unknown"] += 1
            arm_counts[arm_name] = confirmed
            arm_rates[arm_name] = confirmed / SLOTS_PER_ARM
            if confirmed > 0:
                coverage_counts[arm_name]["at_least_one_confirmed_relevant"] += 1
            elif status != ArmStatus.COMPLETE.value or not _get(arm, "primary_response_valid", False):
                coverage_counts[arm_name]["indeterminate"] += 1
            elif any(_slot_outcome(slot, arm) in {"unknown", "unresolved_review"} for slot in slot_list):
                coverage_counts[arm_name]["indeterminate"] += 1
            else:
                coverage_counts[arm_name]["none_confirmed_relevant"] += 1
        confirmed_by_pair[pair_id] = arm_counts
        primary_differences[task_id].append(
            arm_rates[ArmName.SKILLSCOUT] - arm_rates[ArmName.ORDINARY_SEARCH]
        )
        differences_by_repetition[int(_get(pair, "repetition"))][task_id] = (
            arm_rates[ArmName.SKILLSCOUT] - arm_rates[ArmName.ORDINARY_SEARCH]
        )
        if _sensitivity_eligible(pair):
            eligible_differences[task_id].append(
                arm_rates[ArmName.SKILLSCOUT] - arm_rates[ArmName.ORDINARY_SEARCH]
            )
            eligible_repetitions[task_id] += 1

    task_primary = {
        task.task_id: mean(primary_differences[task.task_id])
        for task in tasks
    }
    primary_delta = mean(task_primary.values())
    contributing = [task_id for task_id in (task.task_id for task in tasks) if eligible_differences[task_id]]
    task_sensitivity = {
        task_id: mean(eligible_differences[task_id]) for task_id in contributing
    }

    precision: dict[str, dict[str, int | float | None]] = {}
    for arm_name in ArmName:
        counts = slot_counts[arm_name]
        relevant = counts["relevant"]
        irrelevant = counts["irrelevant"]
        denom = relevant + irrelevant
        precision[arm_name.value] = {
            "relevant": relevant,
            "irrelevant": irrelevant,
            "denominator": denom,
            "precision": relevant / denom if denom else None,
            "unknown": counts["unknown"],
            "unresolved_review": counts["unresolved_review"],
        }

    outcomes = decision_outcomes or {}
    expected_pair_ids = {str(_get(pair, "pair_id")) for pair in pairs}
    if set(outcomes) - expected_pair_ids:
        raise MetricInputError("decision conformity mapping contains an unknown pair ID")
    valid_decision_outcomes = {item.value for item in DecisionConformity}
    if any(_value(value) not in valid_decision_outcomes for value in outcomes.values()):
        raise MetricInputError("decision conformity outcomes must use CORRECT, INCORRECT, or INDETERMINATE")
    decisions = Counter(
        _value(outcomes.get(str(_get(pair, "pair_id")))) or "not_assessed"
        for pair in pairs
    )
    pair_status_counts = Counter(_value(_get(pair, "status")) or "unknown" for pair in pairs)
    task_order_effects: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    no_skill_counts: dict[ArmName, Counter[str]] = {arm: Counter() for arm in ArmName}
    no_skill_task_ids = {"routine-typo", "routine-variable", "routine-unused-import", "routine-unit-test", "routine-readme-command", "weak-argparse-verbose", "weak-react-placeholder-avatar"}
    for pair in pairs:
        order = _get(pair, "arm_order")
        order_key = ">".join(_value(x) or "unknown" for x in order)
        task_id = str(_get(pair, "task_id"))
        pair_id = str(_get(pair, "pair_id"))
        a_counts = confirmed_by_pair[pair_id]
        difference = (a_counts[ArmName.SKILLSCOUT] - a_counts[ArmName.ORDINARY_SEARCH]) / SLOTS_PER_ARM
        task_order_effects[task_id][order_key].append(difference)
        if task_id in no_skill_task_ids:
            for arm_name in ArmName:
                arm = _arm(pair, arm_name)
                for slot in _get(arm, "slots", ()):
                    outcome = _slot_outcome(slot, arm)
                    if outcome in {"relevant", "irrelevant"}:
                        no_skill_counts[arm_name]["evaluable_occupied_slots"] += 1
                        no_skill_counts[arm_name]["irrelevant_confirmed_slots"] += int(outcome == "irrelevant")
                    elif _value(_get(slot, "status")) == SlotStatus.OCCUPIED.value or outcome in {"unknown", "unresolved_review"}:
                        no_skill_counts[arm_name]["unknown_or_unresolved_slots"] += 1

    return {
        "primary": {
            "name": "confirmed relevant-slot yield@3 per planned slot",
            "contrast": "skillscout_minus_ordinary_search",
            "delta": primary_delta,
            "fixed_denominator_per_arm": PLANNED_SLOTS_PER_ARM,
            "planned_slots_per_repetition": SLOTS_PER_ARM,
            "task_level_differences": task_primary,
            "task_weights_equal": True,
            "independent_interpretation_units": ELIGIBLE_TASK_COUNT,
            "repeated_pairs": PLANNED_PAIR_COUNT,
        },
        "complete_pair_sensitivity": {
            "eligible_repetitions_by_task": dict(eligible_repetitions),
            "contributing_tasks": len(contributing),
            "tasks_with_none": [task.task_id for task in tasks if not eligible_repetitions[task.task_id]],
            "task_weighted_delta": mean(task_sensitivity.values()) if task_sensitivity else None,
            "task_level_differences": task_sensitivity,
        },
        "slot_outcomes_fixed_126": {arm.value: dict(slot_counts[arm]) for arm in ArmName},
        "evaluable_slot_precision": precision,
        "evidence_availability": {arm.value: dict(evidence_counts[arm]) for arm in ArmName},
        "arm_status_counts_per_42_planned": {arm.value: dict(arm_status_counts[arm]) for arm in ArmName},
        "timeout_counts": {
            arm.value: {
                **dict(timeout_counts[arm]),
                "per_invoked_rate": timeout_counts[arm]["timeout"] / timeout_counts[arm]["invoked"]
                if timeout_counts[arm]["invoked"] else None,
                "per_planned_rate": timeout_counts[arm]["timeout"] / PLANNED_PAIR_COUNT,
            }
            for arm in ArmName
        },
        "pair_status_counts_per_42_planned": dict(pair_status_counts),
        "top_three_coverage": {arm.value: dict(coverage_counts[arm]) for arm in ArmName},
        "decision_conformity_skillscout_only": {
            "counts": dict(decisions),
            "n_assessed": sum(count for value, count in decisions.items() if value != "not_assessed"),
            "not_assessed": decisions["not_assessed"],
            "denominator": PLANNED_PAIR_COUNT,
        },
        "unnecessary_recommendations": {
            "by_arm": {
                arm.value: {
                    **dict(no_skill_counts[arm]),
                    "planned_slots_denominator": 7 * 3 * SLOTS_PER_ARM,
                }
                for arm in ArmName
            },
            "task_ids": sorted(no_skill_task_ids),
        },
        "relevant_skills_missed": {
            "status": "not_computable_without_a_predeclared_candidate_concept_crosswalk",
            "count": None,
        },
        "time_to_evidence_ready_status": {
            "by_arm": {arm.value: dict(tud_status_counts[arm]) for arm in ArmName},
            "by_pair": dict(Counter(str(_get(pair, "pair_tud_status", "unknown")) for pair in pairs)),
        },
        "latency_seconds": {
            arm.value: _time_summary(latency[arm], planned_n=PLANNED_PAIR_COUNT)
            for arm in ArmName
        },
        "timing_components_seconds": {
            arm.value: {
                name: _time_summary(values, planned_n=PLANNED_PAIR_COUNT)
                for name, values in timing_components[arm].items()
            }
            for arm in ArmName
        },
        "pair_timing_components_seconds": {
            "evidence_retrieval_duration_s": _time_summary([_get(pair, "evidence_retrieval_duration_s") for pair in pairs], planned_n=PLANNED_PAIR_COUNT),
            "time_to_evidence_ready_s": _time_summary([_get(pair, "pair_tud_s") for pair in pairs], planned_n=PLANNED_PAIR_COUNT),
            "pair_wait_s": _time_summary([
                _get(_arm(pair, ArmName.SKILLSCOUT), "pair_wait_s", 0.0)
                + _get(_arm(pair, ArmName.ORDINARY_SEARCH), "pair_wait_s", 0.0)
                for pair in pairs
            ], planned_n=PLANNED_PAIR_COUNT),
        },
        "repetition_and_arm_order_effects_descriptive": {
            "mean_paired_yield_difference_by_repetition": {
                str(repetition): mean(values.values()) if values else None
                for repetition, values in sorted(differences_by_repetition.items())
            },
            "by_task_and_arm_order": {
                task_id: {order: list(values) for order, values in by_order.items()}
                for task_id, by_order in task_order_effects.items()
            },
        },
    }
