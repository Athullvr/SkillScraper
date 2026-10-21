"""Local restricted storage for evaluator-side run records and review events."""

from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from skillscout.evaluation.models import PairRecord
from skillscout.evaluation.protocol import DATASET_SHA256, EvaluationTask
from skillscout.evaluation.reviews import ReviewEvent
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
    runtime_enforcement: Mapping[str, str] | None = None
    run_start_utc: str | None = None
    run_end_utc: str | None = None


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
    _check_manifest_safe(manifest_dict)
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise RunStoreError(f"refusing to overwrite existing run directory: {root}") from exc
    os.chmod(root, 0o700)
    _write_private(root / "manifest.json", json.dumps(manifest_dict, ensure_ascii=False, indent=2) + "\n")
    pair_lines = "".join(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for item in pair_list)
    _write_private(root / "pairs.jsonl", pair_lines)
    events = list(review_events)
    event_lines = "".join(json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for item in events)
    _write_private(root / "review-events.jsonl", event_lines)
    crosswalk = list(crosswalk_entries)
    crosswalk_lines = "".join(
        json.dumps(item.to_dict() if hasattr(item, "to_dict") else dict(item), ensure_ascii=False, sort_keys=True) + "\n"
        for item in crosswalk
    )
    _write_private(root / "restricted-crosswalk.jsonl", crosswalk_lines)
    return root


def _write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)


def read_run(directory: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = Path(directory)
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        pairs = [json.loads(line) for line in (root / "pairs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunStoreError(f"cannot read restricted run record: {exc}") from exc
    _check_manifest_safe(manifest)
    return manifest, pairs
