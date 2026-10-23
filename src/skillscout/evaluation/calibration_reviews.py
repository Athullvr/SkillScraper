"""Append-only human labels for the offline recommendation calibration dataset."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from skillscout.evaluation.fixtures import has_credential_like_text
from skillscout.models import Decision

REVIEW_EVENT_SCHEMA = "skillscout-calibration-review-event-v1"
MAX_REVIEW_LEDGER_BYTES = 8 * 1024 * 1024
MAX_REVIEW_EVENTS = 10_000
MAX_REVIEW_EVENT_BYTES = 32 * 1024
REVIEWER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
REVIEW_STATUSES = {"judgeable", "ambiguous", "unjudgeable"}
EVENT_TYPES = {"review", "revision", "adjudication"}


class CalibrationReviewError(ValueError):
    """A review label or append-only calibration ledger is invalid."""


def read_review_ledger(
    path: str | Path | None,
    *,
    dataset: Mapping[str, Any],
    dataset_sha256: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Read and validate review events, preserving the exact source ledger digest."""
    if path is None:
        return [], None
    source = Path(path)
    try:
        path_info = os.lstat(source)
    except FileNotFoundError:
        return [], None
    except OSError as exc:
        raise CalibrationReviewError("review ledger could not be inspected safely") from exc
    if stat.S_ISLNK(path_info.st_mode) or not stat.S_ISREG(path_info.st_mode):
        raise CalibrationReviewError("review ledger must be a regular, non-symlink file")
    descriptor: int | None = None
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return [], None
    except OSError as exc:
        raise CalibrationReviewError("review ledger could not be opened safely") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CalibrationReviewError("review ledger must be a regular, non-symlink file")
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_REVIEW_LEDGER_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, MAX_REVIEW_LEDGER_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_REVIEW_LEDGER_BYTES:
            raise CalibrationReviewError("review ledger exceeds the 8 MiB limit")
        raw = b"".join(chunks)
    except OSError as exc:
        raise CalibrationReviewError("review ledger could not be read") from exc
    finally:
        os.close(descriptor)
    if not raw:
        return [], hashlib.sha256(raw).hexdigest()
    if not raw.endswith(b"\n"):
        raise CalibrationReviewError("review ledger must end with a newline; incomplete event detected")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line:
            raise CalibrationReviewError(f"review ledger line {line_number} is empty")
        if len(line) > MAX_REVIEW_EVENT_BYTES:
            raise CalibrationReviewError(f"review ledger line {line_number} exceeds the event size limit")
        try:
            event = json.loads(
                line.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CalibrationReviewError(f"review ledger line {line_number} is malformed JSON") from exc
        if not isinstance(event, dict):
            raise CalibrationReviewError(f"review ledger line {line_number} must be an object")
        events.append(event)
        if len(events) > MAX_REVIEW_EVENTS:
            raise CalibrationReviewError("review ledger exceeds the 10,000 event limit")
    validate_review_events(events, dataset=dataset, dataset_sha256=dataset_sha256)
    return events, hashlib.sha256(raw).hexdigest()


def validate_review_events(
    events: Sequence[Mapping[str, Any]],
    *,
    dataset: Mapping[str, Any],
    dataset_sha256: str,
) -> None:
    """Validate event fields, references, revisions, disagreements, and adjudication order."""
    if not isinstance(dataset_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", dataset_sha256):
        raise CalibrationReviewError("review ledger requires a valid dataset SHA-256")
    examples = {item["id"]: item for item in dataset["examples"]}
    seen_event_ids: set[str] = set()
    active: dict[str, dict[str, Mapping[str, Any]]] = {}
    adjudications: dict[str, Mapping[str, Any]] = {}

    for index, event in enumerate(events):
        _validate_event_fields(event, index, examples, dataset, dataset_sha256)
        event_id = event["event_id"]
        example_id = event["example_id"]
        if event_id in seen_event_ids:
            raise CalibrationReviewError(f"duplicate review event ID: {event_id}")
        seen_event_ids.add(event_id)
        current = active.setdefault(example_id, {})
        event_type = event["event_type"]
        if event_type == "review":
            if example_id in adjudications:
                raise CalibrationReviewError("independent reviews cannot be appended after adjudication")
            if event["reviewer_id"] in current:
                raise CalibrationReviewError("reviewer already has a current label; use an explicit revision")
            if len(current) >= 2:
                raise CalibrationReviewError("two independent reviews are already locked for this example")
            current[event["reviewer_id"]] = event
        elif event_type == "revision":
            if example_id in adjudications:
                raise CalibrationReviewError("reviews cannot be revised after adjudication")
            previous = current.get(event["reviewer_id"])
            if previous is None or previous["event_id"] != event["supersedes_event_id"]:
                raise CalibrationReviewError("revision must reference that reviewer's current event")
            current[event["reviewer_id"]] = event
        else:
            if example_id in adjudications:
                raise CalibrationReviewError("only one adjudication event is allowed per example")
            if len(current) < 2 or len({_label_signature(item) for item in current.values()}) < 2:
                raise CalibrationReviewError("adjudication requires conflicting independent reviews")
            current_ids = {item["event_id"] for item in current.values()}
            if set(event["adjudicates_event_ids"]) != current_ids:
                raise CalibrationReviewError("adjudication must cite every current conflicting review event")
            if event["reviewer_id"] in current:
                raise CalibrationReviewError("adjudicator must be independent of the original reviewers")
            adjudications[example_id] = event


def review_state(events: Sequence[Mapping[str, Any]], example_id: str) -> dict[str, Any]:
    """Return the derived review and adjudication state without mutating the ledger."""
    current: dict[str, Mapping[str, Any]] = {}
    adjudication: Mapping[str, Any] | None = None
    for event in events:
        if event["example_id"] != example_id:
            continue
        if event["event_type"] in {"review", "revision"}:
            current[event["reviewer_id"]] = event
        else:
            adjudication = event

    active_events = list(current.values())
    reviewer_ids = sorted(current)
    if adjudication is not None:
        status = "adjudicated"
        adjudication_status = "resolved"
        resolved_event = adjudication
    elif not active_events:
        status = "unreviewed"
        adjudication_status = "not_started"
        resolved_event = None
    elif len(active_events) == 1:
        status = "awaiting_independent_review"
        adjudication_status = "awaiting_second_review"
        resolved_event = None
    elif len({_label_signature(item) for item in active_events}) == 1:
        status = "consensus"
        adjudication_status = "not_needed"
        resolved_event = active_events[0]
    else:
        status = "conflict"
        adjudication_status = "required"
        resolved_event = None

    return {
        "status": status,
        "adjudication_status": adjudication_status,
        "reviewer_ids": reviewer_ids,
        "review_event_ids": sorted(item["event_id"] for item in active_events),
        "adjudicator_id": adjudication["reviewer_id"] if adjudication else None,
        "resolved_event": dict(resolved_event) if resolved_event else None,
        "review_count": len(active_events),
    }


def append_review_event(
    path: str | Path,
    *,
    dataset: Mapping[str, Any],
    dataset_sha256: str,
    events: Sequence[Mapping[str, Any]],
    example_id: str,
    reviewer_id: str,
    status: str,
    decision: str | None,
    candidate_order: Sequence[str] | None,
    rationale: str | None,
    reason: str | None,
    evidence_basis: Sequence[str],
    adjudication: bool = False,
    revise: bool = False,
    adjudicates_event_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Validate and append one human event; previous labels are never overwritten."""
    example = next((item for item in dataset["examples"] if item["id"] == example_id), None)
    if example is None:
        raise CalibrationReviewError(f"unknown calibration example ID: {example_id}")
    _ensure_reviewable_example(example, dataset)
    _validate_label(example, dataset, status, decision, candidate_order, rationale, reason, evidence_basis)

    state = review_state(events, example_id)
    reviewer_id = _validate_reviewer_id(reviewer_id)
    if adjudication:
        if revise:
            raise CalibrationReviewError("adjudication cannot be combined with --revise")
        event_type = "adjudication"
        supersedes = None
        supplied_refs = list(adjudicates_event_ids)
        if not supplied_refs:
            raise CalibrationReviewError("adjudication requires --adjudicates for the current review event IDs")
        if set(supplied_refs) != set(state["review_event_ids"]):
            raise CalibrationReviewError("--adjudicates must list all current review event IDs")
    else:
        if adjudicates_event_ids:
            raise CalibrationReviewError("--adjudicates is only valid for adjudication")
        existing = next(
            (item for item in events if item["example_id"] == example_id and item["reviewer_id"] == reviewer_id and item["event_type"] in {"review", "revision"}),
            None,
        )
        # Later events are the current revision for a reviewer.
        existing = next(
            (item for item in reversed(events) if item["example_id"] == example_id and item["reviewer_id"] == reviewer_id and item["event_type"] in {"review", "revision"}),
            existing,
        )
        if existing is not None and not revise:
            raise CalibrationReviewError("reviewer already submitted a label; use --revise to preserve a new revision")
        if revise and existing is None:
            raise CalibrationReviewError("--revise requires an earlier label from this reviewer")
        if revise and state["status"] == "adjudicated":
            raise CalibrationReviewError("labels cannot be revised after adjudication")
        event_type = "revision" if revise else "review"
        supersedes = existing["event_id"] if existing is not None else None
        supplied_refs = []

    event = {
        "schema_version": REVIEW_EVENT_SCHEMA,
        "dataset_sha256": dataset_sha256,
        "event_id": str(uuid.uuid4()),
        "example_id": example_id,
        "event_type": event_type,
        "reviewer_id": reviewer_id,
        "status": status,
        "decision": decision,
        "candidate_order": list(candidate_order) if candidate_order is not None else None,
        "rationale": rationale,
        "reason": reason,
        "evidence_basis": list(evidence_basis),
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "supersedes_event_id": supersedes,
        "adjudicates_event_ids": sorted(supplied_refs),
    }
    validate_review_events([*events, event], dataset=dataset, dataset_sha256=dataset_sha256)
    _append_private_jsonl(path, event)
    return event


def _validate_event_fields(
    event: Mapping[str, Any],
    index: int,
    examples: Mapping[str, Mapping[str, Any]],
    dataset: Mapping[str, Any],
    dataset_sha256: str,
) -> None:
    required = {
        "schema_version", "dataset_sha256", "event_id", "example_id", "event_type",
        "reviewer_id", "status", "decision", "candidate_order", "rationale", "reason",
        "evidence_basis", "timestamp_utc", "supersedes_event_id", "adjudicates_event_ids",
    }
    if set(event) != required or event.get("schema_version") != REVIEW_EVENT_SCHEMA:
        raise CalibrationReviewError(f"review event {index} has an unsupported schema")
    if event.get("dataset_sha256") != dataset_sha256:
        raise CalibrationReviewError("review ledger belongs to a different dataset revision")
    example_id = event.get("example_id")
    if not isinstance(example_id, str) or example_id not in examples:
        raise CalibrationReviewError(f"review event {index} refers to an unknown example")
    example = examples[example_id]
    _ensure_reviewable_example(example, dataset)
    event_id = event.get("event_id")
    try:
        uuid.UUID(event_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise CalibrationReviewError(f"review event {index} has an invalid event ID") from exc
    _validate_reviewer_id(event.get("reviewer_id"))
    event_type = event.get("event_type")
    if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
        raise CalibrationReviewError(f"review event {index} has an invalid event type")
    _validate_label(
        example,
        dataset,
        event.get("status"),
        event.get("decision"),
        event.get("candidate_order"),
        event.get("rationale"),
        event.get("reason"),
        event.get("evidence_basis"),
    )
    timestamp = event.get("timestamp_utc")
    if not isinstance(timestamp, str):
        raise CalibrationReviewError(f"review event {index} timestamp is missing")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalibrationReviewError(f"review event {index} timestamp is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise CalibrationReviewError(f"review event {index} timestamp must be UTC")
    for field in ("supersedes_event_id",):
        value = event.get(field)
        if value is not None and not isinstance(value, str):
            raise CalibrationReviewError(f"review event {index} has malformed {field}")
    refs = event.get("adjudicates_event_ids")
    if not isinstance(refs, list) or any(not isinstance(value, str) for value in refs):
        raise CalibrationReviewError(f"review event {index} has malformed adjudication references")
    if len(refs) != len(set(refs)):
        raise CalibrationReviewError(f"review event {index} repeats an adjudication reference")
    if event["event_type"] == "adjudication":
        if not refs or event.get("supersedes_event_id") is not None:
            raise CalibrationReviewError("adjudication must cite prior reviews and cannot replace one")
    elif refs or (event["event_type"] == "review" and event.get("supersedes_event_id") is not None):
        raise CalibrationReviewError(f"review event {index} has incompatible history references")
    elif event["event_type"] == "revision" and event.get("supersedes_event_id") is None:
        raise CalibrationReviewError("revision event must identify the event it supersedes")


def _validate_label(
    example: Mapping[str, Any],
    dataset: Mapping[str, Any],
    status: Any,
    decision: Any,
    candidate_order: Any,
    rationale: Any,
    reason: Any,
    evidence_basis: Any,
) -> None:
    if not isinstance(status, str) or status not in REVIEW_STATUSES:
        raise CalibrationReviewError("status must be judgeable, ambiguous, or unjudgeable")
    if status == "judgeable":
        if not isinstance(decision, str) or decision not in {item.value for item in Decision}:
            raise CalibrationReviewError("a judgeable review requires one supported expected decision")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 1000:
            raise CalibrationReviewError("a judgeable review requires a rationale up to 1,000 characters")
        if not isinstance(evidence_basis, list) or not evidence_basis or len(evidence_basis) > 20:
            raise CalibrationReviewError("a judgeable review requires 1 to 20 evidence_basis entries")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 500 for value in evidence_basis):
            raise CalibrationReviewError("evidence_basis entries must be non-empty text up to 500 characters")
        if reason is not None:
            raise CalibrationReviewError("reason is only used for ambiguous or unjudgeable reviews")
    else:
        if decision is not None or candidate_order is not None:
            raise CalibrationReviewError("ambiguous or unjudgeable reviews cannot carry a resolved label")
        if rationale is not None and (not isinstance(rationale, str) or len(rationale) > 1000):
            raise CalibrationReviewError("rationale must be null or text up to 1,000 characters")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise CalibrationReviewError("ambiguous or unjudgeable reviews require a reason")
        if not isinstance(evidence_basis, list) or len(evidence_basis) > 20 or any(
            not isinstance(value, str) or len(value) > 500 for value in evidence_basis
        ):
            raise CalibrationReviewError("evidence_basis must be a bounded list of text")

    if candidate_order is not None:
        if (
            not isinstance(candidate_order, list)
            or not candidate_order
            or len(candidate_order) > dataset["candidate_limit"]
            or any(not isinstance(value, str) for value in candidate_order)
            or len(set(candidate_order)) != len(candidate_order)
        ):
            raise CalibrationReviewError("candidate order must contain unique candidate IDs within the configured limit")
        available = {item["skill"]["id"] for item in example["search"]["candidates"]}
        if not set(candidate_order) <= available:
            raise CalibrationReviewError("candidate order references a skill absent from this example")
    if decision == Decision.NO_SKILL_NEEDED.value:
        routine = _task_is_routine(example["task"])
        search = example["search"]
        if not routine and (search["complete"] is not True or search["coverage_status"] != "adequate"):
            raise CalibrationReviewError("NO_SKILL_NEEDED requires the local fast path or a complete adequate search")
        if not routine and any(item["evidence"]["status"] != "complete" for item in search["candidates"]):
            raise CalibrationReviewError("NO_SKILL_NEEDED requires complete evidence for every returned candidate")
    if decision == Decision.USE.value:
        if not any(
            _candidate_use_safety_evidence(item, dataset, example["project_facts"])
            for item in example["search"]["candidates"]
        ):
            raise CalibrationReviewError(
                "USE requires complete inspected evidence, a current revision-bound passing audit, and compatible project evidence"
            )
    if decision == Decision.AVOID.value and not any(
        _candidate_confirmed_hard_block(item, dataset)
        for item in example["search"]["candidates"]
    ):
        raise CalibrationReviewError(
            "AVOID requires a confirmed critical local finding or current high-risk/failing audit bound to inspected content"
        )

    all_text = json.dumps(
        {
            "rationale": rationale,
            "reason": reason,
            "evidence_basis": evidence_basis,
        },
        ensure_ascii=False,
    )
    if has_credential_like_text(all_text):
        raise CalibrationReviewError("review text contains credential-like material")


def _candidate_use_safety_evidence(
    candidate: Mapping[str, Any], dataset: Mapping[str, Any], project_facts: Sequence[str]
) -> bool:
    evidence = candidate["evidence"]
    audit = candidate["audit"]
    if evidence["status"] != "complete" or not evidence.get("content_hash") or audit["status"] != "available":
        return False
    clock = datetime.fromisoformat(dataset["evaluation_clock_utc"].replace("Z", "+00:00"))
    findings = audit["findings"]
    if not findings:
        return False
    for finding in audit["findings"]:
        if finding["status"].strip().casefold() != "pass" or finding.get("revision") != evidence["content_hash"]:
            return False
        if (finding.get("risk_level") or "").strip().upper() in {"MEDIUM", "HIGH", "CRITICAL"}:
            return False
        timestamp_value = finding.get("audited_at")
        if not isinstance(timestamp_value, str):
            return False
        try:
            timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
        except ValueError:
            return False
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return False
        age = clock - timestamp.astimezone(timezone.utc)
        if 0 <= age.total_seconds() <= 90 * 24 * 60 * 60:
            continue
        return False

    from skillscout.models import CatalogSkill, ProjectSnapshot, SkillDetail, SkillFile
    from skillscout.ranking import evaluate_compatibility
    from skillscout.scanner import scan_skill

    skill = CatalogSkill.model_validate(candidate["skill"])
    detail = SkillDetail(
        id=skill.id,
        source=skill.source,
        slug=skill.slug,
        hash=evidence.get("content_hash"),
        files=[SkillFile(path=item["path"], contents=item["text"]) for item in evidence["files"]],
    )
    project = ProjectSnapshot(path="calibration-review", facts=list(project_facts))
    local_findings, _, scan_complete = scan_skill(detail)
    if not scan_complete or any(
        finding.severity.value in {"medium", "high", "critical"} for finding in local_findings
    ):
        return False
    return evaluate_compatibility(skill, detail, project).status == "compatible"


def _candidate_confirmed_hard_block(
    candidate: Mapping[str, Any], dataset: Mapping[str, Any]
) -> bool:
    evidence = candidate["evidence"]
    if evidence["status"] != "complete" or not evidence.get("content_hash"):
        return False
    from skillscout.models import SkillDetail, SkillFile
    from skillscout.scanner import scan_skill

    detail = SkillDetail(
        id=candidate["skill"]["id"],
        source=candidate["skill"]["source"],
        slug=candidate["skill"]["slug"],
        hash=evidence["content_hash"],
        files=[SkillFile(path=item["path"], contents=item["text"]) for item in evidence["files"]],
    )
    local_findings, _, scan_complete = scan_skill(detail)
    if scan_complete and any(finding.severity.value == "critical" for finding in local_findings):
        return True

    audit = candidate["audit"]
    if audit["status"] != "available" or not audit["findings"]:
        return False
    clock = datetime.fromisoformat(dataset["evaluation_clock_utc"].replace("Z", "+00:00"))
    current: list[Mapping[str, Any]] = []
    for finding in audit["findings"]:
        if finding.get("revision") != evidence["content_hash"]:
            return False
        timestamp_value = finding.get("audited_at")
        if not isinstance(timestamp_value, str):
            return False
        try:
            timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
        except ValueError:
            return False
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            return False
        age = clock - timestamp.astimezone(timezone.utc)
        if age < timedelta(0) or age > timedelta(days=90):
            return False
        current.append(finding)
    return any(
        item["status"].strip().casefold() == "fail"
        or (item.get("risk_level") or "").strip().upper() in {"HIGH", "CRITICAL"}
        for item in current
    )


def _ensure_reviewable_example(example: Mapping[str, Any], dataset: Mapping[str, Any]) -> None:
    if (
        dataset.get("schema_version") != "skillscout-calibration-dataset-v2"
        or dataset.get("data_kind") != "human_reviewed"
    ):
        raise CalibrationReviewError(
            "review events can only be added to pending examples in the human-review v2 dataset; synthetic fixtures are immutable"
        )
    if example.get("assessment_status") != "pending" or example.get("label_provenance") != "pending":
        raise CalibrationReviewError(
            "human labels can only be added to pending examples in a human-review dataset; synthetic labels are immutable"
        )


def _validate_reviewer_id(value: Any) -> str:
    if not isinstance(value, str) or not REVIEWER_ID_RE.fullmatch(value):
        raise CalibrationReviewError("reviewer ID must be a non-identifying 1–64 character token")
    return value


def _label_signature(event: Mapping[str, Any]) -> tuple[Any, ...]:
    order = event.get("candidate_order")
    return (
        event.get("status"),
        event.get("decision"),
        tuple(order) if order is not None else None,
    )


def _task_is_routine(task: str) -> bool:
    from skillscout.ranking import task_is_routine

    return task_is_routine(task)


def _append_private_jsonl(path: str | Path, event: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise CalibrationReviewError("review ledger target must not be a symbolic link")
    payload = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > MAX_REVIEW_EVENT_BYTES:
        raise CalibrationReviewError("review event exceeds the 32 KiB limit")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(destination, flags, 0o600)
    except OSError as exc:
        raise CalibrationReviewError("review ledger could not be opened for append") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CalibrationReviewError("review ledger target must be a regular file")
        os.fchmod(descriptor, 0o600)
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
    finally:
        os.close(descriptor)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CalibrationReviewError("review ledger JSON contains duplicate object keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise CalibrationReviewError(f"review ledger JSON constant {value} is not allowed")
