from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from skillscout.evaluation import __main__ as evaluation_cli
from skillscout.evaluation.calibration import (
    CalibrationError,
    _canonical_json,
    _conformity,
    _diagnose,
    _integrity_hash,
    _quality_violations,
    compare_calibration_runs,
    compute_calibration_metrics,
    load_calibration_dataset,
    load_run_artifact,
    run_calibration,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "docs/calibration/v1/dataset.json"


def test_checked_in_dataset_is_versioned_and_keeps_indeterminate_cases_out_of_metrics():
    dataset, digest = load_calibration_dataset(DATASET)

    assert dataset["schema_version"] == "skillscout-calibration-dataset-v1"
    assert len(digest) == 64
    assert dataset["data_kind"] == "synthetic_smoke_test_only"
    assert sum(item["assessment_status"] == "incomplete" for item in dataset["examples"]) == 2
    assert sum(item["assessment_status"] == "ambiguous" for item in dataset["examples"]) == 1
    assert all(
        item["expected_decision"] is None
        for item in dataset["examples"]
        if item["assessment_status"] != "judgeable"
    )
    schema = json.loads((ROOT / "docs/calibration/v1/schema.json").read_text())
    assert schema["properties"]["schema_version"]["const"] == dataset["schema_version"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data["examples"][0].update(expected_decision="MAYBE"), "expected_decision"),
        (lambda data: data["examples"][0].update(id="not valid!"), "stable ID"),
        (
            lambda data: data["examples"][1]["search"].update(complete=False),
            "adequate coverage",
        ),
        (
            lambda data: data["examples"][1]["search"]["candidates"][0]["evidence"].update(
                content_hash=None
            ),
            "complete evidence",
        ),
        (
            lambda data: data["examples"][0]["search"].update(coverage_status="unknown"),
            "routine no-skill",
        ),
        (
            lambda data: data["examples"][0].update(expected_candidate_order=[{"nested": 1}]),
            "expected_candidate_order",
        ),
    ],
)
def test_invalid_dataset_examples_fail_clearly(tmp_path, mutate, message):
    data = json.loads(DATASET.read_text())
    mutate(data)
    fixture = tmp_path / "invalid.json"
    fixture.write_text(json.dumps(data))

    with pytest.raises(CalibrationError, match=message):
        load_calibration_dataset(fixture)


def test_dataset_rejects_credentials_duplicate_json_keys_and_path_traversal(tmp_path):
    data = json.loads(DATASET.read_text())
    data["examples"][0]["task"] = "Use API_KEY=secret-value in this task"
    credential_file = tmp_path / "credential.json"
    credential_file.write_text(json.dumps(data))
    with pytest.raises(CalibrationError, match="credential-like"):
        load_calibration_dataset(credential_file)

    duplicate_file = tmp_path / "duplicate.json"
    duplicate_file.write_text('{"schema_version":"one","schema_version":"two"}')
    with pytest.raises(CalibrationError, match="duplicate object keys"):
        load_calibration_dataset(duplicate_file)

    data = json.loads(DATASET.read_text())
    excerpt = data["examples"][1]["search"]["candidates"][0]["evidence"]["files"][0]
    excerpt["path"] = "../outside/SKILL.md"
    path_file = tmp_path / "path.json"
    path_file.write_text(json.dumps(data))
    with pytest.raises(CalibrationError, match="invalid evidence file path"):
        load_calibration_dataset(path_file)


