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
    render_run_report,
    run_calibration,
)
from skillscout.evaluation.calibration_reviews import (
    CalibrationReviewError,
    append_review_event,
    read_review_ledger,
    review_state,
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
        "examples": 7,
        "judgeable": 6,
        "incomplete": 0,
        "ambiguous": 1,
        "pending": 0,
        "human_reviewed_development_judgeable": 0,
        "human_reviewed_held_out_judgeable": None,
        "synthetic_judgeable": 6,
        "held_out_examples_excluded": 5,
        "review_statuses": {"unreviewed": 7},
        "unresolved_conflicts": 0,
        "resolved_ambiguous_reviews": 0,
        "resolved_unjudgeable_reviews": 0,
    }
    assert first["metrics"]["evaluation_groups"]["human_reviewed_development"]["decision"]["accuracy"]["value"] is None
    assert first["metrics"]["evaluation_groups"]["synthetic_smoke_test"]["decision"]["accuracy"]["value"] == 1.0
    assert first["metrics"]["by_challenge_category"]["title_only_overlap"]["synthetic_smoke_test"]["decision"]["accuracy"]["denominator"] == 1
    assert first["examples"][0]["trace"]["search"]["called"] is False
    by_id = {item["example_id"]: item for item in first["examples"]}
    assert by_id["title-only-oauth"]["observed_decision"] == "NO_SKILL_NEEDED"
    assert by_id["documented-oauth-use"]["observed_decision"] == "USE"
    all_examples = {
        item["example_id"]: item
        for item in run_calibration(DATASET, include_held_out=True)["examples"]
    }
    assert all_examples["incomplete-search"]["observed_decision"] == "REVIEW"
    assert all_examples["incomplete-search"]["diagnostic"]["likely_stages"] == [
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
    human = metrics["evaluation_groups"]["human_reviewed_development"]["decision"]

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
    assert metrics["by_challenge_category"]["synthetic_or_reviewed"]["human_reviewed_development"]["decision"]["accuracy"]["denominator"] == 7


def test_run_artifacts_and_compare_detect_decision_ranking_and_safety_regressions():
    baseline = run_calibration(DATASET, include_held_out=True)
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

    assert comparison["metric_changes"]["evaluation_groups"]["synthetic_smoke_test"]["accuracy"]["delta"] == pytest.approx(1 / 9)
    assert comparison["metric_changes"]["evaluation_groups"]["synthetic_smoke_test"]["decision"]["false_use_rate"] == {
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
    assert comparison["accuracy_improved_with_safety_or_evidence_regression"]["flag"] is True


def test_run_loader_rejects_tampering_and_comparison_requires_same_dataset(tmp_path):
    run = run_calibration(DATASET)
    good_path = tmp_path / "good.json"
    good_path.write_text(json.dumps(run))
    assert load_run_artifact(good_path)["run_id"] == run["run_id"]

    changed = copy.deepcopy(run)
    changed["metrics"]["evaluation_groups"]["human_reviewed_development"]["decision"]["accuracy"]["numerator"] = 3
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(changed))
    with pytest.raises(CalibrationError, match="run ID does not match"):
        load_run_artifact(tampered_path)

    another = copy.deepcopy(run)
    another["dataset"]["sha256"] = "0" * 64
    _finalize_run(another)
    with pytest.raises(CalibrationError, match="same dataset SHA-256"):
        compare_calibration_runs(run, another)

    synthetic_relabelled = copy.deepcopy(run)
    record = synthetic_relabelled["examples"][0]
    record["label_provenance"] = "human_reviewed"
    record["review_label_status"] = "judgeable"
    record["review_status"] = "consensus"
    record["review_event_ids"] = ["fake-event-id"]
    _finalize_run(synthetic_relabelled)
    relabelled_path = tmp_path / "synthetic-relabelled.json"
    relabelled_path.write_text(json.dumps(synthetic_relabelled))
    with pytest.raises(CalibrationError, match="synthetic-only runs cannot contain human-reviewed labels"):
        load_run_artifact(relabelled_path)


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
    assert "Synthetic smoke-test results (not recommendation-quality evidence)" in captured.out
    assert json.loads(output.read_text())["schema_version"] == "skillscout-calibration-run-v2"
    assert "Judgeable examples: 0" in report.read_text()
    assert "Excluded by default: 5 held-out examples" in report.read_text()

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
    assert json.loads(comparison_output.read_text())["schema_version"] == "skillscout-calibration-comparison-v2"
    assert "No final decisions changed." in comparison_report.read_text()

    assert evaluation_cli.main(["calibrate", "run", str(DATASET), "--output", str(output)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err


def test_v2_empty_template_validates_and_reports_no_human_quality_result():
    template = ROOT / "docs/calibration/v2/dataset-template.json"
    dataset, _ = load_calibration_dataset(template)
    schema = json.loads((ROOT / "docs/calibration/v2/schema.json").read_text())

    assert schema["properties"]["schema_version"]["const"] == "skillscout-calibration-dataset-v2"
    assert dataset["examples"] == []
    run = run_calibration(template)
    report = render_run_report(run)
    assert run["metrics"]["evaluation_groups"]["human_reviewed_development"]["decision"]["accuracy"]["value"] is None
    assert "No genuine human-reviewed examples" not in report
    assert "Judgeable examples: 0" in report


def test_v2_dataset_integrity_rejects_labels_duplicate_ids_and_cross_split_overlap(tmp_path):
    data = _human_review_dataset()
    data["examples"][1]["task"] = "  " + data["examples"][0]["task"].upper() + "  "
    overlapping = tmp_path / "overlap.json"
    overlapping.write_text(json.dumps(data))
    with pytest.raises(CalibrationError, match="same normalized task"):
        load_calibration_dataset(overlapping)

    data = _human_review_dataset()
    data["examples"][1]["id"] = data["examples"][0]["id"]
    duplicate = tmp_path / "duplicate-id.json"
    duplicate.write_text(json.dumps(data))
    with pytest.raises(CalibrationError, match="duplicate calibration example ID"):
        load_calibration_dataset(duplicate)

    data = _human_review_dataset()
    data["examples"][0]["expected_decision"] = "USE"
    inline = tmp_path / "inline-label.json"
    inline.write_text(json.dumps(data))
    with pytest.raises(CalibrationError, match="pending"):
        load_calibration_dataset(inline)

    data = _human_review_dataset()
    data["schema_version"] = {"invalid": "shape"}
    bad_schema = tmp_path / "bad-schema.json"
    bad_schema.write_text(json.dumps(data))
    with pytest.raises(CalibrationError, match="schema_version"):
        load_calibration_dataset(bad_schema)


def test_review_ledger_preserves_revision_history_and_requires_independent_consensus(tmp_path):
    dataset = _human_review_dataset(held_out=False)
    dataset["examples"] = dataset["examples"][:1]
    dataset_path = tmp_path / "review.json"
    dataset_path.write_text(json.dumps(dataset))
    loaded, digest = load_calibration_dataset(dataset_path)
    ledger = tmp_path / "reviews.jsonl"
    events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)

    first = _append_review(
        ledger, loaded, digest, events, "review-use", "reviewer-a", decision="REVIEW"
    )
    events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)
    revision = _append_review(
        ledger,
        loaded,
        digest,
        events,
        "review-use",
        "reviewer-a",
        decision="USE",
        revise=True,
    )
    events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)
    second = _append_review(
        ledger, loaded, digest, events, "review-use", "reviewer-b", decision="USE"
    )
    events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)

    state = review_state(events, "review-use")
    assert first["event_id"] != revision["event_id"]
    assert revision["supersedes_event_id"] == first["event_id"]
    assert state["status"] == "consensus"
    assert state["reviewer_ids"] == ["reviewer-a", "reviewer-b"]
    assert state["review_count"] == 2
    assert len(events) == 3
    assert ledger.stat().st_mode & 0o777 == 0o600


