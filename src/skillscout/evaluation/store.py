"""Local restricted storage for evaluator-side run records and review events."""

from __future__ import annotations

import json
import os
import platform
import stat
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from skillscout.evaluation.fixtures import has_credential_like_text
from skillscout.evaluation.models import PairRecord
from skillscout.evaluation.protocol import DATASET_SHA256, EvaluationTask, load_dataset
from skillscout.evaluation.reviews import ReviewError, ReviewEvent, ReviewLedger
from skillscout.evaluation.validation import validate_records


class RunStoreError(ValueError):
    """A run cannot be written or read without risking accidental overwrite."""


@dataclass(frozen=True, slots=True)
class RunManifest:
    run_id: str
    dataset_sha256: str = DATASET_SHA256
    schema_version: str = "skillscout-evaluation-v1"
    evaluation_version: str = "v1"
    source_revision: str = "not_verified"
    python_version: str = platform.python_version()
    lockfile_name: str = "not_available"
    lockfile_sha256: str = "not_available"
    dependency_versions: Mapping[str, str] | None = None
    catalog_mode: str = "offline"
    credential_configured: bool = False
    review_order_seed: int | None = None
    decision_gates_approved_by_task: Mapping[str, bool] | None = None
    decision_gate_approval_reference: str | None = None
    runtime_enforcement: Mapping[str, str] | None = None
    run_start_utc: str | None = None
    run_end_utc: str | None = None


@dataclass(frozen=True, slots=True)
class StoredRun:
    manifest: dict[str, Any]
    pairs: list[dict[str, Any]]
    candidate_crosswalk: list[dict[str, Any]]
    decision_crosswalk: list[dict[str, Any]]
    review_events: list[dict[str, Any]]


_FORBIDDEN_MANIFEST_KEY_FRAGMENTS = ("token", "secret", "authorization", "credential_value", "api_key")
def _check_manifest_safe(manifest: Mapping[str, Any]) -> None:
    def visit(value: Any, path: str = "") -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                name = str(key).casefold()
                if any(fragment in name for fragment in _FORBIDDEN_MANIFEST_KEY_FRAGMENTS):
                    raise RunStoreError(f"manifest field is prohibited because it may contain a secret: {path}{key}")
                if name == "credential_configured" and not isinstance(nested, bool):
                    raise RunStoreError("credential_configured must be a boolean only")
                visit(nested, f"{path}{key}.")
        elif isinstance(value, list):
            for nested in value:
                visit(nested, path)
        elif isinstance(value, str) and has_credential_like_text(value):
            raise RunStoreError("manifest appears to contain a credential or token value")
        elif isinstance(value, (bytes, bytearray)):
            raise RunStoreError("binary payloads are not permitted in the run manifest")
    visit(manifest)


