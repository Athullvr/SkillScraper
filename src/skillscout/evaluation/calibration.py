"""Offline calibration against versioned, local candidate fixtures.

This workflow calls the production recommendation pipeline through its existing
injectable catalog/project boundaries. It does not use the live-catalog runner
or the fixed 14-task evaluation protocol.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from unittest.mock import patch

from pydantic import ValidationError

from skillscout import __version__
from skillscout.application import prepare_recommendation
from skillscout.catalog import CatalogUnavailableError
from skillscout.config import Config
from skillscout.evaluation.fixtures import has_credential_like_text
from skillscout.evaluation.models import DecisionConformity
from skillscout.evaluation.calibration_reviews import read_review_ledger, review_state
from skillscout.evidence_explanations import explain_candidate
from skillscout.evidence_packet import build_evidence_packet
from skillscout.models import (
    AuditFinding,
    CatalogSkill,
    Decision,
    ProjectSnapshot,
    SkillDetail,
    SkillFile,
)
from skillscout.ranking import RankedSkill, rank_catalog_results, refine_rank, relevance_threshold

DATASET_SCHEMA_V1 = "skillscout-calibration-dataset-v1"
DATASET_SCHEMA_V2 = "skillscout-calibration-dataset-v2"
DATASET_SCHEMAS = {DATASET_SCHEMA_V1, DATASET_SCHEMA_V2}
RUN_SCHEMA = "skillscout-calibration-run-v2"
COMPARISON_SCHEMA = "skillscout-calibration-comparison-v2"
MAX_DATASET_BYTES = 2 * 1024 * 1024
MAX_RUN_BYTES = 8 * 1024 * 1024
MAX_EXAMPLES = 500
MAX_CANDIDATES = 25
MAX_EVIDENCE_CHARS = 30_000
EXAMPLE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}\Z")
DECISION_VALUES = {item.value for item in Decision}
ASSESSMENT_STATUSES = {"judgeable", "incomplete", "ambiguous", "pending"}
LABEL_PROVENANCE = {"human_reviewed", "synthetic_fixture", "pending"}
SPLITS = {"development", "held_out"}
SEARCH_COVERAGE = {"adequate", "incomplete", "unknown", "not_needed"}
DETAIL_STATUSES = {"complete", "incomplete", "unavailable"}
AUDIT_STATUSES = {"available", "none", "unavailable"}


class CalibrationError(ValueError):
    """A calibration dataset or recorded run is malformed or incompatible."""


def load_calibration_dataset(path: str | Path) -> tuple[dict[str, Any], str]:
    """Load a bounded, duplicate-key-free dataset and return it with its SHA-256."""
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise CalibrationError("calibration dataset must be a regular, non-symlink file")
    try:
        info = source.stat()
        if info.st_size > MAX_DATASET_BYTES:
            raise CalibrationError("calibration dataset exceeds the 2 MiB limit")
        raw = source.read_bytes()
    except OSError as exc:
        raise CalibrationError("calibration dataset could not be read") from exc
    try:
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationError("calibration dataset is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise CalibrationError("calibration dataset root must be a JSON object")
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if has_credential_like_text(serialized):
        raise CalibrationError("calibration dataset contains credential-like text")
    _validate_dataset(data)
    return data, hashlib.sha256(raw).hexdigest()


def run_calibration(
    dataset_path: str | Path,
    *,
    reviews_path: str | Path | None = None,
    include_held_out: bool = False,
) -> dict[str, Any]:
    """Run local fixtures and ledger-resolved labels through the production pipeline."""
    dataset, dataset_sha256 = load_calibration_dataset(dataset_path)
    if any(item["label_provenance"] == "human_reviewed" for item in dataset["examples"]):
        raise CalibrationError("human labels must come from the append-only review ledger, not inline dataset fields")
    review_events, reviews_sha256 = read_review_ledger(
        reviews_path,
        dataset=dataset,
        dataset_sha256=dataset_sha256,
    )
    held_out_count = sum(item["split"] == "held_out" for item in dataset["examples"])
    records: list[dict[str, Any]] = []
    for source_example in dataset["examples"]:
        state = review_state(review_events, source_example["id"])
        example = _effective_review_label(source_example, state)
        if example["split"] == "held_out" and not include_held_out:
            continue
        record = _run_example(dataset, example)
        record["review_status"] = state["status"]
        record["adjudication_status"] = state["adjudication_status"]
        record["reviewer_ids"] = state["reviewer_ids"]
        record["adjudicator_id"] = state["adjudicator_id"]
        record["review_event_ids"] = state["review_event_ids"]
        record["review_count"] = state["review_count"]
        resolved_event = state.get("resolved_event")
        record["review_label_status"] = resolved_event.get("status") if resolved_event else None
        record["review_reason"] = resolved_event.get("reason") if resolved_event else None
        records.append(record)

    package_root = Path(__file__).resolve().parents[1]
    product_modules = (
        "application.py",
        "ranking.py",
        "decision.py",
        "models.py",
        "config.py",
        "scanner.py",
        "project_context.py",
        "evidence_explanations.py",
        "evidence_packet.py",
        "mcp_models.py",
        "mcp_server.py",
    )
    fingerprint = hashlib.sha256()
    for name in product_modules:
        content = (package_root / name).read_bytes()
        fingerprint.update(name.encode("utf-8") + b"\0" + content + b"\0")
    implementation = {
        "skillscout_version": __version__,
        "product_source_sha256": fingerprint.hexdigest(),
        "calibration_runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "calibration_review_sha256": hashlib.sha256(
            Path(__file__).with_name("calibration_reviews.py").read_bytes()
        ).hexdigest(),
        "python_version": sys.version.split()[0],
        "relevance_threshold": relevance_threshold(),
        "product_modules": list(product_modules),
    }
    configuration = {
        "candidate_limit": dataset["candidate_limit"],
        "optional_llm": "disabled",
        "catalog_mode": "local_fixture_adapter",
        "network_access": "disabled_by_construction",
        "decision_clock_utc": dataset["evaluation_clock_utc"],
        "include_held_out": bool(include_held_out),
        "held_out_example_count": held_out_count,
        "review_ledger_sha256": reviews_sha256,
        "review_ledger_events": len(review_events),
    }
    result: dict[str, Any] = {
        "schema_version": RUN_SCHEMA,
        "dataset": {
            "id": dataset["dataset_id"],
            "version": dataset["dataset_version"],
            "sha256": dataset_sha256,
            "schema_version": dataset["schema_version"],
            "data_kind": dataset["data_kind"],
        },
        "implementation": implementation,
        "configuration": configuration,
        "examples": records,
        "metrics": compute_calibration_metrics(
            records,
            held_out_included=include_held_out,
            held_out_excluded_count=0 if include_held_out else held_out_count,
        ),
    }
    result["run_id"] = hashlib.sha256(_canonical_json(result)).hexdigest()[:20]
    result["created_at_utc"] = _parse_utc(
        dataset["evaluation_clock_utc"], "evaluation_clock_utc"
    ).isoformat().replace("+00:00", "Z")
    result["integrity_sha256"] = _integrity_hash(result)
    return result


def compute_calibration_metrics(
    records: Sequence[Mapping[str, Any]],
    *,
    held_out_included: bool = False,
    held_out_excluded_count: int = 0,
) -> dict[str, Any]:
    """Report synthetic, development, and held-out measures as separate groups."""
    rows = list(records)
    synthetic = [
        item
        for item in rows
        if item.get("assessment_status") == "judgeable"
        and item.get("label_provenance") == "synthetic_fixture"
        and item.get("expected_decision") in DECISION_VALUES
    ]
    human_development = [
        item
        for item in rows
        if item.get("assessment_status") == "judgeable"
        and item.get("label_provenance") == "human_reviewed"
        and item.get("split") == "development"
        and item.get("expected_decision") in DECISION_VALUES
    ]
    human_held_out = [
        item
        for item in rows
        if item.get("assessment_status") == "judgeable"
        and item.get("label_provenance") == "human_reviewed"
        and item.get("split") == "held_out"
        and item.get("expected_decision") in DECISION_VALUES
    ]
    counts = Counter(str(item.get("assessment_status", "unknown")) for item in rows)
    review_status_counts = Counter(str(item.get("review_status", "unreviewed")) for item in rows)
    candidate_uses = [
        candidate
        for item in rows
        for candidate in item.get("trace", {}).get("candidates", [])
        if candidate.get("decision") == Decision.USE.value
    ]
    unsupported_uses = [
        item for item in candidate_uses if item.get("capability_supported") is not True
    ]
    unsafe_uses = [
        item
        for item in candidate_uses
        if item.get("evidence_status") != "complete"
        or item.get("external_audit_status") != "current"
        or item.get("compatibility") != "compatible"
    ]
    incomplete_search_no_skill = [
        item
        for item in rows
        if item.get("observed_decision") == Decision.NO_SKILL_NEEDED.value
        and item.get("trace", {}).get("search", {}).get("complete") is not True
    ]
    return {
        "counts": {
            "examples": len(rows),
            "judgeable": counts.get("judgeable", 0),
            "incomplete": counts.get("incomplete", 0),
            "ambiguous": counts.get("ambiguous", 0),
            "pending": counts.get("pending", 0),
            "human_reviewed_development_judgeable": len(human_development),
            "human_reviewed_held_out_judgeable": len(human_held_out) if held_out_included else None,
            "synthetic_judgeable": len(synthetic),
            "held_out_examples_excluded": held_out_excluded_count,
            "review_statuses": dict(sorted(review_status_counts.items())),
            "unresolved_conflicts": review_status_counts.get("conflict", 0),
            "resolved_ambiguous_reviews": sum(item.get("review_label_status") == "ambiguous" for item in rows),
            "resolved_unjudgeable_reviews": sum(item.get("review_label_status") == "unjudgeable" for item in rows),
        },
        "evaluation_groups": {
            "synthetic_smoke_test": _metric_group(synthetic, included=True),
            "human_reviewed_development": _metric_group(human_development, included=True),
            "human_reviewed_held_out": (
                _metric_group(human_held_out, included=True)
                if held_out_included
                else {"included": False, "example_count": None, "decision": None, "candidate_ranking": None}
            ),
        },
        "safety_and_evidence_checks": {
            "candidate_use_count": len(candidate_uses),
            "candidate_uses_without_confirmed_capability": {
                "count": len(unsupported_uses),
                "denominator": len(candidate_uses),
                "rate": _ratio(len(unsupported_uses), len(candidate_uses)),
            },
            "candidate_uses_without_complete_safety_or_compatibility_evidence": {
                "count": len(unsafe_uses),
                "denominator": len(candidate_uses),
                "rate": _ratio(len(unsafe_uses), len(candidate_uses)),
            },
            "no_skill_decisions_after_incomplete_search": {
                "count": len(incomplete_search_no_skill),
                "denominator": len(rows),
                "example_ids": [item["example_id"] for item in incomplete_search_no_skill],
            },
        },
        "by_challenge_category": _group_metrics(rows, "challenge_category", held_out_included),
    }


def compare_calibration_runs(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare immutable recorded runs on the same dataset without tuning parameters."""
    _validate_run_artifact(baseline)
    _validate_run_artifact(candidate)
    if baseline["dataset"]["sha256"] != candidate["dataset"]["sha256"]:
        raise CalibrationError("comparison requires runs from the same dataset SHA-256")
    if baseline["configuration"].get("include_held_out") != candidate["configuration"].get("include_held_out"):
        raise CalibrationError("comparison runs must use the same held-out inclusion setting")
    baseline_rows = list(baseline["examples"])
    candidate_rows = list(candidate["examples"])
    baseline_ids = [item["example_id"] for item in baseline_rows]
    candidate_ids = [item["example_id"] for item in candidate_rows]
    if baseline_ids != candidate_ids:
        raise CalibrationError("comparison runs do not contain the same ordered example IDs")
    base_by_id = {item["example_id"]: item for item in baseline_rows}
    cand_by_id = {item["example_id"]: item for item in candidate_rows}
    for example_id in baseline_ids:
        before = base_by_id[example_id]
        after = cand_by_id[example_id]
        for field in (
            "split",
            "challenge_category",
            "assessment_status",
            "label_provenance",
            "expected_decision",
            "expected_candidate_order",
        ):
            if before.get(field) != after.get(field):
                raise CalibrationError(
                    f"comparison runs disagree on fixed dataset label {field} for {example_id}"
                )

    decision_changes: list[dict[str, Any]] = []
    ranking_only_changes: list[dict[str, Any]] = []
    for example_id in baseline_ids:
        old = base_by_id[example_id]
        new = cand_by_id[example_id]
        old_decision = old.get("observed_decision")
        new_decision = new.get("observed_decision")
        old_order = old.get("observed_candidate_order", [])
        new_order = new.get("observed_candidate_order", [])
        if old_decision != new_decision:
            decision_changes.append(
                {
                    "example_id": example_id,
                    "challenge_category": old["challenge_category"],
                    "expected_decision": old.get("expected_decision"),
                    "label_provenance": old.get("label_provenance"),
                    "baseline_decision": old_decision,
                    "candidate_decision": new_decision,
                    "correctness_change": _correctness_change(old, new),
                    "candidate_diagnostic": new.get("diagnostic"),
                }
            )
        elif old_order != new_order:
            ranking_only_changes.append(
                {
                    "example_id": example_id,
                    "challenge_category": old["challenge_category"],
                    "decision": old_decision,
                    "baseline_order": old_order,
                    "candidate_order": new_order,
                    "expected_order": old.get("expected_candidate_order"),
                }
            )

    old_metrics = baseline["metrics"]
    new_metrics = candidate["metrics"]
    group_names = (
        "synthetic_smoke_test",
        "human_reviewed_development",
        "human_reviewed_held_out",
    )
    group_changes = {
        name: _compare_metric_group(
            old_metrics["evaluation_groups"][name],
            new_metrics["evaluation_groups"][name],
        )
        for name in group_names
    }
    category_deltas = _comparison_category_deltas(old_metrics, new_metrics)

    old_violations = {
        item["example_id"]: set(item["quality_violations"])
        for item in baseline_rows
    }
    safety_regressions: list[dict[str, Any]] = []
    evidence_quality_regressions: list[dict[str, Any]] = []
    for item in candidate_rows:
        prior = old_violations[item["example_id"]]
        introduced = sorted(set(item.get("quality_violations", [])) - prior)
        if introduced:
            safety_regressions.append(
                {
                    "example_id": item["example_id"],
                    "challenge_category": item["challenge_category"],
                    "evaluation_group": _evaluation_group_for_record(item),
                    "baseline_violations": sorted(prior),
                    "candidate_violations": sorted(item.get("quality_violations", [])),
                    "new_violations": introduced,
                }
            )
        old = base_by_id[item["example_id"]]
        if (
            _conformity(old) is DecisionConformity.CORRECT
            and _conformity(item) is DecisionConformity.INCORRECT
            and (
                item.get("observed_decision") == Decision.USE.value
                or (
                    item.get("observed_decision") == Decision.NO_SKILL_NEEDED.value
                    and item.get("trace", {}).get("search", {}).get("complete") is not True
                )
            )
        ):
            evidence_quality_regressions.append(
                {
                    "example_id": item["example_id"],
                    "challenge_category": item["challenge_category"],
                    "evaluation_group": _evaluation_group_for_record(item),
                    "expected_decision": item.get("expected_decision"),
                    "baseline_decision": old.get("observed_decision"),
                    "candidate_decision": item.get("observed_decision"),
                    "label_provenance": item.get("label_provenance"),
                    "reason": (
                        "new unsupported or reviewer-disconfirmed USE recommendation"
                        if item.get("observed_decision") == Decision.USE.value
                        else "NO_SKILL_NEEDED after incomplete search"
                    ),
                }
            )
    regression_present = bool(safety_regressions or evidence_quality_regressions)
    regression_groups = {
        item["evaluation_group"]
        for item in [*safety_regressions, *evidence_quality_regressions]
        if item.get("evaluation_group") is not None
    }
    accuracy_improvements = {
        name: (
            detail.get("accuracy", {}).get("delta") is not None
            and detail["accuracy"]["delta"] > 0
            and detail["accuracy"]["candidate_denominator"] > 0
        )
        for name, detail in group_changes.items()
    }

    result: dict[str, Any] = {
        "schema_version": COMPARISON_SCHEMA,
        "dataset": baseline["dataset"],
        "baseline_run_id": baseline["run_id"],
        "candidate_run_id": candidate["run_id"],
        "metric_changes": {
            "evaluation_groups": group_changes,
        },
        "decision_changes": decision_changes,
        "category_changes": category_deltas,
        "ranking_changed_without_decision_change": ranking_only_changes,
        "safety_or_evidence_regressions": safety_regressions,
        "decision_evidence_quality_regressions": evidence_quality_regressions,
        "accuracy_improved_with_safety_or_evidence_regression": {
            "accuracy_improvements_by_group": accuracy_improvements,
            "safety_or_evidence_regression_present": regression_present,
            "groups_with_improvement_and_regression": [
                name for name, improved in accuracy_improvements.items()
                if improved and name in regression_groups
            ],
            "regression_groups": sorted(regression_groups),
            "flag": any(
                improved and name in regression_groups
                for name, improved in accuracy_improvements.items()
            ),
        },
    }
    result["integrity_sha256"] = _integrity_hash(result)
    return result