def test_disagreement_stays_unresolved_until_explicit_adjudication(tmp_path):
    dataset = _human_review_dataset(held_out=False)
    dataset["examples"] = dataset["examples"][:1]
    dataset_path = tmp_path / "review.json"
    dataset_path.write_text(json.dumps(dataset))
    loaded, digest = load_calibration_dataset(dataset_path)
    ledger = tmp_path / "reviews.jsonl"
    events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)
    first = _append_review(ledger, loaded, digest, events, "review-use", "reviewer-a", decision="USE")
    events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)
    second = _append_review(ledger, loaded, digest, events, "review-use", "reviewer-b", decision="REVIEW")
    events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)

    conflict = review_state(events, "review-use")
    assert conflict["status"] == "conflict"
    assert conflict["adjudication_status"] == "required"
    run = run_calibration(dataset_path, reviews_path=ledger)
    assert run["metrics"]["evaluation_groups"]["human_reviewed_development"]["example_count"] == 0
    assert run["metrics"]["counts"]["unresolved_conflicts"] == 1
    assert run["examples"][0]["expected_decision"] is None

    adjudication = _append_review(
        ledger,
        loaded,
        digest,
        events,
        "review-use",
        "adjudicator",
        decision="REVIEW",
        adjudication=True,
        adjudicates_event_ids=[first["event_id"], second["event_id"]],
    )
    events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)
    state = review_state(events, "review-use")
    assert state["status"] == "adjudicated"
    assert state["adjudicator_id"] == "adjudicator"
    assert state["resolved_event"]["event_id"] == adjudication["event_id"]
    resolved_run = run_calibration(dataset_path, reviews_path=ledger)
    assert resolved_run["metrics"]["evaluation_groups"]["human_reviewed_development"]["example_count"] == 1
    assert resolved_run["examples"][0]["expected_decision"] == "REVIEW"


