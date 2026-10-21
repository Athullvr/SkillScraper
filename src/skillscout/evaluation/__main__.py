"""Command-line verification for offline SkillScout evaluation artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skillscout.evaluation.fixtures import DEFAULT_INCOMPLETE_FACTS, pin_fixture_pack, verify_fixture_pack
from skillscout.evaluation.metrics import compute_metrics
from skillscout.evaluation.protocol import DATASET_RELATIVE_PATH, find_repository_root, load_dataset
from skillscout.evaluation.reviews import ReviewLedger, apply_candidate_review_labels, decision_outcomes_by_pair
from skillscout.evaluation.store import RunStoreError, read_run_bundle
from skillscout.evaluation.validation import validate_records


def _repository_root() -> Path:
    return find_repository_root()


def _contexts_path(value: str | None) -> Path:
    return Path(value) if value else _repository_root() / "docs/evaluation/v1/contexts"


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

    args = parser.parse_args(argv)
    try:
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