def write_run(
    directory: str | Path,
    manifest: RunManifest | Mapping[str, Any],
    pairs: Iterable[PairRecord],
    tasks: Iterable[EvaluationTask],
    *,
    review_events: Iterable[ReviewEvent] = (),
    crosswalk_entries: Iterable[Any] = (),
    decision_crosswalk_entries: Iterable[Any] = (),
) -> Path:
    """Create a new private run directory after validating all 42 pair records."""
    root = Path(directory)
    pair_list = list(pairs)
    task_list = list(tasks)
    errors = validate_records(pair_list, task_list)
    if errors:
        raise RunStoreError("run validation failed: " + "; ".join(errors[:8]))
    manifest_dict = asdict(manifest) if isinstance(manifest, RunManifest) else dict(manifest)
    if manifest_dict.get("dataset_sha256") != DATASET_SHA256:
        raise RunStoreError("run manifest dataset hash differs from approved dataset")
    if manifest_dict.get("catalog_mode", "offline") not in {"offline", "mocked"}:
        raise RunStoreError("this implementation milestone refuses to persist live collection records")
    _validate_decision_gate_approvals(manifest_dict, {task.task_id for task in task_list})
    _check_manifest_safe(manifest_dict)
    crosswalk = list(crosswalk_entries)
    decision_crosswalk = list(decision_crosswalk_entries)
    expected_crosswalk = Counter(
        (
            slot.opaque_candidate_key,
            pair.pair_id,
            pair.task_id,
            pair.repetition,
            arm_name.value,
            slot.rank,
            slot.stable_id,
            slot.arm_content_hash,
            slot.arm_revision,
        )
        for pair in pair_list
        for arm_name, arm_record in pair.arms.items()
        for slot in arm_record.slots
        if slot.status.value == "occupied"
    )
    observed_crosswalk = Counter(
        (
            _crosswalk_value(item, "opaque_candidate_key"),
            _crosswalk_value(item, "pair_id"),
            _crosswalk_value(item, "task_id"),
            _crosswalk_value(item, "repetition"),
            _crosswalk_value(item, "arm"),
            _crosswalk_value(item, "rank"),
            _crosswalk_value(item, "stable_id"),
            _crosswalk_value(item, "content_hash"),
            _crosswalk_value(item, "revision"),
        )
        for item in crosswalk
    )
    if expected_crosswalk != observed_crosswalk:
        raise RunStoreError("restricted crosswalk does not exactly link every occupied candidate occurrence")
    pair_by_id = {pair.pair_id: pair for pair in pair_list}
    seen_decision_pairs: set[str] = set()
    decision_keys: set[str] = set()
    for entry in decision_crosswalk:
        pair_id = str(_crosswalk_value(entry, "pair_id"))
        pair = pair_by_id.get(pair_id)
        key = str(_crosswalk_value(entry, "opaque_decision_packet_key") or "")
        arm = _crosswalk_value(entry, "arm")
        if pair is None or pair.task_id != _crosswalk_value(entry, "task_id") or pair.repetition != _crosswalk_value(entry, "repetition"):
            raise RunStoreError("decision crosswalk entry does not match a planned task/pair")
        if arm != "skillscout" or not key or pair_id in seen_decision_pairs or key in decision_keys:
            raise RunStoreError(
                "decision crosswalk keys must be unique and map to SkillScraper only"
            )
        scout = next(item for name, item in pair.arms.items() if name.value == "skillscout")
        if not scout.primary_response_valid or scout.decision is None:
            raise RunStoreError(
                "decision packets may be linked only to valid non-null SkillScraper decisions"
            )
        seen_decision_pairs.add(pair_id)
        decision_keys.add(key)
    candidate_keys = {str(item[0]) for item in observed_crosswalk}
    events = list(review_events)
    try:
        ReviewLedger.from_records(events)
    except ReviewError as exc:
        raise RunStoreError(f"review event history is invalid: {exc}") from exc
    for event in events:
        kind = getattr(event.kind, "value", event.kind)
        allowed = candidate_keys if kind == "candidate_relevance" else decision_keys if kind == "decision_conformity" else set()
        if event.packet_key not in allowed:
            raise RunStoreError("review event packet key is absent from its restricted crosswalk")
    manifest_text = json.dumps(manifest_dict, ensure_ascii=False, indent=2) + "\n"
    pair_lines = "".join(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for item in pair_list)
    event_lines = "".join(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for item in events)
    crosswalk_lines = "".join(
        json.dumps(item.to_dict() if hasattr(item, "to_dict") else dict(item), ensure_ascii=False, sort_keys=True) + "\n"
        for item in crosswalk
    )
    decision_crosswalk_lines = "".join(
        json.dumps(item.to_dict() if hasattr(item, "to_dict") else dict(item), ensure_ascii=False, sort_keys=True) + "\n"
        for item in decision_crosswalk
    )
    if any(
        has_credential_like_text(serialized)
        for serialized in (manifest_text, pair_lines, event_lines, crosswalk_lines, decision_crosswalk_lines)
    ):
        raise RunStoreError("run records contain credential-like text and were not persisted")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise RunStoreError(f"refusing to overwrite existing run directory: {root}") from exc
    os.chmod(root, 0o700)
    _write_private(root / "manifest.json", manifest_text)
    _write_private(root / "pairs.jsonl", pair_lines)
    _write_private(root / "review-events.jsonl", event_lines)
    _write_private(root / "restricted-crosswalk.jsonl", crosswalk_lines)
    _write_private(root / "restricted-decision-crosswalk.jsonl", decision_crosswalk_lines)
    return root


def _write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)


def _crosswalk_value(item: Any, key: str) -> Any:
    value = item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)
    return getattr(value, "value", value)


def _validate_decision_gate_approvals(manifest: Mapping[str, Any], allowed_task_ids: set[str]) -> None:
    approvals = manifest.get("decision_gates_approved_by_task") or {}
    if not isinstance(approvals, Mapping) or any(
        not isinstance(task_id, str) or not isinstance(approved, bool)
        for task_id, approved in approvals.items()
    ):
        raise RunStoreError("decision gate approvals must be an explicit task-to-boolean mapping")
    if not set(approvals).issubset(allowed_task_ids):
        raise RunStoreError("decision gate approvals refer to a task outside the eligible cohort")
    if any(approvals.values()) and not str(manifest.get("decision_gate_approval_reference") or "").strip():
        raise RunStoreError("approved decision gates require a human approval reference")