def test_ambiguous_and_unjudgeable_consensus_remain_outside_accuracy(tmp_path):
    dataset = _human_review_dataset(held_out=False)
    second = copy.deepcopy(dataset["examples"][0])
    second.update(
        {
            "id": "review-unjudgeable",
            "task": "Add a database migration with an unknown ORM",
            "challenge_category": "missing_operation_evidence",
        }
    )
    dataset["examples"].append(second)
    path = tmp_path / "review.json"
    path.write_text(json.dumps(dataset))
    loaded, digest = load_calibration_dataset(path)
    ledger = tmp_path / "reviews.jsonl"
    events = []
    for example_id, status, reason in (
        ("review-use", "ambiguous", "Available excerpts do not resolve whether REVIEW or USE is justified."),
        ("review-unjudgeable", "unjudgeable", "The candidate detail is missing the requested operation evidence."),
    ):
        for reviewer in ("reviewer-a", "reviewer-b"):
            append_review_event(
                ledger,
                dataset=loaded,
                dataset_sha256=digest,
                events=events,
                example_id=example_id,
                reviewer_id=reviewer,
                status=status,
                decision=None,
                candidate_order=None,
                rationale=None,
                reason=reason,
                evidence_basis=[],
            )
            events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)

    run = run_calibration(path, reviews_path=ledger)
    assert run["metrics"]["counts"]["ambiguous"] == 1
    assert run["metrics"]["counts"]["incomplete"] == 1
    assert run["metrics"]["evaluation_groups"]["human_reviewed_development"]["example_count"] == 0
    by_id = {item["example_id"]: item for item in run["examples"]}
    assert by_id["review-use"]["assessment_status"] == "ambiguous"
    assert by_id["review-unjudgeable"]["assessment_status"] == "incomplete"


def test_held_out_metrics_are_separate_and_excluded_by_default(tmp_path):
    dataset = _human_review_dataset()
    dataset_path = tmp_path / "review.json"
    dataset_path.write_text(json.dumps(dataset))
    loaded, digest = load_calibration_dataset(dataset_path)
    ledger = tmp_path / "reviews.jsonl"
    events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)
    for example_id in ("review-use", "review-heldout"):
        reviewer_a = _append_review(ledger, loaded, digest, events, example_id, "reviewer-a", decision="USE")
        events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)
        _append_review(ledger, loaded, digest, events, example_id, "reviewer-b", decision="USE")
        events, _ = read_review_ledger(ledger, dataset=loaded, dataset_sha256=digest)

    default = run_calibration(dataset_path, reviews_path=ledger)
    assert [item["example_id"] for item in default["examples"]] == ["review-use"]
    assert default["metrics"]["counts"]["held_out_examples_excluded"] == 1
    groups = default["metrics"]["evaluation_groups"]
    assert groups["human_reviewed_development"]["decision"]["accuracy"]["denominator"] == 1
    assert groups["human_reviewed_held_out"]["included"] is False
    assert groups["synthetic_smoke_test"]["example_count"] == 0
    assert "review-heldout" not in render_run_report(default)

    final = run_calibration(dataset_path, reviews_path=ledger, include_held_out=True)
    groups = final["metrics"]["evaluation_groups"]
    assert groups["human_reviewed_held_out"]["decision"]["accuracy"]["denominator"] == 1
    assert "Held-out examples were explicitly included" in render_run_report(final)
    changed = copy.deepcopy(final)
    _set_observed_decision(
        next(item for item in changed["examples"] if item["example_id"] == "review-heldout"),
        "REVIEW",
    )
    _finalize_run(changed)
    comparison = compare_calibration_runs(final, changed)
    comparison_groups = comparison["metric_changes"]["evaluation_groups"]
    assert comparison_groups["human_reviewed_development"]["accuracy"]["delta"] == 0
    assert comparison_groups["human_reviewed_held_out"]["accuracy"]["delta"] == -1
    assert comparison_groups["synthetic_smoke_test"]["accuracy"]["delta"] is None
    with pytest.raises(CalibrationError, match="same held-out inclusion"):
        compare_calibration_runs(default, final)