def test_offline_pipeline_is_deterministic_and_does_not_make_http_requests(monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError("calibration attempted an HTTP request")

    monkeypatch.setattr(httpx.Client, "request", fail_network)
    first = run_calibration(DATASET)
    second = run_calibration(DATASET)

    assert first == second
    assert first["configuration"]["network_access"] == "disabled_by_construction"
    assert first["configuration"]["optional_llm"] == "disabled"
    assert first["metrics"]["counts"] == {
        "examples": 12,
        "judgeable": 9,
        "incomplete": 2,
        "ambiguous": 1,
        "human_reviewed_judgeable": 0,
        "synthetic_judgeable": 9,
        "pending_review": 3,
    }
    assert first["metrics"]["human_reviewed"]["accuracy"]["value"] is None
    assert first["metrics"]["synthetic_smoke_test_only"]["accuracy"]["value"] == 1.0
    assert first["metrics"]["by_challenge_category"]["title_only_overlap"]["synthetic_smoke_test_only"]["judgeable_examples"] == 1
    assert first["examples"][0]["trace"]["search"]["called"] is False
    by_id = {item["example_id"]: item for item in first["examples"]}
    assert by_id["title-only-oauth"]["observed_decision"] == "NO_SKILL_NEEDED"
    assert by_id["documented-oauth-use"]["observed_decision"] == "USE"
    assert by_id["incomplete-search"]["observed_decision"] == "REVIEW"
    assert by_id["incomplete-search"]["diagnostic"]["likely_stages"] == [
        "candidate_retrieval_or_search_completeness"
    ]
    assert by_id["rank-by-documented-capability"]["observed_candidate_order"] == by_id[
        "rank-by-documented-capability"
    ]["expected_candidate_order"]


def test_stage_diagnostic_uses_trace_evidence_and_marks_explanation_stage_unknown():
    dataset, _ = load_calibration_dataset(DATASET)
    run = run_calibration(DATASET)
    example = next(item for item in dataset["examples"] if item["id"] == "title-only-oauth")
    record = copy.deepcopy(next(item for item in run["examples"] if item["example_id"] == example["id"]))
    record["observed_decision"] = "USE"
    record["trace"]["candidates"][0]["decision"] = "USE"

    diagnostic = _diagnose(example, record)

    assert diagnostic["status"] == "incorrect"
    assert diagnostic["likely_stages"] == ["operation_evidence_detection"]
    assert diagnostic["explanation_generation"]["status"] == "unknown"


def test_metrics_have_explicit_prediction_denominators_and_safety_rates():
    records = [
        _record("use-correct", "USE", "USE", "human_reviewed"),
        _record("use-false", "AVOID", "USE", "human_reviewed", capability=False),
        _record("no-correct", "NO_SKILL_NEEDED", "NO_SKILL_NEEDED", "human_reviewed"),
        _record("no-false", "USE", "NO_SKILL_NEEDED", "human_reviewed"),
        _record("review-extra", "USE", "REVIEW", "human_reviewed"),
        _record("review-needed", "REVIEW", "REVIEW", "human_reviewed"),
        _record("avoid-correct", "AVOID", "AVOID", "human_reviewed"),
        _record(
            "incomplete-no",
            "REVIEW",
            "NO_SKILL_NEEDED",
            "human_reviewed",
            complete=False,
            status="incomplete",
        ),
        _record("ambiguous", None, "REVIEW", "pending", status="ambiguous"),
    ]

    metrics = compute_calibration_metrics(records)
    human = metrics["human_reviewed"]

    assert human["judgeable_examples"] == 7
    assert human["accuracy"] == {"value": 4 / 7, "numerator": 4, "denominator": 7}
    assert human["per_decision"]["USE"]["precision"] == {
        "value": 0.5,
        "numerator": 1,
        "denominator": 2,
    }
    assert human["false_use_rate"] == {"value": 0.5, "numerator": 1, "denominator": 2}
    assert human["false_use_share_of_judgeable_examples"] == {
        "value": 1 / 7,
        "numerator": 1,
        "denominator": 7,
    }
    assert human["false_no_skill_needed_rate"] == {
        "value": 1 / 2,
        "numerator": 1,
        "denominator": 2,
    }
    assert human["unnecessary_review_rate"] == {
        "value": 1 / 2,
        "numerator": 1,
        "denominator": 2,
    }
    assert metrics["counts"]["ambiguous"] == 1
    assert metrics["counts"]["incomplete"] == 1
    assert metrics["safety_and_evidence_checks"]["candidate_uses_without_confirmed_capability"] == {
        "count": 1,
        "denominator": 2,
        "rate": 0.5,
    }
    assert metrics["safety_and_evidence_checks"]["no_skill_decisions_after_incomplete_search"]["example_ids"] == [
        "incomplete-no"
    ]
    assert metrics["by_challenge_category"]["synthetic_or_reviewed"]["human_reviewed"]["accuracy"]["denominator"] == 7


def test_run_artifacts_and_compare_detect_decision_ranking_and_safety_regressions():
    baseline = run_calibration(DATASET)
    baseline = copy.deepcopy(baseline)
    by_id = {item["example_id"]: item for item in baseline["examples"]}
    _set_observed_decision(by_id["documented-oauth-use"], "NO_SKILL_NEEDED")
    _set_observed_decision(by_id["title-only-oauth"], "USE")
    _finalize_run(baseline)

    candidate = copy.deepcopy(baseline)
    by_id = {item["example_id"]: item for item in candidate["examples"]}
    _set_observed_decision(by_id["documented-oauth-use"], "USE")
    critical = by_id["critical-safety-evidence"]
    _set_observed_decision(critical, "USE")
    critical["trace"]["candidates"][0]["decision"] = "USE"
    _set_observed_decision(by_id["title-only-oauth"], "NO_SKILL_NEEDED")
    ranking = by_id["rank-by-documented-capability"]
    ranking["observed_candidate_order"].reverse()
    _finalize_run(candidate)

    comparison = compare_calibration_runs(baseline, candidate)

    assert comparison["metric_changes"]["synthetic_smoke_test_accuracy_not_quality_evidence"]["delta"] == pytest.approx(1 / 9)
    assert comparison["metric_changes"]["decision_metrics"]["synthetic_smoke_test_only"]["false_use_rate"] == {
        "baseline": 0.5,
        "candidate": pytest.approx(1 / 3),
        "delta": pytest.approx(-1 / 6),
        "baseline_denominator": 2,
        "candidate_denominator": 3,
    }
    assert comparison["ranking_changed_without_decision_change"][0]["example_id"] == "rank-by-documented-capability"
    assert any(item["example_id"] == "critical-safety-evidence" for item in comparison["decision_changes"])
    assert any(
        item["example_id"] == "critical-safety-evidence"
        for item in comparison["safety_or_evidence_regressions"]
    )
    assert comparison["aggregate_accuracy_improved_with_safety_or_evidence_regression"]["flag"] is True


def test_run_loader_rejects_tampering_and_comparison_requires_same_dataset(tmp_path):
    run = run_calibration(DATASET)
    good_path = tmp_path / "good.json"
    good_path.write_text(json.dumps(run))
    assert load_run_artifact(good_path)["run_id"] == run["run_id"]

    changed = copy.deepcopy(run)
    changed["metrics"]["human_reviewed"]["accuracy"]["numerator"] = 3
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(changed))
    with pytest.raises(CalibrationError, match="run ID does not match"):
        load_run_artifact(tampered_path)

    another = copy.deepcopy(run)
    another["dataset"]["sha256"] = "0" * 64
    _finalize_run(another)
    with pytest.raises(CalibrationError, match="same dataset SHA-256"):
        compare_calibration_runs(run, another)


