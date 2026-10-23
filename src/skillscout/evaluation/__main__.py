"""Command-line verification for offline SkillScout evaluation artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skillscout.evaluation.calibration import (
    compare_calibration_runs,
    load_run_artifact,
    render_comparison_report,
    render_run_report,
    run_calibration,
    write_new_artifact,
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
    calibration_compare = calibration_commands.add_parser("compare", help="compare two recorded calibration runs")
    calibration_compare.add_argument("baseline_run")
    calibration_compare.add_argument("candidate_run")
    calibration_compare.add_argument("--output", required=True, help="new machine-readable JSON artifact path")
    calibration_compare.add_argument("--report", help="optional path for a new Markdown report")

    args = parser.parse_args(argv)
    try:
        if args.command == "calibrate":
            output_path = Path(args.output).resolve()
            report_path = Path(args.report).resolve() if args.report else None
            if report_path is not None and output_path == report_path:
                raise ValueError("JSON output and Markdown report paths must be different")
            if args.calibration_command == "run":
                result = run_calibration(args.dataset)
                report_text = render_run_report(result)
            else:
                baseline = load_run_artifact(args.baseline_run)
                candidate = load_run_artifact(args.candidate_run)
                result = compare_calibration_runs(baseline, candidate)
                report_text = render_comparison_report(result)
            write_new_artifact(
                output_path,
                json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            )
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