def test_human_reviewed_calibration_run_is_offline_without_catalog_credentials(tmp_path, monkeypatch):
    def fail_network(*_args, **_kwargs):
        raise AssertionError("human-review calibration attempted an HTTP request")

    monkeypatch.setattr(httpx.Client, "request", fail_network)
    path = tmp_path / "review.json"
    path.write_text(json.dumps(_human_review_dataset(held_out=False)))
    run = run_calibration(path)

    assert run["configuration"]["network_access"] == "disabled_by_construction"
    assert run["configuration"]["optional_llm"] == "disabled"
    assert run["examples"][0]["review_status"] == "unreviewed"


def test_review_labels_reject_incomplete_no_skill_missing_safety_and_synthetic_conversion(tmp_path):
    dataset = _human_review_dataset(held_out=False)
    dataset["examples"] = dataset["examples"][:1]
    example = dataset["examples"][0]
    example["search"].update(complete=False, coverage_status="incomplete")
    dataset_path = tmp_path / "review.json"
    dataset_path.write_text(json.dumps(dataset))
    loaded, digest = load_calibration_dataset(dataset_path)
    ledger = tmp_path / "reviews.jsonl"
    with pytest.raises(CalibrationReviewError, match="complete adequate search"):
        _append_review(ledger, loaded, digest, [], "review-use", "reviewer-a", decision="NO_SKILL_NEEDED")
    no_audit = _human_review_dataset(held_out=False)
    no_audit["examples"][0]["search"]["candidates"][0]["audit"] = {
        "status": "none", "findings": [], "notes": []
    }
    no_audit_path = tmp_path / "no-audit.json"
    no_audit_path.write_text(json.dumps(no_audit))
    no_audit_loaded, no_audit_digest = load_calibration_dataset(no_audit_path)
    with pytest.raises(CalibrationReviewError, match="passing audit"):
        _append_review(ledger, no_audit_loaded, no_audit_digest, [], "review-use", "reviewer-a", decision="USE")
    with pytest.raises(CalibrationReviewError, match="confirmed critical local finding"):
        _append_review(ledger, no_audit_loaded, no_audit_digest, [], "review-use", "reviewer-a", decision="AVOID")

    synthetic, synthetic_digest = load_calibration_dataset(DATASET)
    synthetic_pending = next(item for item in synthetic["examples"] if item["label_provenance"] == "pending")
    with pytest.raises(CalibrationReviewError, match="human-review v2 dataset"):
        append_review_event(
            ledger,
            dataset=synthetic,
            dataset_sha256=synthetic_digest,
            events=[],
            example_id=synthetic_pending["id"],
            reviewer_id="reviewer-a",
            status="unjudgeable",
            decision=None,
            candidate_order=None,
            rationale=None,
            reason="Synthetic fixtures cannot be converted to human labels.",
            evidence_basis=[],
        )


def test_avoid_label_can_cite_a_confirmed_critical_local_finding(tmp_path):
    source, _ = load_calibration_dataset(DATASET)
    example = copy.deepcopy(next(item for item in source["examples"] if item["id"] == "critical-safety-evidence"))
    example.update(
        {
            "id": "review-critical",
            "split": "development",
            "assessment_status": "pending",
            "label_provenance": "pending",
            "reviewer": None,
            "expected_decision": None,
            "expected_candidate_order": None,
            "rationale": "",
            "evidence_basis": [],
        }
    )
    dataset = {
        "schema_version": "skillscout-calibration-dataset-v2",
        "dataset_id": "human-review-critical-test",
        "dataset_version": "2.0.0-test",
        "data_kind": "human_reviewed",
        "description": "Local critical signal fixture.",
        "candidate_limit": 5,
        "evaluation_clock_utc": source["evaluation_clock_utc"],
        "examples": [example],
    }
    path = tmp_path / "critical.json"
    path.write_text(json.dumps(dataset))
    loaded, digest = load_calibration_dataset(path)
    event = append_review_event(
        tmp_path / "reviews.jsonl",
        dataset=loaded,
        dataset_sha256=digest,
        events=[],
        example_id="review-critical",
        reviewer_id="reviewer-a",
        status="judgeable",
        decision="AVOID",
        candidate_order=None,
        rationale="The inspected excerpt instructs credential exfiltration and the local scanner marks it critical.",
        reason=None,
        evidence_basis=["The exact SKILL.md excerpt says to send API_KEY to a remote collector."],
    )
    assert event["decision"] == "AVOID"


