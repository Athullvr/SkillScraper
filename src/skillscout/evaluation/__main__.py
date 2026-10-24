"""Command-line verification for offline SkillScout evaluation artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skillscout.evaluation.calibration import (
    _run_example,
    compare_calibration_runs,
    load_calibration_dataset,
    load_run_artifact,
    render_comparison_report,
    render_run_report,
    run_calibration,
    write_new_artifact,
)
from skillscout.evaluation.calibration_reviews import (
    append_review_event,
    read_review_ledger,
    review_state,
)
from skillscout.evaluation.fixtures import DEFAULT_INCOMPLETE_FACTS, pin_fixture_pack, verify_fixture_pack
from skillscout.evaluation.evidence_packets import (
    apply_persisted_reviews_and_derive_metrics,
    build_review_artifacts_from_run,
    load_review_packet_artifacts,
)
from skillscout.evaluation.metrics import compute_metrics
from skillscout.evaluation.protocol import DATASET_RELATIVE_PATH, find_repository_root, load_dataset
from skillscout.evaluation.reviews import ReviewLedger, apply_candidate_review_labels, decision_outcomes_by_pair
from skillscout.evaluation.store import RunStoreError, read_run_bundle
from skillscout.evaluation.validation import validate_records


def _repository_root() -> Path:
    return find_repository_root()


def _contexts_path(value: str | None) -> Path:
    return Path(value) if value else _repository_root() / "docs/evaluation/v1/contexts"


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON input contains duplicate object keys")
        result[key] = value
    return result


def _read_json_input(path: str, *, expected_type: type) -> object:
    source = Path(path)
    if source.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("JSON input exceeds the 8 MiB limit")
    try:
        value = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("JSON input is malformed") from exc
    if not isinstance(value, expected_type):
        raise ValueError("JSON input has the wrong root type")
    return value


def _add_review_label_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("dataset")
    parser.add_argument("example_id")
    parser.add_argument("--reviews", required=True, help="append-only human-review JSONL ledger path")
    parser.add_argument("--reviewer", required=True, help="non-identifying reviewer token")
    parser.add_argument("--status", required=True, choices=("judgeable", "ambiguous", "unjudgeable"))
    parser.add_argument("--decision", choices=("NO_SKILL_NEEDED", "USE", "REVIEW", "AVOID"))
    parser.add_argument("--candidate-order", nargs="+", help="ordered candidate IDs, best first")
    parser.add_argument("--rationale", help="concise evidence-backed rationale for a judgeable label")
    parser.add_argument("--reason", help="required reason for ambiguous or unjudgeable labels")
    parser.add_argument("--evidence-basis", action="append", default=[], help="specific evidence fact; repeat for multiple facts")
    parser.add_argument("--revise", action="store_true", help="append a revision while preserving the earlier label")


def _run_calibration_review(args: argparse.Namespace) -> int:
    dataset, dataset_sha256 = load_calibration_dataset(args.dataset)
    examples = {item["id"]: item for item in dataset["examples"]}
    if args.review_command == "show":
        example = examples.get(args.example_id)
        if example is None:
            raise ValueError(f"unknown calibration example ID: {args.example_id}")
        if example["split"] == "held_out" and not args.include_held_out:
            raise ValueError("held-out inspection is sealed by default; pass --include-held-out to reveal it explicitly")
        events, ledger_digest = read_review_ledger(
            args.reviews, dataset=dataset, dataset_sha256=dataset_sha256
        )
        record = _run_example(dataset, example)
        candidate_traces = {item["stable_id"]: item for item in record["trace"]["candidates"]}
        payload = {
            "example_id": example["id"],
            "split": example["split"],
            "challenge_category": example["challenge_category"],
            "assessment_status": example["assessment_status"],
            "label_provenance": example["label_provenance"],
            "task": example["task"],
            "project_facts": example["project_facts"],
            "search": {
                "complete": example["search"]["complete"],
                "coverage_status": example["search"]["coverage_status"],
                "candidates": [
                    {
                        "skill": candidate["skill"],
                        "evidence": candidate["evidence"],
                        "audit": candidate["audit"],
                        "proposed_candidate_decision": candidate_traces.get(
                            candidate["skill"]["id"], {}
                        ).get("decision"),
                        "proposed_candidate_reasons": candidate_traces.get(
                            candidate["skill"]["id"], {}
                        ).get("decision_reasons", []),
                        "task_fit_rationale": candidate_traces.get(
                            candidate["skill"]["id"], {}
                        ).get("task_fit_rationale", []),
                    }
                    for candidate in example["search"]["candidates"]
                ],
            },
            "proposed_decision": record["observed_decision"],
            "proposed_rationale": record["trace"]["report_summary"],
            "proposed_limitations": record["trace"]["report_limitations"],
            "review_state": review_state(events, example["id"]),
            "review_ledger_sha256": ledger_digest,
        }
        if example["split"] == "held_out":
            payload["held_out_notice"] = "This is held-out material. Do not use it to select thresholds, vocabulary, or ranking changes."
        print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    if args.review_command == "status":
        events, _ = read_review_ledger(
            args.reviews, dataset=dataset, dataset_sha256=dataset_sha256
        )
        output = []
        for example in dataset["examples"]:
            state = review_state(events, example["id"])
            output.append(
                {
                    "example_id": example["id"],
                    "split": example["split"],
                    "status": state["status"],
                    "adjudication_status": state["adjudication_status"],
                    "reviewer_ids": state["reviewer_ids"],
                    "review_event_ids": state["review_event_ids"],
                    "adjudicator_id": state["adjudicator_id"],
                }
            )
        print(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    if args.review_command in {"submit", "adjudicate"}:
        if args.example_id not in examples:
            raise ValueError(f"unknown calibration example ID: {args.example_id}")
        events, _ = read_review_ledger(
            args.reviews, dataset=dataset, dataset_sha256=dataset_sha256
        )
        event = append_review_event(
            args.reviews,
            dataset=dataset,
            dataset_sha256=dataset_sha256,
            events=events,
            example_id=args.example_id,
            reviewer_id=args.reviewer,
            status=args.status,
            decision=args.decision,
            candidate_order=args.candidate_order,
            rationale=args.rationale,
            reason=args.reason,
            evidence_basis=args.evidence_basis,
            adjudication=args.review_command == "adjudicate",
            revise=args.revise,
            adjudicates_event_ids=getattr(args, "adjudicates", ()),
        )
        print(
            json.dumps(
                {"event_id": event["event_id"], "event_type": event["event_type"], "example_id": event["example_id"]},
                indent=2,
            )
        )
        return 0
    raise ValueError("unsupported calibration review command")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m skillscout.evaluation")
    commands = parser.add_subparsers(dest="command", required=True)

    fixtures = commands.add_parser("fixtures", help="pin or verify offline task contexts")
    fixture_commands = fixtures.add_subparsers(dest="fixture_command", required=True)
    pin = fixture_commands.add_parser("pin", help="write canonical fixture manifests and lock index")
    pin.add_argument("--root", help="contexts directory; defaults to the v1 repository directory")
    verify = fixture_commands.add_parser("verify", help="verify the pinned context pack")
    verify.add_argument("--root", help="contexts directory; defaults to the v1 repository directory")

    validate = commands.add_parser("validate", help="validate a restricted run directory")
    validate.add_argument("run_directory")
    metrics = commands.add_parser("metrics", help="derive protocol metrics from a validated run")
    metrics.add_argument("run_directory")

    packets = commands.add_parser("packets", help="build, verify, or score blinded review artifacts")
    packet_commands = packets.add_subparsers(dest="packet_command", required=True)
    packet_build = packet_commands.add_parser("build", help="build blinded packets from a persisted offline run")
    packet_build.add_argument("run_directory")
    packet_build.add_argument("--evidence-source", help="private skillscout-restricted-evidence-source-v1 artifact")
    packet_build.add_argument("--approved-contexts", help="JSON object mapping all 14 task IDs to approved context text")
    packet_build.add_argument("--decision-criteria", help="JSON object mapping task IDs to approved criteria")
    packet_build.add_argument("--decision-assessable-pairs", help="JSON array of explicitly assessable pair IDs")
    packet_build.add_argument("--protected-decision-pairs", help="JSON array of pairs gated by protected evidence")
    packet_build.add_argument("--reviewer-output", required=True)
    packet_build.add_argument("--restricted-output", required=True)
    packet_verify = packet_commands.add_parser("verify", help="verify a persisted reviewer packet bundle")
    packet_verify.add_argument("reviewer_artifact")
    packet_metrics = packet_commands.add_parser("metrics", help="derive metrics from persisted packets and reviews")
    packet_metrics.add_argument("run_directory")
    packet_metrics.add_argument("--reviewer-artifact", required=True)
    packet_metrics.add_argument("--restricted-crosswalk", required=True)
    packet_metrics.add_argument("--review-ledger", required=True)
    packet_metrics.add_argument("--authorization-reference", required=True)

    calibrate = commands.add_parser(
        "calibrate",
        help="run offline recommendation-quality calibration on a versioned local fixture dataset",
    )
    calibration_commands = calibrate.add_subparsers(dest="calibration_command", required=True)
    calibration_run = calibration_commands.add_parser("run", help="run the production pipeline on local fixtures")
    calibration_run.add_argument("dataset")
    calibration_run.add_argument("--output", required=True, help="new machine-readable JSON artifact path")
    calibration_run.add_argument("--report", help="optional path for a new Markdown report")
    calibration_run.add_argument("--reviews", help="optional append-only human-review JSONL ledger")
    calibration_run.add_argument(
        "--include-held-out",
        action="store_true",
        help="explicitly include held-out examples for a final evaluation report",
    )
    calibration_compare = calibration_commands.add_parser("compare", help="compare two recorded calibration runs")
    calibration_compare.add_argument("baseline_run")
    calibration_compare.add_argument("candidate_run")
    calibration_compare.add_argument("--output", required=True, help="new machine-readable JSON artifact path")
    calibration_compare.add_argument("--report", help="optional path for a new Markdown report")
    calibration_validate = calibration_commands.add_parser("validate", help="validate a dataset and its review history")
    calibration_validate.add_argument("dataset")
    calibration_validate.add_argument("--reviews", help="optional append-only human-review JSONL ledger")

    calibration_review = calibration_commands.add_parser("review", help="inspect and record offline human judgments")
    review_commands = calibration_review.add_subparsers(dest="review_command", required=True)
    review_show = review_commands.add_parser("show", help="show a task, fixture evidence, and current proposal")
    review_show.add_argument("dataset")
    review_show.add_argument("example_id")
    review_show.add_argument("--reviews", help="optional append-only human-review JSONL ledger")
    review_show.add_argument("--include-held-out", action="store_true", help="explicitly reveal a held-out example")
    review_status = review_commands.add_parser("status", help="show review and adjudication state for every example")
    review_status.add_argument("dataset")
    review_status.add_argument("--reviews", help="optional append-only human-review JSONL ledger")
    review_submit = review_commands.add_parser("submit", help="append an independent label or explicit revision")
    _add_review_label_arguments(review_submit)
    review_adjudicate = review_commands.add_parser("adjudicate", help="append an independent adjudication of conflicting reviews")
    _add_review_label_arguments(review_adjudicate)
    review_adjudicate.add_argument("--adjudicates", nargs="+", required=True, help="all current conflicting review event IDs")

    args = parser.parse_args(argv)
    try:
        if args.command == "calibrate":
            if args.calibration_command == "run":
                output_path = Path(args.output).resolve()
                report_path = Path(args.report).resolve() if args.report else None
                if report_path is not None and output_path == report_path:
                    raise ValueError("JSON output and Markdown report paths must be different")
                result = run_calibration(
                    args.dataset,
                    reviews_path=args.reviews,
                    include_held_out=args.include_held_out,
                )
                report_text = render_run_report(result)
            elif args.calibration_command == "compare":
                output_path = Path(args.output).resolve()
                report_path = Path(args.report).resolve() if args.report else None
                if report_path is not None and output_path == report_path:
                    raise ValueError("JSON output and Markdown report paths must be different")
                baseline = load_run_artifact(args.baseline_run)
                candidate = load_run_artifact(args.candidate_run)
                result = compare_calibration_runs(baseline, candidate)
                report_text = render_comparison_report(result)
            elif args.calibration_command == "validate":
                dataset, digest = load_calibration_dataset(args.dataset)
                events, ledger_digest = read_review_ledger(
                    args.reviews, dataset=dataset, dataset_sha256=digest
                )
                states = {
                    item["id"]: review_state(events, item["id"])
                    for item in dataset["examples"]
                }
                print(
                    json.dumps(
                        {
                            "integrity": "valid",
                            "dataset_id": dataset["dataset_id"],
                            "dataset_version": dataset["dataset_version"],
                            "schema_version": dataset["schema_version"],
                            "dataset_sha256": digest,
                            "example_count": len(dataset["examples"]),
                            "development_count": sum(item["split"] == "development" for item in dataset["examples"]),
                            "held_out_count": sum(item["split"] == "held_out" for item in dataset["examples"]),
                            "review_ledger_sha256": ledger_digest,
                            "review_event_count": len(events),
                            "review_states": {key: value["status"] for key, value in states.items()},
                            "unresolved_conflicts": sum(value["status"] == "conflict" for value in states.values()),
                            "awaiting_second_review": sum(value["status"] == "awaiting_independent_review" for value in states.values()),
                        },
                        indent=2,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0
            else:
                return _run_calibration_review(args)
            write_new_artifact(output_path, json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
            if report_path is not None:
                write_new_artifact(report_path, report_text)
            print(report_text)
            return 0

        if args.command == "fixtures":
            dataset = load_dataset(_repository_root() / DATASET_RELATIVE_PATH)
            root = _contexts_path(args.root)
            if args.fixture_command == "pin":
                result = pin_fixture_pack(
                    root,
                    dataset.eligible_tasks,
                    incomplete_facts=DEFAULT_INCOMPLETE_FACTS,
                )
            else:
                result = verify_fixture_pack(root, dataset.eligible_tasks)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0

        if args.command == "packets":
            if args.packet_command == "build":
                contexts = _read_json_input(args.approved_contexts, expected_type=dict) if args.approved_contexts else None
                criteria = _read_json_input(args.decision_criteria, expected_type=dict) if args.decision_criteria else None
                assessable_pairs = _read_json_input(args.decision_assessable_pairs, expected_type=list) if args.decision_assessable_pairs else ()
                protected_pairs = _read_json_input(args.protected_decision_pairs, expected_type=list) if args.protected_decision_pairs else ()
                if any(not isinstance(item, str) for item in (*assessable_pairs, *protected_pairs)):
                    raise ValueError("decision pair inputs must contain only pair ID strings")
                bundle, reviewer_path, restricted_path = build_review_artifacts_from_run(
                    args.run_directory,
                    reviewer_path=args.reviewer_output,
                    restricted_crosswalk_path=args.restricted_output,
                    evidence_source_path=args.evidence_source,
                    approved_context_by_task=contexts,
                    decision_criteria_by_task=criteria,
                    decision_assessable_pairs=assessable_pairs,
                    protected_decision_pairs=protected_pairs,
                )
                print(json.dumps({
                    "reviewer_artifact": str(reviewer_path),
                    "restricted_crosswalk": str(restricted_path),
                    "candidate_packets": len(bundle.candidate_artifacts),
                    "decision_packets": len(bundle.decision_packets),
                    "indeterminate_decision_gates": len(bundle.decision_gates),
                }, indent=2, ensure_ascii=False))
                return 0
            if args.packet_command == "verify":
                loaded = load_review_packet_artifacts(args.reviewer_artifact)
                print(json.dumps({
                    "dataset_sha256": loaded.dataset_sha256,
                    "catalog_mode": loaded.catalog_mode,
                    "candidate_packets": len(loaded.candidate_packets),
                    "assessable_candidate_packets": sum(item.packet.assessment_allowed for item in loaded.candidate_packets),
                    "decision_packets": len(loaded.decision_packets),
                    "assessable_decision_packets": sum(item.assessment_allowed for item in loaded.decision_packets),
                }, indent=2, ensure_ascii=False))
                return 0
            result = apply_persisted_reviews_and_derive_metrics(
                args.run_directory,
                reviewer_artifact_path=args.reviewer_artifact,
                restricted_crosswalk_path=args.restricted_crosswalk,
                packet_event_path=args.review_ledger,
                authorization_reference=args.authorization_reference,
            )
            print(json.dumps(result["metrics"], indent=2, ensure_ascii=False))
            return 0

        run = read_run_bundle(args.run_directory)
        manifest, pairs = run.manifest, run.pairs
        dataset = load_dataset(_repository_root() / DATASET_RELATIVE_PATH)
        errors = validate_records(pairs, dataset.eligible_tasks, dataset_sha256=manifest.get("dataset_sha256", ""))
        if args.command == "validate":
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
            print(f"PASS: validated {len(pairs)} fixed-schedule pair records")
            return 0
        if errors:
            print("run validation failed; metrics not produced:", file=sys.stderr)
            print("\n".join(errors), file=sys.stderr)
            return 1
        ledger = ReviewLedger.from_records(run.review_events)
        apply_candidate_review_labels(pairs, ledger)
        decision_packet_keys = {
            entry["pair_id"]: entry["opaque_decision_packet_key"]
            for entry in run.decision_crosswalk
        }
        decision_outcomes = decision_outcomes_by_pair(
            pairs,
            ledger,
            decision_packet_keys,
            task_gates_approved=manifest.get("decision_gates_approved_by_task") or False,
        )
        report = compute_metrics(
            pairs,
            dataset.eligible_tasks,
            decision_outcomes=decision_outcomes,
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RunStoreError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
