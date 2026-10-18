from __future__ import annotations

import json
from pathlib import Path


def test_exploratory_task_dataset_is_complete_and_explicit_about_measurements():
    dataset_path = Path(__file__).parents[1] / "docs" / "evaluation" / "tasks.jsonl"
    tasks = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines()]
    required = {
        "id",
        "task",
        "project_context",
        "expected_outcome",
        "expected_rationale",
        "relevant_candidate_hypotheses",
        "skillscout_result",
        "ordinary_catalog_search_baseline",
        "better_decision",
        "runtime_ms",
        "optional_llm_cost_usd",
    }

    assert len(tasks) == 20
    assert len({task["id"] for task in tasks}) == 20
    assert all(required <= task.keys() for task in tasks)
    assert all(task["skillscout_result"] != "measured" for task in tasks)
    assert all(task["ordinary_catalog_search_baseline"] != "measured" for task in tasks)