def test_review_cli_shows_evidence_and_requires_explicit_held_out_access(tmp_path, capsys):
    dataset = _human_review_dataset()
    path = tmp_path / "review.json"
    path.write_text(json.dumps(dataset))
    ledger = tmp_path / "reviews.jsonl"

    assert evaluation_cli.main(["calibrate", "review", "show", str(path), "review-use"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["proposed_decision"] == "USE"
    assert shown["search"]["candidates"][0]["evidence"]["files"][0]["path"] == "SKILL.md"

    assert evaluation_cli.main(["calibrate", "review", "show", str(path), "review-heldout"]) == 1
    assert "held-out inspection is sealed" in capsys.readouterr().err
    assert evaluation_cli.main(
        ["calibrate", "review", "show", str(path), "review-heldout", "--include-held-out"]
    ) == 0
    assert "held_out_notice" in json.loads(capsys.readouterr().out)

    assert evaluation_cli.main(
        [
            "calibrate", "review", "submit", str(path), "review-use", "--reviews", str(ledger),
            "--reviewer", "reviewer-a", "--status", "judgeable", "--decision", "USE",
            "--rationale", "The inspected instructions document the requested OAuth operation.",
            "--evidence-basis", "The SKILL.md excerpt gives the FastAPI OAuth procedure.",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["event_type"] == "review"
    assert evaluation_cli.main(
        ["calibrate", "validate", str(path), "--reviews", str(ledger)]
    ) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["awaiting_second_review"] == 1


def _human_review_dataset(*, held_out=True):
    source, _ = load_calibration_dataset(DATASET)
    base = copy.deepcopy(next(item for item in source["examples"] if item["id"] == "documented-oauth-use"))
    base.update(
        {
            "id": "review-use",
            "split": "development",
            "challenge_category": "human_capability_evidence",
            "assessment_status": "pending",
            "label_provenance": "pending",
            "reviewer": None,
            "expected_decision": None,
            "expected_candidate_order": None,
            "rationale": "",
            "evidence_basis": [],
        }
    )
    examples = [base]
    if held_out:
        held = copy.deepcopy(base)
        held.update(
            {
                "id": "review-heldout",
                "split": "held_out",
                "task": "Add OAuth sign-in to a FastAPI application",
                "challenge_category": "held_out_paraphrase",
            }
        )
        examples.append(held)
    return {
        "schema_version": "skillscout-calibration-dataset-v2",
        "dataset_id": "human-review-test-fixture",
        "dataset_version": "2.0.0-test",
        "data_kind": "human_reviewed",
        "description": "Prepared local evidence fixture used to test the review workflow.",
        "candidate_limit": 5,
        "evaluation_clock_utc": source["evaluation_clock_utc"],
        "examples": examples,
    }


def _append_review(
    ledger,
    dataset,
    digest,
    events,
    example_id,
    reviewer_id,
    *,
    decision,
    revise=False,
    adjudication=False,
    adjudicates_event_ids=(),
):
    return append_review_event(
        ledger,
        dataset=dataset,
        dataset_sha256=digest,
        events=events,
        example_id=example_id,
        reviewer_id=reviewer_id,
        status="judgeable",
        decision=decision,
        candidate_order=None,
        rationale=f"The evidence packet supports {decision} for this prepared task.",
        reason=None,
        evidence_basis=["The exact SKILL.md fixture excerpt documents FastAPI OAuth authentication."],
        adjudication=adjudication,
        revise=revise,
        adjudicates_event_ids=adjudicates_event_ids,
    )


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
    run["metrics"] = compute_calibration_metrics(
        run["examples"],
        held_out_included=run["configuration"]["include_held_out"],
        held_out_excluded_count=(
            0 if run["configuration"]["include_held_out"] else run["configuration"]["held_out_example_count"]
        ),
    )
    run.pop("run_id", None)
    run.pop("integrity_sha256", None)
    run["run_id"] = hashlib.sha256(
        _canonical_json(
            {key: value for key, value in run.items() if key != "created_at_utc"}
        )
    ).hexdigest()[:20]
    run["integrity_sha256"] = _integrity_hash(run)
