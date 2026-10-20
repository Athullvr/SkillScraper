from __future__ import annotations

import hashlib
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


def test_preregistered_v1_dataset_has_fixed_categories_and_human_judgments():
    dataset_path = Path(__file__).parents[1] / "docs" / "evaluation" / "v1" / "tasks.jsonl"
    tasks = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines()]
    categories = [task["category"] for task in tasks]

    assert len(tasks) == 15
    assert len({task["id"] for task in tasks}) == 15
    assert categories.count("routine_no_skill") == 5
    assert categories.count("skill_may_help") == 5
    assert categories.count("weak_or_incomplete_or_suspicious") == 5
    assert all(task["task"].strip() for task in tasks)
    assert all(task["human_relevance_judgment"]["criteria"] for task in tasks)
    assert all(task["human_relevance_judgment"]["generic_match_is_not_enough"] for task in tasks)
    assert all("skillscout_score" not in task for task in tasks)

    routine_tasks = [task for task in tasks if task["category"] == "routine_no_skill"]
    assert all(task["expected_decision"] == "NO_SKILL_NEEDED" for task in routine_tasks)

    synthetic = next(task for task in tasks if task["id"] == "avoid-suspicious-deploy-skill")
    assert synthetic["measurement_scope"] == "synthetic_policy_fixture_only"
    assert "not a real catalog skill" in synthetic["fixture_description"]

    results_path = dataset_path.with_name("results.json")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    assert results["dataset"]["task_count"] == len(tasks)
    assert results["dataset"]["sha256"] == hashlib.sha256(dataset_path.read_bytes()).hexdigest()