def load_run_artifact(path: str | Path) -> dict[str, Any]:
    """Read and integrity-check a bounded run artifact for comparison."""
    result = _read_json_file(path, MAX_RUN_BYTES, "calibration run")
    if not isinstance(result, dict):
        raise CalibrationError("calibration run root must be a JSON object")
    _validate_run_artifact(result)
    return result


def render_run_report(run: Mapping[str, Any]) -> str:
    """Render a concise report without combining different label sources."""
    metrics = run["metrics"]
    counts = metrics["counts"]
    groups = metrics["evaluation_groups"]
    include_held_out = run["configuration"]["include_held_out"]
    lines = [
        "# SkillScout offline calibration run",
        "",
        f"- Dataset: `{run['dataset']['id']}` v{run['dataset']['version']} ({run['dataset']['data_kind']})",
        f"- Dataset SHA-256: `{run['dataset']['sha256']}`",
        f"- Run ID: `{run['run_id']}`",
        f"- Implementation source SHA-256: `{run['implementation']['product_source_sha256']}`",
        f"- Relevance threshold: {run['implementation']['relevance_threshold']:.3f}",
        "- Catalog: local fixture adapter; network and optional LLM disabled",
        "",
        "## Human-reviewed development results",
        "",
    ]
    lines.extend(_format_metric_group(groups["human_reviewed_development"], "Human-reviewed development"))
    lines.extend(["", "## Human-reviewed held-out results", ""])
    if include_held_out:
        lines.extend(_format_metric_group(groups["human_reviewed_held_out"], "Human-reviewed held-out"))
        lines.append("Held-out examples were explicitly included in this report. Keep these results out of threshold and vocabulary selection.")
    else:
        lines.append(
            f"Excluded by default: {counts['held_out_examples_excluded']} held-out examples. "
            "Pass `--include-held-out` only for an explicit final evaluation."
        )
    lines.extend(["", "## Synthetic smoke-test results (not recommendation-quality evidence)", ""])
    lines.extend(_format_metric_group(groups["synthetic_smoke_test"], "Synthetic smoke test"))
    lines.extend(
        [
            "",
            "## Dataset disposition",
            "",
            f"- Evaluated examples: {counts['examples']}",
            f"- Judgeable: {counts['judgeable']}",
            f"- Incomplete: {counts['incomplete']}",
            f"- Ambiguous: {counts['ambiguous']}",
            f"- Pending: {counts['pending']}",
            f"- Human-reviewed ambiguous: {counts['resolved_ambiguous_reviews']}",
            f"- Human-reviewed unjudgeable: {counts['resolved_unjudgeable_reviews']}",
            f"- Unresolved review conflicts: {counts['unresolved_conflicts']}",
            f"- Review states: `{json.dumps(counts['review_statuses'], sort_keys=True)}`",
            "",
            "A zero denominator is `n/a`, not a passing score. Accuracy and per-decision rates show explicit denominators in the JSON. False USE rate uses predicted USE decisions as its denominator; false USE share of all judgeable examples is separately recorded. Every group warns when it has fewer than 30 judgeable examples.",
            "",
            "## Safety and evidence checks",
            "",
            _format_rate(
                "Candidate USE without confirmed task capability",
                metrics["safety_and_evidence_checks"]["candidate_uses_without_confirmed_capability"],
            ),
            _format_rate(
                "Candidate USE without complete safety/compatibility evidence",
                metrics["safety_and_evidence_checks"]["candidate_uses_without_complete_safety_or_compatibility_evidence"],
            ),
            f"NO_SKILL_NEEDED after incomplete search: {metrics['safety_and_evidence_checks']['no_skill_decisions_after_incomplete_search']['count']}/{metrics['safety_and_evidence_checks']['no_skill_decisions_after_incomplete_search']['denominator']}",
            "",
            "## By challenge category",
            "",
            "| Category | Human development n / accuracy | Human held-out n / accuracy | Synthetic n / agreement | Incomplete | Ambiguous | Pending |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for category, item in metrics["by_challenge_category"].items():
        development = item["human_reviewed_development"]["decision"]["accuracy"]
        synthetic = item["synthetic_smoke_test"]["decision"]["accuracy"]
        if include_held_out:
            held_out = item["human_reviewed_held_out"]["decision"]["accuracy"]
            held_text = f"{held_out['denominator']} / {_percent(held_out['value'])}"
        else:
            held_text = "excluded"
        lines.append(
            f"| {category} | {development['denominator']} / {_percent(development['value'])} | "
            f"{held_text} | {synthetic['denominator']} / {_percent(synthetic['value'])} | "
            f"{item['incomplete']} | {item['ambiguous']} | {item['pending']} |"
        )
    mismatches = [
        item
        for item in run["examples"]
        if item.get("diagnostic", {}).get("status") == "incorrect"
    ]
    lines.extend(["", "## Incorrect judgeable examples", ""])
    if not mismatches:
        lines.append("None in the recorded expected labels.")
    else:
        for item in mismatches:
            diagnostic = item["diagnostic"]
            lines.append(
                f"- `{item['example_id']}` ({item['label_provenance']}): expected "
                f"{item['expected_decision']}, observed {item['observed_decision']}; "
                f"likely stage(s): {', '.join(diagnostic['likely_stages']) or 'unknown'}. "
                f"{diagnostic['suggested_follow_up']}"
            )
            lines.append(f"  Expected rationale: {item['expected_rationale']}")
            lines.append(f"  Evidence basis: {'; '.join(item['evidence_basis'])}")
    lines.append("")
    return "\n".join(lines)


def render_comparison_report(comparison: Mapping[str, Any]) -> str:
    """Render decision, ranking, category, and safety/evidence deltas."""
    delta = comparison["metric_changes"]
    lines = [
        "# SkillScout calibration comparison",
        "",
        f"- Dataset: `{comparison['dataset']['id']}` v{comparison['dataset']['version']}",
        f"- Baseline run: `{comparison['baseline_run_id']}`",
        f"- Candidate run: `{comparison['candidate_run_id']}`",
        "",
        "## Metric changes by label source",
        "",
    ]
    for name, label in (
        ("human_reviewed_development", "Human-reviewed development"),
        ("human_reviewed_held_out", "Human-reviewed held-out"),
        ("synthetic_smoke_test", "Synthetic smoke test (not quality evidence)"),
    ):
        group = delta["evaluation_groups"][name]
        lines.extend([f"### {label}", ""])
        if not group["included"]:
            lines.append("Held-out metrics were excluded from both runs.")
            lines.append("")
            continue
        lines.extend(
            [
                _format_delta("Decision accuracy", group["accuracy"]),
                _format_delta("False USE rate", group["decision"]["false_use_rate"]),
                _format_delta("False NO_SKILL_NEEDED rate", group["decision"]["false_no_skill_needed_rate"]),
                _format_delta("Unnecessary REVIEW rate", group["decision"]["unnecessary_review_rate"]),
                _format_delta("Top-1 candidate ranking", group["candidate_ranking"]["top1_accuracy"]),
                f"- Small-sample warning: {group['small_sample_warning_baseline']} baseline / {group['small_sample_warning_candidate']} candidate",
                "",
            ]
        )
        for decision, measures in group["decision"]["per_decision_precision_recall"].items():
            lines.append(
                f"- {decision}: precision Δ {_format_delta_cell(measures['precision'])}; "
                f"recall Δ {_format_delta_cell(measures['recall'])}"
            )
        lines.append("")
    lines.extend([
        "## Decision changes",
        "",
    ])
    if comparison["decision_changes"]:
        for item in comparison["decision_changes"]:
            lines.append(
                f"- `{item['example_id']}` ({item['challenge_category']}): "
                f"{item['baseline_decision']} → {item['candidate_decision']} "
                f"({item['correctness_change']}; {item['label_provenance']})."
            )
    else:
        lines.append("No final decisions changed.")
    lines.extend(["", "## Category changes", ""])
    if comparison["category_changes"]:
        lines.append("Category-level metric changes are kept within their source group in the JSON artifact.")
        for category, item in comparison["category_changes"].items():
            lines.append(f"- `{category}`")
            for group_name, group in item["evaluation_groups"].items():
                if not group["included"]:
                    lines.append(f"  - {group_name}: held-out metrics excluded")
                else:
                    lines.append(
                        f"  - {group_name}: accuracy {_format_delta_cell(group['accuracy'])}; "
                        f"false USE {_format_delta_cell(group['decision']['false_use_rate'])}; "
                        f"false NO {_format_delta_cell(group['decision']['false_no_skill_needed_rate'])}; "
                        f"unnecessary REVIEW {_format_delta_cell(group['decision']['unnecessary_review_rate'])}; "
                        f"n={group['baseline_example_count']} → {group['candidate_example_count']}"
                    )
    else:
        lines.append("No challenge categories were present.")
    lines.extend(["", "## Ranking-only changes", ""])
    if comparison["ranking_changed_without_decision_change"]:
        for item in comparison["ranking_changed_without_decision_change"]:
            lines.append(
                f"- `{item['example_id']}`: {item['baseline_order']} → {item['candidate_order']} "
                f"(decision remained {item['decision']})."
            )
    else:
        lines.append("No candidate ordering changed while leaving the final decision unchanged.")
    lines.extend(["", "## Safety and evidence regressions", ""])
    if comparison["safety_or_evidence_regressions"]:
        for item in comparison["safety_or_evidence_regressions"]:
            lines.append(
                f"- `{item['example_id']}`: {', '.join(item['new_violations'])}."
            )
    else:
        lines.append("No new decision/evidence invariant violations were detected.")
    lines.extend(["", "## Reviewer-disconfirmed evidence regressions", ""])
    if comparison["decision_evidence_quality_regressions"]:
        for item in comparison["decision_evidence_quality_regressions"]:
            lines.append(
                f"- `{item['example_id']}` ({item['label_provenance']}): {item['reason']} "
                f"changed from {item['baseline_decision']} to {item['candidate_decision']}."
            )
    else:
        lines.append("No new false USE or NO_SKILL_NEEDED-after-incomplete-search regression was detected.")
    summary = comparison["accuracy_improved_with_safety_or_evidence_regression"]
    lines.extend(
        [
            "",
            "Any source-group accuracy improvement paired with a safety/evidence regression: "
            f"**{summary['flag']}** (groups: {', '.join(summary['groups_with_improvement_and_regression']) or 'none'}).",
            "",
        ]
    )
    return "\n".join(lines)


def write_new_artifact(path: str | Path, content: str) -> Path:
    """Create a new artifact without replacing any existing file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CalibrationError(f"refusing to overwrite existing calibration artifact: {target}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
    return target


def _run_example(dataset: Mapping[str, Any], example: Mapping[str, Any]) -> dict[str, Any]:
    skills = [CatalogSkill.model_validate(item["skill"]) for item in example["search"]["candidates"]]
    details = {item["skill"]["id"]: item for item in example["search"]["candidates"]}
    searches: list[dict[str, Any]] = []
    initial_ranking: dict[str, RankedSkill] = {}
    refinements: dict[str, RankedSkill] = {}
    retrieved_details: dict[str, SkillDetail] = {}
    clock = dataset["evaluation_clock_utc"]

    class SearchResults(list):
        def __init__(self, values: list[CatalogSkill], complete: bool) -> None:
            super().__init__(values)
            self.complete = complete

    class FixtureCatalog:
        def __enter__(self) -> FixtureCatalog:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def search(self, query: str, *, limit: int) -> SearchResults:
            searches.append({"requested_limit": limit, "query_was_redacted": query != example["task"]})
            return SearchResults(skills, example["search"]["complete"])

        def get_detail(self, skill: CatalogSkill) -> SkillDetail:
            candidate = details[skill.id]
            evidence = candidate["evidence"]
            if evidence["status"] == "unavailable":
                raise CatalogUnavailableError("fixture detail is marked unavailable")
            files = [
                SkillFile(path=item["path"], contents=item["text"])
                for item in evidence["files"]
            ]
            detail = SkillDetail(
                id=skill.id,
                source=skill.source,
                slug=skill.slug,
                hash=evidence.get("content_hash"),
                files=files,
            )
            retrieved_details[skill.id] = detail
            return detail

        def get_audits(self, skill: CatalogSkill):
            audit = details[skill.id]["audit"]
            return ([_audit_finding(item) for item in audit["findings"]], audit["status"], audit.get("notes", []))

    catalog = FixtureCatalog()
    candidate_limit = dataset["candidate_limit"]
    config = Config(
        catalog_base_url="https://offline.fixture.invalid",
        catalog_token=None,
        candidate_limit=candidate_limit,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        max_response_bytes=100_000,
        retry_count=0,
        llm_api_key=None,
        llm_base_url="https://offline.fixture.invalid/v1",
        llm_model="disabled",
        llm_send_task=False,
        request_timeout_seconds=1.0,
    )

    facts = list(example["project_facts"])
    project_path = f"calibration://{example['id']}"

    def read_fixture_project(_path: str | Path) -> ProjectSnapshot:
        return ProjectSnapshot(path=project_path, facts=facts)

    def observe_candidate(candidate, detail, task, project) -> None:
        prior = initial_ranking.get(candidate.skill.id)
        if prior is None:
            return
        refinements[candidate.skill.id] = refine_rank(task, prior, detail)

    if not _task_is_routine_fixture(example["task"]):
        initial = rank_catalog_results(example["task"], skills)
        initial_ranking = {item.skill.id: item for item in initial}

    with _fixed_clock(clock):
        report = prepare_recommendation(
            example["task"],
            project_path,
            "calibration",
            allow_llm=False,
            config_loader=lambda: config,
            project_context_reader=read_fixture_project,
            catalog_client_factory=lambda _config: catalog,
            detail_observer=observe_candidate,
        )

        candidate_traces: list[dict[str, Any]] = []
        for candidate in report.candidates:
            detail = retrieved_details.get(candidate.skill.id)
            explanation = explain_candidate(candidate)
            packet = build_evidence_packet(
                candidate,
                detail,
                example["task"],
                report.project,
            )
            refined = refinements.get(candidate.skill.id)
            candidate_traces.append(
                {
                    "stable_id": candidate.skill.id,
                    "rank": candidate.rank,
                    "relevance_score": candidate.relevance_score,
                    "listing_score": (
                        refined.listing_score if refined is not None else initial_ranking.get(candidate.skill.id, RankedSkill(candidate.skill, 0.0, [])).score
                    ),
                    "capability_supported": (
                        refined.capability_supported if refined is not None else None
                    ),
                    "task_fit_rationale": candidate.task_fit_rationale[:12],
                    "decision": candidate.decision.value,
                    "decision_reasons": candidate.decision_reasons[:20],
                    "evidence_status": candidate.evidence.evidence_status,
                    "external_audit_status": candidate.evidence.external_audit_status,
                    "audit_findings": [
                        {
                            "status": item.status,
                            "risk_level": item.risk_level,
                            "revision": item.revision,
                        }
                        for item in candidate.evidence.external_audits
                    ],
                    "compatibility": candidate.compatibility.status,
                    "local_findings": [
                        {
                            "check_id": item.check_id,
                            "severity": item.severity.value,
                            "summary": item.summary,
                        }
                        for item in candidate.evidence.local_findings
                    ],
                    "evidence_statuses": [
                        {
                            "category": item.category,
                            "status": item.status,
                            "summary": item.summary,
                        }
                        for item in explanation.statuses
                    ],
                    "unresolved_evidence": [
                        {
                            "category": item.category,
                            "status": item.status,
                            "reason": item.reason,
                        }
                        for item in explanation.unresolved
                    ],
                    "next_steps": explanation.next_steps[:20],
                    "evidence_packet": {
                        "status": packet.status,
                        "explanation": packet.explanation,
                        "excerpt_reasons": [item.relevance_reason for item in packet.excerpts],
                    },
                }
            )

    listing_order = [item.skill.id for item in sorted(initial_ranking.values(), key=lambda item: item.score, reverse=True)]
    observed_order = [item["stable_id"] for item in candidate_traces]
    search_called = bool(searches)
    search_complete = bool(example["search"]["complete"]) if search_called else True
    trace = {
        "task_is_routine": _task_is_routine_fixture(example["task"]),
        "search": {
            "called": search_called,
            "complete": search_complete,
            "coverage_status": example["search"]["coverage_status"],
            "requested_limit": searches[0]["requested_limit"] if searches else None,
            "query_was_redacted": searches[0]["query_was_redacted"] if searches else None,
            "returned_count": len(skills) if search_called else 0,
            "returned_ids": [item.id for item in skills] if search_called else [],
        },
        "listing_ranking": [
            {
                "stable_id": item.skill.id,
                "score": item.score,
                "rationale": item.rationale[:10],
            }
            for item in sorted(initial_ranking.values(), key=lambda item: item.score, reverse=True)
        ],
        "candidates": candidate_traces,
        "report_summary": report.summary,
        "report_limitations": report.limitations[:20],
    }
    record: dict[str, Any] = {
        "example_id": example["id"],
        "split": example["split"],
        "challenge_category": example["challenge_category"],
        "assessment_status": example["assessment_status"],
        "label_provenance": example["label_provenance"],
        "expected_decision": example["expected_decision"],
        "expected_candidate_order": example["expected_candidate_order"],
        "expected_rationale": example["rationale"],
        "evidence_basis": example["evidence_basis"],
        "observed_decision": report.decision.value,
        "listing_candidate_order": listing_order,
        "observed_candidate_order": observed_order,
        "trace": trace,
    }
    record["decision_conformity"] = _conformity(record).value
    record["quality_violations"] = _quality_violations(record)
    record["diagnostic"] = _diagnose(example, record)
    return record


def _effective_review_label(
    source_example: Mapping[str, Any], state: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply a resolved ledger label to a copy while retaining the source template."""
    example = dict(source_example)
    event = state.get("resolved_event")
    if event is None:
        return example
    if event["status"] == "judgeable":
        example.update(
            {
                "assessment_status": "judgeable",
                "label_provenance": "human_reviewed",
                "reviewer": ",".join(state["reviewer_ids"]),
                "expected_decision": event["decision"],
                "expected_candidate_order": event["candidate_order"],
                "rationale": event["rationale"],
                "evidence_basis": list(event["evidence_basis"]),
            }
        )
    else:
        example.update(
            {
                "assessment_status": "ambiguous" if event["status"] == "ambiguous" else "incomplete",
                "label_provenance": "pending",
                "reviewer": None,
                "expected_decision": None,
                "expected_candidate_order": None,
                "rationale": event.get("rationale") or event.get("reason", ""),
                "evidence_basis": list(event.get("evidence_basis", [])),
            }
        )
    return example


def _metric_group(rows: Sequence[Mapping[str, Any]], *, included: bool) -> dict[str, Any]:
    return {
        "included": included,
        "example_count": len(rows),
        "judgeable_examples": len(rows),
        "small_sample_warning": len(rows) < 30,
        "decision": _decision_metrics(rows),
        "candidate_ranking": _ranking_metrics(rows),
    }


def _validate_dataset(data: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "dataset_id",
        "dataset_version",
        "data_kind",
        "description",
        "candidate_limit",
        "evaluation_clock_utc",
        "examples",
    }
    if set(data) != required:
        raise CalibrationError("calibration dataset fields do not match the supported schema")
    if not isinstance(data["schema_version"], str) or data["schema_version"] not in DATASET_SCHEMAS:
        raise CalibrationError("unsupported calibration dataset schema_version")
    schema_version = data["schema_version"]
    _require_text(data, "dataset_id", 100)
    _require_text(data, "dataset_version", 40)
    _require_text(data, "description", 1000)
    _require_enum(
        data["data_kind"],
        {"synthetic_smoke_test_only", "human_reviewed", "mixed"},
        "data_kind",
    )
    if schema_version == DATASET_SCHEMA_V2 and data["data_kind"] != "human_reviewed":
        raise CalibrationError("dataset v2 is reserved for pending human-review examples")
    if not _is_int(data["candidate_limit"]) or not 1 <= data["candidate_limit"] <= 5:
        raise CalibrationError("candidate_limit must be an integer from 1 to 5")
    _parse_utc(data["evaluation_clock_utc"], "evaluation_clock_utc")
    examples = data["examples"]
    minimum_examples = 0 if schema_version == DATASET_SCHEMA_V2 else 1
    if not isinstance(examples, list) or len(examples) < minimum_examples or len(examples) > MAX_EXAMPLES:
        qualifier = "list" if minimum_examples == 0 else "non-empty list"
        raise CalibrationError(f"examples must be a {qualifier} of at most {MAX_EXAMPLES} items")
    identifiers: set[str] = set()
    task_by_split: dict[str, set[str]] = {"development": set(), "held_out": set()}
    for index, example in enumerate(examples):
        _validate_example(example, data, index)
        if example["id"] in identifiers:
            raise CalibrationError(f"duplicate calibration example ID: {example['id']}")
        identifiers.add(example["id"])
        if data["data_kind"] == "human_reviewed" and example["label_provenance"] != "pending":
            raise CalibrationError("human-review datasets must keep labels pending in the dataset; use the append-only review ledger")
        if data["data_kind"] == "synthetic_smoke_test_only" and example["label_provenance"] == "human_reviewed":
            raise CalibrationError("synthetic-only datasets cannot contain human-reviewed labels")
        if schema_version == DATASET_SCHEMA_V2 and (
            example["assessment_status"] != "pending" or example["label_provenance"] != "pending"
        ):
            raise CalibrationError("dataset v2 examples must remain pending until reviewed in the ledger")
        if schema_version == DATASET_SCHEMA_V2:
            normalized_task = " ".join(example["task"].casefold().split())
            other_split = "held_out" if example["split"] == "development" else "development"
            if normalized_task in task_by_split[other_split]:
                raise CalibrationError("the same normalized task cannot appear in development and held_out splits")
            task_by_split[example["split"]].add(normalized_task)


def _validate_example(value: Any, dataset: Mapping[str, Any], index: int) -> None:
    required = {
        "id",
        "split",
        "task",
        "challenge_category",
        "assessment_status",
        "label_provenance",
        "reviewer",
        "expected_decision",
        "expected_candidate_order",
        "rationale",
        "evidence_basis",
        "project_facts",
        "search",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise CalibrationError(f"example[{index}] fields do not match the supported dataset schema")
    identifier = value["id"]
    if not isinstance(identifier, str) or not EXAMPLE_ID_RE.fullmatch(identifier):
        raise CalibrationError(f"example[{index}] has an invalid stable ID")
    _require_enum(value["split"], SPLITS, f"example {identifier} split")
    _require_text(value, "task", 4000)
    _require_text(value, "challenge_category", 80)
    _require_enum(value["assessment_status"], ASSESSMENT_STATUSES, f"example {identifier} assessment_status")
    _require_enum(value["label_provenance"], LABEL_PROVENANCE, f"example {identifier} label_provenance")
    expected = value["expected_decision"]
    if expected is not None and (not isinstance(expected, str) or expected not in DECISION_VALUES):
        raise CalibrationError(f"example {identifier} has an invalid expected_decision")
    is_pending = value["assessment_status"] == "pending" and value["label_provenance"] == "pending"
    if is_pending:
        if dataset["schema_version"] != DATASET_SCHEMA_V2:
            raise CalibrationError(f"pending example {identifier} is only supported in dataset v2")
        if expected is not None or value["reviewer"] is not None:
            raise CalibrationError(f"pending example {identifier} cannot contain a resolved label or reviewer")
    elif value["assessment_status"] == "judgeable":
        if expected is None or value["label_provenance"] not in {"human_reviewed", "synthetic_fixture"}:
            raise CalibrationError(f"judgeable example {identifier} needs an explicit sourced decision")
    elif expected is not None or value["label_provenance"] != "pending":
        raise CalibrationError(
            f"incomplete or ambiguous example {identifier} must have no expected decision and remain pending"
        )
    if value["label_provenance"] == "human_reviewed":
        _require_text(value, "reviewer", 120)
    elif value["reviewer"] is not None:
        raise CalibrationError(f"example {identifier} cannot name a reviewer for a non-human label")
    if is_pending:
        if not isinstance(value["rationale"], str) or len(value["rationale"]) > 1000:
            raise CalibrationError(f"pending example {identifier} rationale must be empty or bounded text")
    else:
        _require_text(value, "rationale", 1000)
    if not isinstance(value["evidence_basis"], list) or (not is_pending and not value["evidence_basis"]):
        raise CalibrationError(f"example {identifier} needs explicit evidence_basis entries")
    if len(value["evidence_basis"]) > 20 or any(
        not isinstance(item, str) or not item.strip() or len(item) > 500
        for item in value["evidence_basis"]
    ):
        raise CalibrationError(f"example {identifier} has malformed evidence_basis entries")
    facts = value["project_facts"]
    if not isinstance(facts, list) or len(facts) > 50 or any(
        not isinstance(item, str) or len(item) > 300 for item in facts
    ):
        raise CalibrationError(f"example {identifier} has malformed project_facts")
    search = value["search"]
    if not isinstance(search, dict) or set(search) != {"complete", "coverage_status", "candidates"}:
        raise CalibrationError(f"example {identifier} has malformed search fixture")
    if not isinstance(search["complete"], bool):
        raise CalibrationError(f"example {identifier} has invalid search completeness")
    _require_enum(search["coverage_status"], SEARCH_COVERAGE, f"example {identifier} coverage_status")
    if search["coverage_status"] == "incomplete" and search["complete"]:
        raise CalibrationError(f"example {identifier} cannot mark a complete search as incomplete coverage")
    if search["coverage_status"] == "adequate" and not search["complete"]:
        raise CalibrationError(f"example {identifier} cannot claim adequate coverage for incomplete search")
    candidates = search["candidates"]
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise CalibrationError(f"example {identifier} candidates must be a list of at most {MAX_CANDIDATES}")
    candidate_ids: set[str] = set()
    for candidate in candidates:
        _validate_candidate(candidate, identifier)
        candidate_id = candidate["skill"]["id"]
        if candidate_id in candidate_ids:
            raise CalibrationError(f"example {identifier} has duplicate candidate IDs")
        candidate_ids.add(candidate_id)
    expected_order = value["expected_candidate_order"]
    if expected_order is not None:
        if (
            not isinstance(expected_order, list)
            or not expected_order
            or any(not isinstance(item, str) for item in expected_order)
        ):
            raise CalibrationError(f"example {identifier} expected_candidate_order must be null or non-empty")
        if len(set(expected_order)) != len(expected_order) or not set(expected_order) <= candidate_ids:
            raise CalibrationError(f"example {identifier} expected candidate order must reference known candidates once")
        if len(expected_order) > dataset["candidate_limit"]:
            raise CalibrationError(
                f"example {identifier} expected candidate order exceeds the configured candidate limit"
            )
    if expected == Decision.NO_SKILL_NEEDED.value and value["assessment_status"] == "judgeable":
        routine = _task_is_routine_fixture(value["task"])
        if routine:
            if search["coverage_status"] != "not_needed":
                raise CalibrationError(f"routine no-skill example {identifier} must mark search not_needed")
        else:
            if search["complete"] is not True or search["coverage_status"] != "adequate":
                raise CalibrationError(
                    f"NO_SKILL_NEEDED example {identifier} requires complete, adequate search coverage"
                )
            if any(item["evidence"]["status"] != "complete" for item in candidates):
                raise CalibrationError(
                    f"NO_SKILL_NEEDED example {identifier} requires complete candidate evidence"
                )


def _validate_candidate(value: Any, example_id: str) -> None:
    required = {"skill", "evidence", "audit"}
    if not isinstance(value, dict) or set(value) != required:
        raise CalibrationError(f"candidate in {example_id} must contain skill, evidence, and audit")
    skill = value["skill"]
    skill_required = {"id", "name", "source", "slug", "description"}
    if not isinstance(skill, dict) or set(skill) != skill_required:
        raise CalibrationError(f"candidate in {example_id} has malformed skill metadata")
    for name, maximum in (("id", 500), ("name", 200), ("source", 300), ("slug", 200), ("description", 1000)):
        _require_text(skill, name, maximum)
    try:
        CatalogSkill.model_validate(skill)
    except ValidationError as exc:
        raise CalibrationError(f"candidate in {example_id} has invalid catalog metadata") from exc
    evidence = value["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"status", "content_hash", "scope", "files"}:
        raise CalibrationError(f"candidate in {example_id} has malformed evidence")
    _require_enum(evidence["status"], DETAIL_STATUSES, f"candidate in {example_id} evidence status")
    _require_enum(evidence["scope"], {"complete_synthetic_detail", "selected_excerpts"}, f"candidate in {example_id} evidence scope")
    if evidence["content_hash"] is not None and (
        not isinstance(evidence["content_hash"], str) or not evidence["content_hash"].strip()
    ):
        raise CalibrationError(f"candidate in {example_id} has invalid content_hash")
    files = evidence["files"]
    if not isinstance(files, list) or len(files) > 10:
        raise CalibrationError(f"candidate in {example_id} files must be a list of at most 10 excerpts")
    total_chars = 0
    for file in files:
        if not isinstance(file, dict) or set(file) != {"path", "text"}:
            raise CalibrationError(f"candidate in {example_id} has a malformed evidence excerpt")
        _require_text(file, "path", 500)
        _require_text(file, "text", MAX_EVIDENCE_CHARS)
        total_chars += len(file["text"])
        try:
            SkillFile(path=file["path"], contents=file["text"])
        except ValidationError as exc:
            raise CalibrationError(f"candidate in {example_id} has an invalid evidence file path or body") from exc
    if total_chars > MAX_EVIDENCE_CHARS:
        raise CalibrationError(f"candidate in {example_id} evidence exceeds {MAX_EVIDENCE_CHARS} characters")
    if evidence["status"] == "complete" and (
        not evidence["content_hash"] or not any(item["path"].casefold() == "skill.md" for item in files)
    ):
        raise CalibrationError(f"complete evidence in {example_id} needs a content hash and SKILL.md excerpt")
    if evidence["status"] == "unavailable" and files:
        raise CalibrationError(f"unavailable evidence in {example_id} cannot contain excerpts")
    audit = value["audit"]
    if not isinstance(audit, dict) or set(audit) != {"status", "findings", "notes"}:
        raise CalibrationError(f"candidate in {example_id} has malformed audit data")
    _require_enum(audit["status"], AUDIT_STATUSES, f"candidate in {example_id} audit status")
    if not isinstance(audit["findings"], list) or len(audit["findings"]) > 10:
        raise CalibrationError(f"candidate in {example_id} audit findings must be a bounded list")
    if audit["status"] == "none" and audit["findings"]:
        raise CalibrationError(f"candidate in {example_id} cannot include findings for audit status none")
    if not isinstance(audit["notes"], list) or any(
        not isinstance(note, str) or len(note) > 500 for note in audit["notes"]
    ):
        raise CalibrationError(f"candidate in {example_id} has malformed audit notes")
    for finding in audit["findings"]:
        if not isinstance(finding, dict) or not {"provider", "status", "summary", "audited_at", "revision"} <= set(finding):
            raise CalibrationError(f"candidate in {example_id} has malformed audit finding")
        _audit_finding(finding)


def _run_artifact_validity(example: Mapping[str, Any]) -> None:
    _require_enum(example.get("assessment_status"), ASSESSMENT_STATUSES, "recorded assessment_status")
    _require_enum(example.get("label_provenance"), LABEL_PROVENANCE, "recorded label_provenance")
    expected = example.get("expected_decision")
    if expected is not None and (not isinstance(expected, str) or expected not in DECISION_VALUES):
        raise CalibrationError("recorded run contains an invalid expected decision")
    if not isinstance(example.get("observed_decision"), str) or example.get("observed_decision") not in DECISION_VALUES:
        raise CalibrationError("recorded run contains an invalid observed decision")
    if example["assessment_status"] == "judgeable":
        if expected is None or example["label_provenance"] not in {"human_reviewed", "synthetic_fixture"}:
            raise CalibrationError("recorded judgeable run example has no sourced expected decision")
    elif expected is not None or example["label_provenance"] != "pending":
        raise CalibrationError("recorded incomplete or ambiguous example has a resolved label")
    if not isinstance(example.get("trace"), dict) or not isinstance(example.get("example_id"), str):
        raise CalibrationError("recorded run contains a malformed example trace")
    _require_enum(
        example.get("review_status"),
        {"unreviewed", "awaiting_independent_review", "consensus", "conflict", "adjudicated"},
        "recorded human-review status",
    )
    _require_enum(
        example.get("adjudication_status"),
        {"not_started", "awaiting_second_review", "not_needed", "required", "resolved"},
        "recorded adjudication status",
    )
    if not isinstance(example.get("reviewer_ids"), list) or any(
        not isinstance(item, str) for item in example["reviewer_ids"]
    ):
        raise CalibrationError("recorded run contains invalid reviewer identities")
    if not isinstance(example.get("review_event_ids"), list) or any(
        not isinstance(item, str) for item in example["review_event_ids"]
    ):
        raise CalibrationError("recorded run contains invalid review event references")
    if example.get("adjudicator_id") is not None and not isinstance(example.get("adjudicator_id"), str):
        raise CalibrationError("recorded run contains an invalid adjudicator identity")
    if not _is_int(example.get("review_count")) or example["review_count"] < 0:
        raise CalibrationError("recorded run contains an invalid review count")
    review_label_status = example.get("review_label_status")
    if review_label_status is not None and (
        not isinstance(review_label_status, str)
        or review_label_status not in {"judgeable", "ambiguous", "unjudgeable"}
    ):
        raise CalibrationError("recorded run contains an invalid resolved review label status")
    review_reason = example.get("review_reason")
    if review_reason is not None and (not isinstance(review_reason, str) or len(review_reason) > 1000):
        raise CalibrationError("recorded run contains an invalid review reason")
    if example["label_provenance"] == "human_reviewed" and (
        review_label_status != "judgeable"
        or example["review_status"] not in {"consensus", "adjudicated"}
        or not example["review_event_ids"]
    ):
        raise CalibrationError("human-reviewed run labels must have a resolved, auditable ledger history")
    if not isinstance(example.get("challenge_category"), str) or not isinstance(example.get("split"), str):
        raise CalibrationError("recorded run contains invalid example grouping fields")
    _require_enum(example["split"], SPLITS, "recorded split")
    diagnostic = example.get("diagnostic")
    if not isinstance(diagnostic, dict) or diagnostic.get("status") not in {"correct", "incorrect", "not_judged"}:
        raise CalibrationError("recorded run contains a malformed stage diagnostic")
    conformity = _conformity(example)
    expected_diagnostic_status = {
        DecisionConformity.CORRECT: "correct",
        DecisionConformity.INCORRECT: "incorrect",
        DecisionConformity.INDETERMINATE: "not_judged",
    }[conformity]
    if example.get("decision_conformity") != conformity.value:
        raise CalibrationError("recorded run decision conformity does not match its labels")
    if diagnostic["status"] != expected_diagnostic_status:
        raise CalibrationError("recorded stage diagnostic does not match decision conformity")
    expected_order = example.get("expected_candidate_order")
    if expected_order is not None and (
        not isinstance(expected_order, list) or any(not isinstance(item, str) for item in expected_order)
    ):
        raise CalibrationError("recorded run contains an invalid expected candidate order")
    observed_order = example.get("observed_candidate_order")
    if not isinstance(observed_order, list) or any(not isinstance(item, str) for item in observed_order):
        raise CalibrationError("recorded run contains an invalid observed candidate order")
    violations = example.get("quality_violations")
    if not isinstance(violations, list) or any(not isinstance(item, str) for item in violations):
        raise CalibrationError("recorded run contains invalid quality violation data")
    trace = example["trace"]
    search = trace.get("search")
    if not isinstance(search, dict) or type(search.get("complete")) is not bool:
        raise CalibrationError("recorded run contains invalid search trace data")
    if type(search.get("called")) is not bool:
        raise CalibrationError("recorded run contains invalid search trace data")
    if not isinstance(trace.get("candidates"), list):
        raise CalibrationError("recorded run contains invalid candidate trace data")
    for candidate in trace["candidates"]:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("decision"), str) or candidate["decision"] not in DECISION_VALUES:
            raise CalibrationError("recorded run contains an invalid candidate decision")
        if not isinstance(candidate.get("stable_id"), str):
            raise CalibrationError("recorded run contains an invalid candidate identity")
        if not _is_int(candidate.get("rank")) or candidate["rank"] < 1:
            raise CalibrationError("recorded run contains an invalid candidate rank")
        score = candidate.get("relevance_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
            raise CalibrationError("recorded run contains an invalid candidate relevance score")
        if candidate.get("capability_supported") is not True and candidate.get("capability_supported") is not False and candidate.get("capability_supported") is not None:
            raise CalibrationError("recorded run contains an invalid capability evidence status")
        _require_enum(candidate.get("evidence_status"), {"complete", "incomplete", "unknown"}, "candidate evidence status")
        _require_enum(candidate.get("external_audit_status"), {"current", "stale", "unbound", "unavailable", "none"}, "candidate audit status")
        _require_enum(candidate.get("compatibility"), {"compatible", "partial", "unknown", "incompatible"}, "candidate compatibility status")
        local_findings = candidate.get("local_findings")
        audits = candidate.get("audit_findings")
        if not isinstance(local_findings, list) or not isinstance(audits, list):
            raise CalibrationError("recorded run contains malformed candidate safety evidence")
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("severity"), str)
            or item["severity"] not in {"info", "low", "medium", "high", "critical"}
            for item in local_findings
        ):
            raise CalibrationError("recorded run contains malformed local safety findings")
        if any(not isinstance(item, dict) for item in audits):
            raise CalibrationError("recorded run contains malformed audit findings")
    if violations != _quality_violations(example):
        raise CalibrationError("recorded run quality violations do not match the candidate trace")


def _validate_run_artifact(run: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "dataset",
        "implementation",
        "configuration",
        "examples",
        "metrics",
        "run_id",
        "created_at_utc",
        "integrity_sha256",
    }
    if set(run) != required or run.get("schema_version") != RUN_SCHEMA:
        raise CalibrationError("unsupported or malformed calibration run artifact")
    dataset = run.get("dataset")
    if (
        not isinstance(dataset, dict)
        or set(dataset) != {"id", "version", "sha256", "schema_version", "data_kind"}
        or not isinstance(dataset.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", dataset["sha256"])
        or not isinstance(dataset.get("id"), str)
        or not isinstance(dataset.get("version"), str)
        or not isinstance(dataset.get("schema_version"), str)
        or dataset.get("schema_version") not in DATASET_SCHEMAS
        or not isinstance(dataset.get("data_kind"), str)
        or dataset["data_kind"] not in {"synthetic_smoke_test_only", "human_reviewed", "mixed"}
    ):
        raise CalibrationError("calibration run has no dataset identity")
    if dataset["schema_version"] == DATASET_SCHEMA_V2 and dataset["data_kind"] != "human_reviewed":
        raise CalibrationError("dataset v2 run metadata must identify a human-review dataset")
    if not isinstance(run.get("implementation"), dict) or not isinstance(run.get("configuration"), dict):
        raise CalibrationError("calibration run has malformed implementation or configuration metadata")
    configuration = run["configuration"]
    if type(configuration.get("include_held_out")) is not bool:
        raise CalibrationError("calibration run has invalid held-out inclusion metadata")
    if not _is_int(configuration.get("held_out_example_count")) or configuration["held_out_example_count"] < 0:
        raise CalibrationError("calibration run has invalid held-out sample count")
    examples = run.get("examples")
    if not isinstance(examples, list):
        raise CalibrationError("calibration run example records must be a list")
    ids: list[str] = []
    for example in examples:
        if not isinstance(example, dict):
            raise CalibrationError("calibration run contains a malformed example")
        _run_artifact_validity(example)
        ids.append(example["example_id"])
    if len(ids) != len(set(ids)):
        raise CalibrationError("calibration run contains duplicate example IDs")
    if dataset["schema_version"] == DATASET_SCHEMA_V2 and any(
        item["label_provenance"] == "synthetic_fixture" for item in examples
    ):
        raise CalibrationError("human-review v2 runs cannot classify a synthetic example as human data")
    if dataset["data_kind"] == "synthetic_smoke_test_only" and any(
        item["label_provenance"] == "human_reviewed" for item in examples
    ):
        raise CalibrationError("synthetic-only runs cannot contain human-reviewed labels")
    if not configuration["include_held_out"] and any(item["split"] == "held_out" for item in examples):
        raise CalibrationError("run includes held-out examples without explicit inclusion")
    run_id_material = {
        key: value
        for key, value in run.items()
        if key not in {"run_id", "created_at_utc", "integrity_sha256"}
    }
    expected_run_id = hashlib.sha256(_canonical_json(run_id_material)).hexdigest()[:20]
    if run.get("run_id") != expected_run_id:
        raise CalibrationError("calibration run ID does not match its recorded source and metrics")
    try:
        created_at = _parse_utc(run.get("created_at_utc"), "created_at_utc")
        decision_clock = _parse_utc(run["configuration"].get("decision_clock_utc"), "decision_clock_utc")
    except (KeyError, CalibrationError) as exc:
        raise CalibrationError("calibration run has invalid deterministic clock metadata") from exc
    if created_at != decision_clock:
        raise CalibrationError("calibration run timestamp does not match its frozen decision clock")
    if run.get("integrity_sha256") != _integrity_hash(run):
        raise CalibrationError("calibration run integrity SHA-256 mismatch")
    expected_metrics = compute_calibration_metrics(
        examples,
        held_out_included=configuration["include_held_out"],
        held_out_excluded_count=(
            0 if configuration["include_held_out"] else configuration["held_out_example_count"]
        ),
    )
    if run.get("metrics") != expected_metrics:
        raise CalibrationError("calibration run metrics do not match its recorded example traces")


def _diagnose(example: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    if example["assessment_status"] != "judgeable" or example["expected_decision"] is None:
        stages: list[str] = []
        if record["trace"]["search"]["complete"] is not True:
            stages.append("candidate_retrieval_or_search_completeness")
        if any(item["evidence_status"] != "complete" for item in record["trace"]["candidates"]):
            stages.append("evidence_inspection")
        return {
            "status": "not_judged",
            "likely_stages": stages,
            "certainty": "observed_trace_only",
            "reason": "No resolved expected decision is recorded; this example is excluded from accuracy metrics.",
            "suggested_follow_up": "Complete evidence review or resolve the ambiguity before treating this as a model error.",
            "explanation_generation": {
                "status": "unknown",
                "reason": "Schema v1 has no reviewer-coded expected explanation for comparison.",
            },
        }
    conformity = _conformity(record)
    if conformity is DecisionConformity.CORRECT:
        return {
            "status": "correct",
            "likely_stages": [],
            "certainty": "label_comparison",
            "reason": "Observed decision matches the recorded expected decision.",
            "suggested_follow_up": "No change is indicated by this example alone.",
            "explanation_generation": {
                "status": "unknown",
                "reason": "Schema v1 has no reviewer-coded expected explanation for comparison.",
            },
        }

    trace = record["trace"]
    candidates = trace["candidates"]
    top = candidates[0] if candidates else None
    expected = example["expected_decision"]
    observed = record["observed_decision"]
    stages: list[str] = []
    evidence: list[str] = []
    if _task_is_routine_fixture(example["task"]) and trace["search"]["called"]:
        stages.append("task_normalization")
        evidence.append("The local routine classifier did not take the no-catalog fast path.")
    if trace["search"]["complete"] is not True:
        stages.append("candidate_retrieval_or_search_completeness")
        evidence.append("Search completeness was false or unknown in the fixture trace.")
    if example["expected_candidate_order"] is not None and record["observed_candidate_order"] != example["expected_candidate_order"]:
        stages.append("listing_relevance")
        evidence.append("Observed candidate order differs from the reviewed expected order.")
    if expected == Decision.USE.value and observed != Decision.USE.value:
        if top is None or top["capability_supported"] is not True:
            stages.append("operation_evidence_detection")
            evidence.append("No inspected candidate has confirmed operation-and-topic support.")
        if top is None or top["evidence_status"] != "complete":
            stages.append("evidence_inspection")
            evidence.append("The leading candidate's detail inspection is not complete.")
        if top is not None and top["capability_supported"] is True and top["relevance_score"] < relevance_threshold():
            stages.append("decision_threshold")
            evidence.append("Capability is supported but the displayed relevance score is below the current threshold.")
        if top is not None and (
            top["external_audit_status"] != "current" or top["compatibility"] != "compatible"
        ):
            stages.append("safety_or_compatibility_gate")
            evidence.append("Audit or compatibility evidence does not satisfy the existing USE gates.")
    elif expected == Decision.AVOID.value and observed != Decision.AVOID.value:
        if top is None or top["evidence_status"] != "complete":
            stages.append("evidence_inspection")
            evidence.append("The potentially concerning detail is unavailable or incomplete.")
        if top is not None and _has_hard_block(top):
            stages.append("safety_or_compatibility_gate")
            evidence.append("The trace contains an affirmative hard-block signal that was not reflected in AVOID.")
    elif expected == Decision.NO_SKILL_NEEDED.value and observed != Decision.NO_SKILL_NEEDED.value:
        if observed == Decision.USE.value and (top is None or top["capability_supported"] is not True):
            stages.append("operation_evidence_detection")
            evidence.append("A USE result lacks confirmed operation-and-topic evidence in the trace.")
        elif top is not None and top["relevance_score"] >= relevance_threshold():
            stages.extend(["listing_relevance", "decision_threshold"])
            evidence.append("A candidate crossed the relevance threshold despite the expected no-skill label.")
    elif expected == Decision.REVIEW.value and observed != Decision.REVIEW.value:
        if observed == Decision.NO_SKILL_NEEDED.value and trace["search"]["complete"] is not True:
            stages.append("candidate_retrieval_or_search_completeness")
            evidence.append("The pipeline returned NO_SKILL_NEEDED despite incomplete search coverage.")
        if top is None or top["evidence_status"] != "complete":
            stages.append("evidence_inspection")
            evidence.append("Candidate detail evidence is unavailable or incomplete.")
        if top is not None and (
            top["compatibility"] != "compatible" or top["external_audit_status"] != "current"
        ):
            stages.append("safety_or_compatibility_gate")
            evidence.append("The trace contains a compatibility or audit uncertainty that may warrant REVIEW.")
    if not stages:
        stages.append("unknown")
        evidence.append("Available deterministic trace fields do not isolate a responsible stage.")
    return {
        "status": "incorrect",
        "likely_stages": list(dict.fromkeys(stages)),
        "certainty": "trace_based_hypothesis",
        "reason": " ".join(evidence),
        "suggested_follow_up": _follow_up(stages),
        "explanation_generation": {
            "status": "unknown",
            "reason": "Schema v1 has no reviewer-coded expected explanation for comparison.",
        },
    }


def _quality_violations(record: Mapping[str, Any]) -> list[str]:
    violations: set[str] = set()
    trace = record["trace"]
    candidates = trace["candidates"]
    if record["observed_decision"] == Decision.NO_SKILL_NEEDED.value and trace["search"]["complete"] is not True:
        violations.add("no_skill_needed_after_incomplete_search")
    for candidate in candidates:
        if candidate["decision"] != Decision.USE.value:
            continue
        if candidate["capability_supported"] is not True:
            violations.add("candidate_use_without_confirmed_operation_capability")
        if candidate["evidence_status"] != "complete":
            violations.add("candidate_use_without_complete_inspection")
        if candidate["external_audit_status"] != "current":
            violations.add("candidate_use_without_current_audit")
        if candidate["compatibility"] != "compatible":
            violations.add("candidate_use_without_compatible_project_evidence")
        if _has_hard_block(candidate):
            violations.add("candidate_use_with_affirmative_hard_block")
    if record["observed_decision"] == Decision.AVOID.value and not any(
        _has_hard_block(candidate) for candidate in candidates
    ):
        violations.add("avoid_without_affirmative_current_hard_block")
    return sorted(violations)


def _has_hard_block(candidate: Mapping[str, Any]) -> bool:
    if any(item.get("severity") == "critical" for item in candidate.get("local_findings", [])):
        return True
    return candidate.get("external_audit_status") == "current" and any(
        str(item.get("status", "")).strip().casefold() == "fail"
        or str(item.get("risk_level", "")).strip().upper() in {"HIGH", "CRITICAL"}
        for item in candidate.get("audit_findings", [])
    )


def _conformity(record: Mapping[str, Any]) -> DecisionConformity:
    if record.get("assessment_status") != "judgeable" or record.get("expected_decision") is None:
        return DecisionConformity.INDETERMINATE
    if record.get("expected_decision") == record.get("observed_decision"):
        return DecisionConformity.CORRECT
    return DecisionConformity.INCORRECT


def _decision_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    correct = sum(_conformity(item) is DecisionConformity.CORRECT for item in rows)
    predicted = Counter(item["observed_decision"] for item in rows)
    expected = Counter(item["expected_decision"] for item in rows)
    per_decision: dict[str, Any] = {}
    for decision in sorted(DECISION_VALUES):
        tp = sum(
            item["observed_decision"] == decision and item["expected_decision"] == decision
            for item in rows
        )
        fp = predicted[decision] - tp
        fn = expected[decision] - tp
        per_decision[decision] = {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": {"value": _ratio(tp, tp + fp), "numerator": tp, "denominator": tp + fp},
            "recall": {"value": _ratio(tp, tp + fn), "numerator": tp, "denominator": tp + fn},
        }
    false_use = sum(
        item["observed_decision"] == Decision.USE.value
        and item["expected_decision"] != Decision.USE.value
        for item in rows
    )
    false_no_skill = sum(
        item["observed_decision"] == Decision.NO_SKILL_NEEDED.value
        and item["expected_decision"] != Decision.NO_SKILL_NEEDED.value
        for item in rows
    )
    unnecessary_review = sum(
        item["observed_decision"] == Decision.REVIEW.value
        and item["expected_decision"] != Decision.REVIEW.value
        for item in rows
    )
    predicted_use = predicted[Decision.USE.value]
    predicted_no_skill = predicted[Decision.NO_SKILL_NEEDED.value]
    predicted_review = predicted[Decision.REVIEW.value]
    return {
        "judgeable_examples": total,
        "accuracy": {"value": _ratio(correct, total), "numerator": correct, "denominator": total},
        "per_decision": per_decision,
        "false_use_rate": {
            "value": _ratio(false_use, predicted_use),
            "numerator": false_use,
            "denominator": predicted_use,
        },
        "false_use_share_of_judgeable_examples": {
            "value": _ratio(false_use, total),
            "numerator": false_use,
            "denominator": total,
        },
        "false_no_skill_needed_rate": {
            "value": _ratio(false_no_skill, predicted_no_skill),
            "numerator": false_no_skill,
            "denominator": predicted_no_skill,
        },
        "unnecessary_review_rate": {
            "value": _ratio(unnecessary_review, predicted_review),
            "numerator": unnecessary_review,
            "denominator": predicted_review,
        },
    }


def _ranking_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    evaluated = [
        item
        for item in rows
        if isinstance(item.get("expected_candidate_order"), list)
        and item.get("expected_candidate_order")
    ]
    top1 = 0
    reciprocal_ranks: list[float] = []
    pairwise_correct = 0
    pairwise_total = 0
    for item in evaluated:
        expected = item["expected_candidate_order"]
        observed = item.get("observed_candidate_order", [])
        if observed and observed[0] == expected[0]:
            top1 += 1
        positions = {candidate_id: index for index, candidate_id in enumerate(observed)}
        first_position = positions.get(expected[0])
        reciprocal_ranks.append(1 / (first_position + 1) if first_position is not None else 0.0)
        for left in range(len(expected)):
            for right in range(left + 1, len(expected)):
                pairwise_total += 1
                left_position = positions.get(expected[left], len(observed) + 1)
                right_position = positions.get(expected[right], len(observed) + 1)
                pairwise_correct += int(left_position < right_position)
    denominator = len(evaluated)
    return {
        "examples_with_expected_order": denominator,
        "top1_accuracy": {"value": _ratio(top1, denominator), "numerator": top1, "denominator": denominator},
        "mean_reciprocal_rank": {
            "value": sum(reciprocal_ranks) / denominator if denominator else None,
            "numerator": sum(reciprocal_ranks) if denominator else 0,
            "denominator": denominator,
        },
        "pairwise_order_accuracy": {
            "value": _ratio(pairwise_correct, pairwise_total),
            "numerator": pairwise_correct,
            "denominator": pairwise_total,
        },
    }


def _group_metrics(
    rows: Sequence[Mapping[str, Any]], field: str, held_out_included: bool
) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in rows:
        groups[str(item.get(field, "unknown"))].append(item)
    output: dict[str, Any] = {}
    for key in sorted(groups):
        members = groups[key]
        synthetic = [
            item for item in members
            if item.get("assessment_status") == "judgeable"
            and item.get("label_provenance") == "synthetic_fixture"
            and item.get("expected_decision") in DECISION_VALUES
        ]
        human_development = [
            item for item in members
            if item.get("assessment_status") == "judgeable"
            and item.get("label_provenance") == "human_reviewed"
            and item.get("split") == "development"
            and item.get("expected_decision") in DECISION_VALUES
        ]
        human_held_out = [
            item for item in members
            if item.get("assessment_status") == "judgeable"
            and item.get("label_provenance") == "human_reviewed"
            and item.get("split") == "held_out"
            and item.get("expected_decision") in DECISION_VALUES
        ]
        statuses = Counter(str(item.get("assessment_status", "unknown")) for item in members)
        output[key] = {
            "examples": len(members),
            "judgeable": statuses.get("judgeable", 0),
            "incomplete": statuses.get("incomplete", 0),
            "ambiguous": statuses.get("ambiguous", 0),
            "pending": statuses.get("pending", 0),
            "resolved_ambiguous_reviews": sum(item.get("review_label_status") == "ambiguous" for item in members),
            "resolved_unjudgeable_reviews": sum(item.get("review_label_status") == "unjudgeable" for item in members),
            "synthetic_smoke_test": _metric_group(synthetic, included=True),
            "human_reviewed_development": _metric_group(human_development, included=True),
            "human_reviewed_held_out": (
                _metric_group(human_held_out, included=True)
                if held_out_included
                else {"included": False, "example_count": None, "decision": None, "candidate_ranking": None}
            ),
        }
    return output


def _compare_decision_metrics(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    names = (
        "accuracy",
        "false_use_rate",
        "false_no_skill_needed_rate",
        "unnecessary_review_rate",
        "false_use_share_of_judgeable_examples",
    )
    result = {name: _metric_delta(before[name], after[name]) for name in names}
    result["per_decision_precision_recall"] = {
        decision: {
            metric: _metric_delta(
                before["per_decision"][decision][metric],
                after["per_decision"][decision][metric],
            )
            for metric in ("precision", "recall")
        }
        for decision in sorted(DECISION_VALUES)
    }
    return result


def _compare_metric_group(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    included = before.get("included") is True and after.get("included") is True
    if not included:
        return {
            "included": False,
            "baseline_example_count": before.get("example_count"),
            "candidate_example_count": after.get("example_count"),
        }
    old_decision = before["decision"]
    new_decision = after["decision"]
    old_ranking = before["candidate_ranking"]
    new_ranking = after["candidate_ranking"]
    return {
        "included": True,
        "baseline_example_count": before["example_count"],
        "candidate_example_count": after["example_count"],
        "small_sample_warning_baseline": before["small_sample_warning"],
        "small_sample_warning_candidate": after["small_sample_warning"],
        "accuracy": _metric_delta(old_decision["accuracy"], new_decision["accuracy"]),
        "decision": _compare_decision_metrics(old_decision, new_decision),
        "candidate_ranking": {
            metric_name: _metric_delta(old_ranking[metric_name], new_ranking[metric_name])
            for metric_name in ("top1_accuracy", "mean_reciprocal_rank", "pairwise_order_accuracy")
        },
    }


def _comparison_category_deltas(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    old_categories = old["by_challenge_category"]
    new_categories = new["by_challenge_category"]
    output: dict[str, Any] = {}
    for category in sorted(set(old_categories) | set(new_categories)):
        before = old_categories.get(category, {})
        after = new_categories.get(category, {})
        evaluation_groups = {}
        for group_name in (
            "synthetic_smoke_test",
            "human_reviewed_development",
            "human_reviewed_held_out",
        ):
            old_group = before.get(group_name, {})
            new_group = after.get(group_name, {})
            evaluation_groups[group_name] = _compare_metric_group(old_group, new_group)
        output[category] = {
            "evaluation_groups": evaluation_groups,
            "incomplete_baseline": before.get("incomplete", 0),
            "incomplete_candidate": after.get("incomplete", 0),
            "ambiguous_baseline": before.get("ambiguous", 0),
            "ambiguous_candidate": after.get("ambiguous", 0),
            "pending_baseline": before.get("pending", 0),
            "pending_candidate": after.get("pending", 0),
        }
    return output


def _correctness_change(old: Mapping[str, Any], new: Mapping[str, Any]) -> str:
    before = _conformity(old)
    after = _conformity(new)
    if before is DecisionConformity.INDETERMINATE or after is DecisionConformity.INDETERMINATE:
        return "not_assessable"
    if before is after:
        return "correctness_unchanged"
    return "improved" if after is DecisionConformity.CORRECT else "regressed"


def _evaluation_group_for_record(record: Mapping[str, Any]) -> str | None:
    if record.get("label_provenance") == "synthetic_fixture":
        return "synthetic_smoke_test"
    if record.get("label_provenance") == "human_reviewed":
        if record.get("split") == "held_out":
            return "human_reviewed_held_out"
        if record.get("split") == "development":
            return "human_reviewed_development"
    return None


def _follow_up(stages: Sequence[str]) -> str:
    if any(stage in stages for stage in ("task_normalization", "operation_evidence_detection")):
        return "Review action vocabulary and paraphrase coverage; inspect examples before changing the capability gate."
    if "listing_relevance" in stages:
        return "Review retrieval/ranking evidence and compare candidate ordering before changing weights."
    if "evidence_inspection" in stages:
        return "Review excerpt coverage and identity/revision evidence; missing evidence is not a negative finding."
    if "decision_threshold" in stages:
        return "Review the threshold against development examples only; keep held-out examples untouched."
    if "safety_or_compatibility_gate" in stages:
        return "Review the existing safety and compatibility evidence gates; do not weaken them to improve aggregate accuracy."
    if "candidate_retrieval_or_search_completeness" in stages:
        return "Review search coverage and completeness handling; do not interpret an incomplete search as no skill needed."
    return "Inspect the recorded trace and obtain a resolved human label before changing product behavior."


def _task_is_routine_fixture(task: str) -> bool:
    from skillscout.ranking import task_is_routine

    return task_is_routine(task)


def _audit_finding(value: Mapping[str, Any]) -> AuditFinding:
    payload = {
        "provider": value.get("provider"),
        "status": value.get("status"),
        "summary": value.get("summary", ""),
        "auditedAt": value.get("audited_at"),
        "riskLevel": value.get("risk_level"),
        "revision": value.get("revision"),
    }
    try:
        return AuditFinding.model_validate(payload)
    except ValidationError as exc:
        raise CalibrationError("calibration dataset contains an invalid audit finding") from exc


@contextmanager
def _fixed_clock(value: str) -> Iterator[None]:
    reference = _parse_utc(value, "evaluation_clock_utc")

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = reference if tz is not None else reference.replace(tzinfo=None)
            return fixed.astimezone(tz) if tz is not None else fixed

        @classmethod
        def utcnow(cls):
            return reference.replace(tzinfo=None)

    import skillscout.decision as decision_module
    import skillscout.models as model_module

    with patch.object(decision_module, "datetime", FrozenDateTime), patch.object(
        model_module, "datetime", FrozenDateTime
    ):
        yield


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise CalibrationError(f"{field} must be a timezone-aware UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalibrationError(f"{field} must be a timezone-aware UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise CalibrationError(f"{field} must be a timezone-aware UTC timestamp")
    return parsed.astimezone(timezone.utc)


def _require_text(container: Mapping[str, Any], key: str, maximum: int) -> None:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CalibrationError(f"{key} must be non-empty text no longer than {maximum} characters")


def _require_enum(value: Any, allowed: set[str], label: str) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise CalibrationError(f"{label} must be one of the supported values")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CalibrationError("JSON input contains duplicate object keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise CalibrationError(f"JSON constant {value} is not allowed")


def _read_json_file(path: str | Path, maximum: int, label: str) -> Any:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise CalibrationError(f"{label} must be a regular, non-symlink file")
    try:
        if source.stat().st_size > maximum:
            raise CalibrationError(f"{label} exceeds the {maximum // (1024 * 1024)} MiB limit")
        raw = source.read_bytes()
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"{label} could not be parsed as UTF-8 JSON") from exc


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _integrity_hash(value: Mapping[str, Any]) -> str:
    material = {key: item for key, item in value.items() if key != "integrity_sha256"}
    return hashlib.sha256(_canonical_json(material)).hexdigest()


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _metric_delta(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    old_value = old.get("value")
    new_value = new.get("value")
    return {
        "baseline": old_value,
        "candidate": new_value,
        "delta": new_value - old_value if old_value is not None and new_value is not None else None,
        "baseline_denominator": old.get("denominator", 0),
        "candidate_denominator": new.get("denominator", 0),
    }


def _format_rate(label: str, metric: Mapping[str, Any]) -> str:
    return f"- {label}: {_percent(metric.get('value'))} ({metric.get('numerator', 0)}/{metric.get('denominator', 0)})"


def _format_metric_group(group: Mapping[str, Any], label: str) -> list[str]:
    if not group.get("included"):
        return ["Not included."]
    decision = group["decision"]
    ranking = group["candidate_ranking"]
    lines = [
        f"Judgeable examples: {group['example_count']}",
        f"Small-sample warning (<30): {'yes' if group['small_sample_warning'] else 'no'}",
        _format_rate("Decision accuracy", decision["accuracy"]),
        _format_rate("False USE rate", decision["false_use_rate"]),
        _format_rate("False USE share of judgeable examples", decision["false_use_share_of_judgeable_examples"]),
        _format_rate("False NO_SKILL_NEEDED rate", decision["false_no_skill_needed_rate"]),
        _format_rate("Unnecessary REVIEW rate", decision["unnecessary_review_rate"]),
        "Per-decision precision and recall:",
        *[
            f"- {decision_name}: precision {_percent(item['precision']['value'])} "
            f"({item['precision']['numerator']}/{item['precision']['denominator']}), "
            f"recall {_percent(item['recall']['value'])} "
            f"({item['recall']['numerator']}/{item['recall']['denominator']})"
            for decision_name, item in sorted(decision["per_decision"].items())
        ],
        _format_rate("Top-1 candidate ordering", ranking["top1_accuracy"]),
        _format_rate("Mean reciprocal rank", ranking["mean_reciprocal_rank"]),
        _format_rate("Pairwise candidate ordering", ranking["pairwise_order_accuracy"]),
    ]
    if label.startswith("Synthetic"):
        lines.append("This group is a tooling smoke test and is not evidence of recommendation quality.")
    return lines


def _percent(value: Any) -> str:
    return "n/a" if value is None else f"{100 * float(value):.1f}%"


def _format_delta(label: str, metric: Mapping[str, Any]) -> str:
    delta = metric.get("delta")
    visible = "n/a" if delta is None else f"{100 * float(delta):+.1f} points"
    return f"- {label}: {visible} (n={metric.get('baseline_denominator')} → {metric.get('candidate_denominator')})"


def _format_delta_cell(metric: Mapping[str, Any]) -> str:
    value = metric.get("delta")
    return "n/a" if value is None else f"{100 * float(value):+.1f} pp"