def read_run_bundle(directory: str | Path) -> StoredRun:
    root = Path(directory)
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise RunStoreError(f"cannot read restricted run directory: {exc}") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink() or stat.S_IMODE(root_stat.st_mode) & 0o077:
        raise RunStoreError("restricted run directory must be a private real directory")
    required_files = (
        "manifest.json",
        "pairs.jsonl",
        "restricted-crosswalk.jsonl",
        "restricted-decision-crosswalk.jsonl",
        "review-events.jsonl",
    )
    raw_files: dict[str, bytes] = {}
    try:
        for name in required_files:
            path = root / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink() or stat.S_IMODE(info.st_mode) & 0o077:
                raise RunStoreError(f"restricted run file must be regular and private: {name}")
            raw_files[name] = path.read_bytes()
        decoded = {name: data.decode("utf-8") for name, data in raw_files.items()}
        if any(has_credential_like_text(value) for value in decoded.values()):
            raise RunStoreError("restricted run data contains credential-like text")
        manifest = json.loads(decoded["manifest.json"])
        pairs = [json.loads(line) for line in decoded["pairs.jsonl"].splitlines() if line.strip()]
        candidate_crosswalk = [json.loads(line) for line in decoded["restricted-crosswalk.jsonl"].splitlines() if line.strip()]
        decision_crosswalk = [json.loads(line) for line in decoded["restricted-decision-crosswalk.jsonl"].splitlines() if line.strip()]
        review_events = [json.loads(line) for line in decoded["review-events.jsonl"].splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunStoreError(f"cannot read restricted run record: {exc}") from exc
    if not isinstance(manifest, dict) or any(
        not isinstance(collection, list) or any(not isinstance(item, dict) for item in collection)
        for collection in (pairs, candidate_crosswalk, decision_crosswalk, review_events)
    ):
        raise RunStoreError("restricted run files must contain JSON objects in their declared collections")
    _check_manifest_safe(manifest)
    if manifest.get("dataset_sha256") != DATASET_SHA256:
        raise RunStoreError("stored run manifest dataset hash differs from the approved dataset")
    try:
        eligible_task_ids = {task.task_id for task in load_dataset().eligible_tasks}
    except ValueError as exc:
        raise RunStoreError("cannot verify the current protected task dataset") from exc
    _validate_decision_gate_approvals(manifest, eligible_task_ids)
    _validate_stored_crosswalks(pairs, candidate_crosswalk, decision_crosswalk, review_events)
    try:
        ReviewLedger.from_records(review_events)
    except ReviewError as exc:
        raise RunStoreError(f"stored reviewer history is invalid: {exc}") from exc
    return StoredRun(manifest, pairs, candidate_crosswalk, decision_crosswalk, review_events)


def read_run(directory: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compatibility view for consumers that need only the run header and pairs."""
    run = read_run_bundle(directory)
    return run.manifest, run.pairs


def _validate_stored_crosswalks(
    pairs: list[Mapping[str, Any]],
    candidate_entries: list[Mapping[str, Any]],
    decision_entries: list[Mapping[str, Any]],
    events: list[Mapping[str, Any]],
) -> None:
    expected = Counter(
        (
            slot.get("opaque_candidate_key"),
            pair.get("pair_id"),
            pair.get("task_id"),
            pair.get("repetition"),
            str(arm_name),
            slot.get("rank"),
            slot.get("stable_id"),
            slot.get("arm_content_hash"),
            slot.get("arm_revision"),
        )
        for pair in pairs
        for arm_name, arm in pair.get("arms", {}).items()
        for slot in arm.get("slots", ())
        if slot.get("status") == "occupied"
    )
    observed = Counter(
        (
            entry.get("opaque_candidate_key"),
            entry.get("pair_id"),
            entry.get("task_id"),
            entry.get("repetition"),
            str(entry.get("arm")),
            entry.get("rank"),
            entry.get("stable_id"),
            entry.get("content_hash"),
            entry.get("revision"),
        )
        for entry in candidate_entries
    )
    if expected != observed:
        raise RunStoreError("stored restricted candidate crosswalk does not match pair slots")
    pair_by_id = {pair.get("pair_id"): pair for pair in pairs}
    decision_keys: set[str] = set()
    decision_pairs: set[str] = set()
    for entry in decision_entries:
        pair = pair_by_id.get(entry.get("pair_id"))
        key = entry.get("opaque_decision_packet_key")
        if (
            pair is None
            or pair.get("task_id") != entry.get("task_id")
            or pair.get("repetition") != entry.get("repetition")
            or entry.get("arm") != "skillscout"
            or not key
            or key in decision_keys
            or entry.get("pair_id") in decision_pairs
        ):
            raise RunStoreError("stored decision crosswalk is malformed or duplicated")
        scout = pair.get("arms", {}).get("skillscout", {})
        if not scout.get("primary_response_valid") or not scout.get("decision"):
            raise RunStoreError("stored decision crosswalk points to no valid non-null decision")
        decision_keys.add(key)
        decision_pairs.add(entry["pair_id"])
    candidate_keys = {entry.get("opaque_candidate_key") for entry in candidate_entries}
    for event in events:
        kind = event.get("kind")
        allowed = candidate_keys if kind == "candidate_relevance" else decision_keys if kind == "decision_conformity" else set()
        if event.get("packet_key") not in allowed:
            raise RunStoreError("stored reviewer event has no matching restricted crosswalk key")