def test_calibration_cli_writes_new_json_and_report_without_overwriting(tmp_path, capsys):
    output = tmp_path / "run.json"
    report = tmp_path / "run.md"
    candidate_output = tmp_path / "candidate.json"
    comparison_output = tmp_path / "comparison.json"
    comparison_report = tmp_path / "comparison.md"

    assert evaluation_cli.main(
        ["calibrate", "run", str(DATASET), "--output", str(output), "--report", str(report)]
    ) == 0
    captured = capsys.readouterr()
    assert "Synthetic fixture smoke test (not product-quality evidence)" in captured.out
    assert json.loads(output.read_text())["schema_version"] == "skillscout-calibration-run-v1"
    assert "Judgeable human examples: 0" in report.read_text()

    assert evaluation_cli.main(
        ["calibrate", "run", str(DATASET), "--output", str(candidate_output)]
    ) == 0
    capsys.readouterr()
    assert evaluation_cli.main(
        [
            "calibrate",
            "compare",
            str(output),
            str(candidate_output),
            "--output",
            str(comparison_output),
            "--report",
            str(comparison_report),
        ]
    ) == 0
    assert json.loads(comparison_output.read_text())["schema_version"] == "skillscout-calibration-comparison-v1"
    assert "No final decisions changed." in comparison_report.read_text()

    assert evaluation_cli.main(["calibrate", "run", str(DATASET), "--output", str(output)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err


def _record(
    example_id,
    expected,
    observed,
    provenance,
    *,
    capability=True,
    complete=True,
    status="judgeable",
):
    candidate_decision = "USE" if observed == "USE" else "REVIEW"
    return {
        "example_id": example_id,
        "split": "development",
        "challenge_category": "synthetic_or_reviewed",
        "assessment_status": status,
        "label_provenance": provenance,
        "expected_decision": expected,
        "observed_decision": observed,
        "expected_candidate_order": None,
        "observed_candidate_order": [],
        "quality_violations": [],
        "trace": {
            "search": {"complete": complete},
            "candidates": [
                {
                    "decision": candidate_decision,
                    "capability_supported": capability,
                    "evidence_status": "complete",
                    "external_audit_status": "current",
                    "compatibility": "compatible",
                }
            ]
            if candidate_decision == "USE"
            else [],
        },
    }


def _set_observed_decision(record, decision):
    record["observed_decision"] = decision
    if decision != "NO_SKILL_NEEDED":
        for candidate in record["trace"]["candidates"]:
            candidate["decision"] = decision


def _finalize_run(run):
    for record in run["examples"]:
        conformity = _conformity(record)
        record["decision_conformity"] = conformity.value
        record["diagnostic"]["status"] = (
            "not_judged" if conformity.name == "INDETERMINATE" else conformity.name.lower()
        )
        record["quality_violations"] = _quality_violations(record)
    run["metrics"] = compute_calibration_metrics(run["examples"])
    run.pop("run_id", None)
    run.pop("integrity_sha256", None)
    run["run_id"] = hashlib.sha256(
        _canonical_json(
            {key: value for key, value in run.items() if key != "created_at_utc"}
        )
    ).hexdigest()[:20]
    run["integrity_sha256"] = _integrity_hash(run)
