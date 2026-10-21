"""Content-addressed, symlink-free task contexts and opaque runtime copies."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator

from skillscout.evaluation.protocol import DATASET_SHA256, EvaluationTask, ELIGIBLE_TASK_COUNT

MANIFEST_NAME = "MANIFEST.sha256"
FIXTURE_LOCK_NAME = "fixtures.lock.json"
DEFAULT_INCOMPLETE_FACTS: dict[str, list[str]] = {
    "routine-unit-test": ["existing test framework was not specified"],
    "routine-readme-command": ["existing test command and framework were not specified"],
    "helpful-postgres-migration": ["target column and quantitative workload were not specified"],
    "helpful-terraform-iam": ["starting IAM policy and cross-account trust relationship were not specified"],
}
PROTECTED_AUDIT_TEXT = (
    "current inspected revision has no verified revision-bound external audit evidence",
    "no verified revision-bound external audit evidence",
)
_PROTECTED_AUDIT_PATTERNS = (
    re.compile(
        r"(?is)\b(?:no|without|missing|lacks?|unverified|not\s+verified|unavailable|absent)\b"
        r".{0,120}\b(?:current\s+)?(?:inspected\s+)?revision\b.{0,120}"
        r"\b(?:external\s+)?audit(?:\s+evidence)?\b"
    ),
    re.compile(
        r"(?is)\b(?:external\s+)?audit(?:\s+evidence)?\b.{0,120}"
        r"\b(?:no|missing|unverified|not\s+verified|unavailable|absent)\b.{0,100}"
        r"\b(?:current\s+)?revision\b"
    ),
    re.compile(
        r"(?is)\bcurrent\s+revision\b.{0,100}"
        r"\b(?:no|not|without|missing|unverified|unavailable)\b.{0,60}"
        r"\b(?:audit|external\s+review)\b"
    ),
)
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|access[_-]?token|password)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bauthorization\s*[:=]\s*basic\s+[A-Za-z0-9+/=]{12,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|sk-[A-Za-z0-9]{20,})\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)


class FixtureError(ValueError):
    """A fixture violates its manifest, path, or input-boundary rules."""


def _relative_path(root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    normalized = unicodedata.normalize("NFC", rel)
    if normalized != rel:
        raise FixtureError(f"fixture path is not Unicode NFC normalized: {rel!r}")
    pure = PurePosixPath(rel)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(ord(char) < 32 or ord(char) == 127 for char in rel)
        or "\n" in rel
        or "\r" in rel
    ):
        raise FixtureError(f"unsafe fixture path: {rel!r}")
    try:
        rel.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise FixtureError(f"fixture path is not valid UTF-8: {rel!r}") from exc
    return rel


def fixture_files(root: str | Path) -> list[Path]:
    """List regular files and reject symlinks, devices, sockets, and hidden omissions."""
    base = Path(root)
    if not base.is_dir() or base.is_symlink():
        raise FixtureError(f"fixture root is not a real directory: {base}")
    files: list[Path] = []
    for directory, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
        current = Path(directory)
        for dirname in list(dirnames):
            candidate = current / dirname
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise FixtureError(f"fixture contains a symlink or special directory: {candidate}")
            _relative_path(base, candidate)
        for filename in filenames:
            candidate = current / filename
            mode = candidate.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise FixtureError(f"fixture contains a symlink or special file: {candidate}")
            _relative_path(base, candidate)
            if candidate.name != MANIFEST_NAME or candidate.parent != base:
                files.append(candidate)
    return sorted(files, key=lambda item: _relative_path(base, item).encode("utf-8"))


def manifest_bytes(root: str | Path) -> bytes:
    """Build the v1 canonical manifest from exact file bytes, excluding its root file."""
    base = Path(root)
    rows = []
    for path in fixture_files(base):
        rel = _relative_path(base, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {rel}\n")
    return "".join(rows).encode("utf-8")


def write_manifest(root: str | Path) -> str:
    base = Path(root)
    content = manifest_bytes(base)
    (base / MANIFEST_NAME).write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def pin_fixture_pack(
    contexts_root: str | Path,
    tasks: tuple[EvaluationTask, ...] | list[EvaluationTask],
    *,
    incomplete_facts: dict[str, list[str]] | None = None,
    git_commit: str | None = None,
    dataset_sha256: str = DATASET_SHA256,
) -> dict[str, object]:
    """Write content manifests and the evaluator-only fixture lock index.

    ``git_commit`` remains null until the fixture content is represented by an
    approved commit. A caller must not point it at an unrelated prior revision.
    """
    if len(tasks) != ELIGIBLE_TASK_COUNT:
        raise FixtureError(f"fixture pinning requires exactly {ELIGIBLE_TASK_COUNT} tasks")
    root = Path(contexts_root)
    incomplete_facts = incomplete_facts or {}
    entries: list[dict[str, object]] = []
    lock_path = root / FIXTURE_LOCK_NAME
    if lock_path.exists() or any((root / task.task_id / MANIFEST_NAME).exists() for task in tasks):
        raise FixtureError("fixture pack is already pinned; refusing to replace existing content hashes")
    expected_ids = [task.task_id for task in tasks]
    actual_ids = sorted(
        path.name for path in root.iterdir()
        if path.is_dir() and not path.is_symlink()
    )
    if sorted(expected_ids) != actual_ids:
        raise FixtureError("context directories must match the 14 eligible task IDs exactly")
    for task in tasks:
        directory = root / task.task_id
        digest = write_manifest(directory)
        blocked = incomplete_facts.get(task.task_id, [])
        entries.append(
            {
                "task_id": task.task_id,
                "eligible_index": task.eligible_index,
                "manifest_sha256": digest,
                "context_status": "incomplete_by_protocol" if blocked else "ready",
                "blocked_facts": list(blocked),
            }
        )
    lock = {
        "schema_version": "skillscout-fixture-lock-v1",
        "dataset_sha256": dataset_sha256,
        "git_commit": git_commit,
        "fixtures": entries,
    }
    lock_path.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    verify_fixture_pack(root, tasks, dataset_sha256=dataset_sha256)
    return {
        "dataset_sha256": dataset_sha256,
        "fixture_count": len(entries),
        "fixtures": entries,
        "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        "git_commit": git_commit,
    }


def verify_manifest(root: str | Path) -> str:
    base = Path(root)
    manifest_path = base / MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise FixtureError(f"missing regular {MANIFEST_NAME} in {base}")
    expected = manifest_bytes(base)
    actual = manifest_path.read_bytes()
    if actual != expected:
        raise FixtureError(f"fixture manifest mismatch in {base}")
    digest = hashlib.sha256(actual).hexdigest()
    _check_protected_text(base)
    return digest


def _check_protected_text(root: Path) -> None:
    for path in fixture_files(root):
        try:
            content = path.read_text(encoding="utf-8").casefold()
        except UnicodeDecodeError:
            continue
        if has_protected_audit_disclosure(content):
            raise FixtureError(f"protected evaluator-only audit text found in fixture: {path.name}")
        if has_credential_like_text(content):
            raise FixtureError(f"credential-like text found in fixture: {path.name}")


def has_protected_audit_disclosure(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(protected in normalized for protected in PROTECTED_AUDIT_TEXT) or any(
        pattern.search(text) for pattern in _PROTECTED_AUDIT_PATTERNS
    )


def has_credential_like_text(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS)


def redact_credential_like_text(text: str) -> str:
    """Remove common credential forms before untrusted evidence is persisted or reviewed."""
    result = text
    for pattern in _CREDENTIAL_PATTERNS:
        result = pattern.sub("[credential redacted]", result)
    return result


def verify_fixture_pack(
    contexts_root: str | Path,
    tasks: tuple[EvaluationTask, ...] | list[EvaluationTask],
    *,
    dataset_sha256: str = DATASET_SHA256,
) -> dict[str, object]:
    """Verify all eligible contexts against the evaluator-only lock index."""
    root = Path(contexts_root)
    lock_path = root / FIXTURE_LOCK_NAME
    if not lock_path.is_file() or lock_path.is_symlink():
        raise FixtureError(f"missing regular fixture lock index: {lock_path}")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureError(f"fixture lock is invalid JSON: {exc}") from exc
    if lock.get("dataset_sha256") != dataset_sha256:
        raise FixtureError("fixture lock is pinned to a different task dataset hash")
    entries = lock.get("fixtures")
    expected_ids = [task.task_id for task in tasks]
    if not isinstance(entries, list) or [entry.get("task_id") for entry in entries] != expected_ids:
        raise FixtureError("fixture lock task IDs/order do not match the eligible dataset")
    if len(entries) != ELIGIBLE_TASK_COUNT:
        raise FixtureError(f"fixture lock must contain exactly {ELIGIBLE_TASK_COUNT} contexts")
    actual_ids = sorted(
        path.name for path in root.iterdir()
        if path.is_dir() and not path.is_symlink()
    )
    if actual_ids != sorted(expected_ids):
        raise FixtureError("context directories do not match the 14 eligible task IDs exactly")
    commit = lock.get("git_commit")
    if commit is not None and not re.fullmatch(r"[0-9a-f]{40,64}", str(commit)):
        raise FixtureError("fixture lock Git commit must be null or a full lowercase object ID")

    verified: list[dict[str, object]] = []
    for task, entry in zip(tasks, entries, strict=True):
        directory = root / task.task_id
        digest = verify_manifest(directory)
        if entry.get("manifest_sha256") != digest:
            raise FixtureError(f"fixture lock hash mismatch for {task.task_id}")
        if entry.get("eligible_index") != task.eligible_index:
            raise FixtureError(f"fixture lock index mismatch for {task.task_id}")
        status = entry.get("context_status")
        if status not in {"ready", "incomplete_by_protocol"}:
            raise FixtureError(f"invalid context status for {task.task_id}")
        blocked_facts = entry.get("blocked_facts", [])
        if not isinstance(blocked_facts, list) or not all(isinstance(item, str) for item in blocked_facts):
            raise FixtureError(f"invalid blocked_facts for {task.task_id}")
        if (status == "ready") != (len(blocked_facts) == 0):
            raise FixtureError(f"context status and blocked_facts disagree for {task.task_id}")
        verified.append(
            {
                "task_id": task.task_id,
                "eligible_index": task.eligible_index,
                "manifest_sha256": digest,
                "context_status": status,
                "blocked_facts": blocked_facts,
            }
        )
    return {
        "dataset_sha256": dataset_sha256,
        "fixture_count": len(verified),
        "fixtures": verified,
        "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        "git_commit": lock.get("git_commit"),
    }


@contextmanager
def materialize_opaque_contexts(
    contexts_root: str | Path,
    tasks: tuple[EvaluationTask, ...] | list[EvaluationTask],
    *,
    temporary_root: str | Path | None = None,
) -> Iterator[dict[str, Path]]:
    """Copy verified fixture files to random paths that do not expose task IDs."""
    root = Path(contexts_root)
    temp = tempfile.TemporaryDirectory(prefix="skillscout-context-", dir=temporary_root)
    try:
        base = Path(temp.name)
        paths: dict[str, Path] = {}
        for task in tasks:
            source = root / task.task_id
            verify_manifest(source)
            target = Path(tempfile.mkdtemp(prefix="context-", dir=base))
            for file_path in fixture_files(source):
                relative = file_path.relative_to(source)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file_path, destination)
            paths[task.task_id] = target
        yield paths
    finally:
        temp.cleanup()
