"""Dataset access, input boundaries, and the immutable v1 arm schedule."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

DATASET_SHA256 = "6bcdd11f33f55193edd3808d02075a22dfa47f86333d099e8432e448010c3649"
DATASET_RELATIVE_PATH = Path("docs/evaluation/v1/tasks.jsonl")
ELIGIBLE_TASK_COUNT = 14
REPETITIONS = 3
PLANNED_PAIR_COUNT = ELIGIBLE_TASK_COUNT * REPETITIONS
SLOTS_PER_ARM = 3
PLANNED_SLOTS_PER_ARM = ELIGIBLE_TASK_COUNT * REPETITIONS * SLOTS_PER_ARM
INVOCATION_TIMEOUT_SECONDS = 240
PAIRING_WINDOW_SECONDS = 300
LOOKUP_BUDGET_SECONDS = 600
EXCLUDED_TASK_ID = "avoid-suspicious-deploy-skill"


class ArmName(StrEnum):
    SKILLSCOUT = "skillscout"
    ORDINARY_SEARCH = "ordinary_search"


@dataclass(frozen=True, slots=True)
class EvaluationTask:
    """Evaluator-side row. Never serialize this object as a system input."""

    task_id: str
    task_text: str
    category: str
    expected_decision: str
    human_relevance_judgment: dict[str, Any]
    project_context: str
    eligible_index: int

    @property
    def relevance_criteria(self) -> tuple[str, ...]:
        criteria = self.human_relevance_judgment.get("criteria", [])
        return tuple(item for item in criteria if isinstance(item, str))


@dataclass(frozen=True, slots=True)
class TaskDataset:
    sha256: str
    all_tasks: tuple[EvaluationTask, ...]
    eligible_tasks: tuple[EvaluationTask, ...]


@dataclass(frozen=True, slots=True)
class PairPlan:
    pair_number: int
    task_id: str
    task_index: int
    repetition: int
    arm_order: tuple[ArmName, ArmName]


ARM_ORDER_BY_TASK: tuple[tuple[ArmName, ArmName, ArmName], ...] = (
    (ArmName.SKILLSCOUT, ArmName.ORDINARY_SEARCH, ArmName.SKILLSCOUT),
    (ArmName.ORDINARY_SEARCH, ArmName.SKILLSCOUT, ArmName.ORDINARY_SEARCH),
    (ArmName.SKILLSCOUT, ArmName.SKILLSCOUT, ArmName.ORDINARY_SEARCH),
    (ArmName.SKILLSCOUT, ArmName.ORDINARY_SEARCH, ArmName.ORDINARY_SEARCH),
    (ArmName.ORDINARY_SEARCH, ArmName.SKILLSCOUT, ArmName.SKILLSCOUT),
    (ArmName.ORDINARY_SEARCH, ArmName.SKILLSCOUT, ArmName.SKILLSCOUT),
    (ArmName.SKILLSCOUT, ArmName.ORDINARY_SEARCH, ArmName.ORDINARY_SEARCH),
    (ArmName.SKILLSCOUT, ArmName.ORDINARY_SEARCH, ArmName.SKILLSCOUT),
    (ArmName.SKILLSCOUT, ArmName.ORDINARY_SEARCH, ArmName.ORDINARY_SEARCH),
    (ArmName.ORDINARY_SEARCH, ArmName.ORDINARY_SEARCH, ArmName.SKILLSCOUT),
    (ArmName.SKILLSCOUT, ArmName.ORDINARY_SEARCH, ArmName.SKILLSCOUT),
    (ArmName.ORDINARY_SEARCH, ArmName.SKILLSCOUT, ArmName.ORDINARY_SEARCH),
    (ArmName.SKILLSCOUT, ArmName.ORDINARY_SEARCH, ArmName.ORDINARY_SEARCH),
    (ArmName.ORDINARY_SEARCH, ArmName.SKILLSCOUT, ArmName.SKILLSCOUT),
)


class DatasetError(ValueError):
    """The immutable task dataset is missing, malformed, or has changed."""


def find_repository_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    for root in (candidate, *candidate.parents):
        if (root / DATASET_RELATIVE_PATH).is_file():
            return root
    raise DatasetError(f"could not find {DATASET_RELATIVE_PATH} from {candidate}")


def load_dataset(path: str | Path | None = None) -> TaskDataset:
    """Load evaluator records and verify the protected byte-for-byte dataset hash."""
    dataset_path = Path(path) if path is not None else find_repository_root() / DATASET_RELATIVE_PATH
    raw = dataset_path.read_bytes()
    observed_hash = hashlib.sha256(raw).hexdigest()
    if observed_hash != DATASET_SHA256:
        raise DatasetError(
            f"dataset SHA-256 mismatch: expected {DATASET_SHA256}, observed {observed_hash}"
        )
    try:
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetError(f"dataset is not valid UTF-8 JSONL: {exc}") from exc
    if len(rows) != 15:
        raise DatasetError(f"expected 15 authored rows including the excluded case, found {len(rows)}")
    ids = [row.get("id") for row in rows]
    if len(set(ids)) != len(ids) or EXCLUDED_TASK_ID not in ids:
        raise DatasetError("dataset task IDs are duplicated or the fixed exclusion is absent")

    tasks: list[EvaluationTask] = []
    eligible_index = 0
    required = {"id", "task", "category", "expected_decision", "human_relevance_judgment", "project_context"}
    for row in rows:
        if not required.issubset(row):
            raise DatasetError(f"task row is missing required evaluator fields: {sorted(required - row.keys())}")
        if not all(isinstance(row[name], str) for name in ("id", "task", "category", "expected_decision", "project_context")):
            raise DatasetError("task IDs, task text, categories, decisions, and contexts must be strings")
        judgment = row["human_relevance_judgment"]
        if not isinstance(judgment, dict) or not isinstance(judgment.get("criteria"), list):
            raise DatasetError(f"task {row['id']} has malformed relevance criteria")
        if row["id"] == EXCLUDED_TASK_ID:
            continue
        eligible_index += 1
        tasks.append(
            EvaluationTask(
                task_id=row["id"],
                task_text=row["task"],
                category=row["category"],
                expected_decision=row["expected_decision"],
                human_relevance_judgment=judgment,
                project_context=row["project_context"],
                eligible_index=eligible_index,
            )
        )
    if eligible_index != ELIGIBLE_TASK_COUNT:
        raise DatasetError(f"expected 14 eligible tasks, found {eligible_index}")
    return TaskDataset(observed_hash, tuple(_tasks_from_rows(rows)), tuple(tasks))


def _tasks_from_rows(rows: list[dict[str, Any]]) -> list[EvaluationTask]:
    """Keep the excluded synthetic case evaluator-side without assigning it an index."""
    result: list[EvaluationTask] = []
    index = 0
    for row in rows:
        if row["id"] != EXCLUDED_TASK_ID:
            index += 1
        result.append(
            EvaluationTask(
                task_id=row["id"],
                task_text=row["task"],
                category=row["category"],
                expected_decision=row["expected_decision"],
                human_relevance_judgment=row["human_relevance_judgment"],
                project_context=row["project_context"],
                eligible_index=index if row["id"] != EXCLUDED_TASK_ID else 0,
            )
        )
    return result


def build_schedule(tasks: tuple[EvaluationTask, ...] | list[EvaluationTask]) -> tuple[PairPlan, ...]:
    """Return the approved repetition-major 42-pair schedule without rebalancing."""
    if len(tasks) != ELIGIBLE_TASK_COUNT:
        raise DatasetError(f"schedule requires exactly {ELIGIBLE_TASK_COUNT} eligible tasks")
    plans: list[PairPlan] = []
    for repetition in range(1, REPETITIONS + 1):
        for task in tasks:
            order = ARM_ORDER_BY_TASK[task.eligible_index - 1][repetition - 1]
            plans.append(
                PairPlan(
                    pair_number=len(plans) + 1,
                    task_id=task.task_id,
                    task_index=task.eligible_index,
                    repetition=repetition,
                    arm_order=(order, _other_arm(order)),
                )
            )
    if len(plans) != PLANNED_PAIR_COUNT:
        raise DatasetError("fixed schedule construction did not produce 42 pairs")
    return tuple(plans)


def _other_arm(arm: ArmName) -> ArmName:
    return ArmName.ORDINARY_SEARCH if arm is ArmName.SKILLSCOUT else ArmName.SKILLSCOUT


def ordinary_search_input(task_text: str) -> dict[str, object]:
    """The baseline sees only exact task text and the approved limit."""
    return {"query": task_text, "limit": SLOTS_PER_ARM}


def skillscout_input(task_text: str, opaque_project_path: str | Path) -> dict[str, object]:
    """SkillScraper sees task text and a project path; evaluator row data is never copied."""
    return {"task": task_text, "project_path": str(opaque_project_path)}


def criteria_for_candidate_review(task: EvaluationTask) -> tuple[str, ...]:
    """Expose only predeclared criteria, never task-level expected labels."""
    return task.relevance_criteria
