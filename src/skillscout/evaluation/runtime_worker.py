"""Single-operation child process used by :mod:`skillscout.evaluation.runtime`."""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from dataclasses import replace
from typing import Any


def _error(category: str, termination_status: str = "unknown", evidence: str = "") -> dict[str, object]:
    return {
        "status": "error",
        "category": category,
        "termination_status": termination_status,
        "termination_evidence": evidence or "operation ended without a verified request termination signal",
    }


def _before_deadline(request: dict[str, Any], *, lookup: bool = False) -> bool:
    key = "lookup_deadline_monotonic_s" if lookup else "dispatch_deadline_monotonic_s"
    deadline = request.get(key)
    if deadline is None:
        return True
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or not math.isfinite(float(deadline)):
        return False
    now = time.monotonic()
    return now < float(deadline) if lookup else now <= float(deadline)


def _invocation_cap_remaining(request: dict[str, Any]) -> bool:
    deadline = request.get("invocation_deadline_monotonic_s")
    if deadline is None:
        return True
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or not math.isfinite(float(deadline)):
        return False
    return time.monotonic() < float(deadline)


def _skillscout(payload: dict[str, Any], request: dict[str, Any]) -> dict[str, object]:
    if not _before_deadline(request):
        return {"status": "not_started", "category": "pairing_deadline_expired_before_second_arm_start"}
    if not _invocation_cap_remaining(request):
        return _error("invocation_timeout", "confirmed", "invocation cap expired before SkillScraper dispatch")
    if set(payload) != {"task", "project_path"}:
        return _error("system_input_boundary_error", "confirmed", "no SkillScraper operation was dispatched")
    try:
        from skillscout.mcp_server import prepare_task

        result = asyncio.run(prepare_task(task=str(payload["task"]), project_path=str(payload["project_path"])))
    except Exception as exc:  # noqa: BLE001 - do not return exception text or credentials.
        return _error(f"skillscout_{type(exc).__name__.casefold()}")
    if result.status != "success" or result.decision is None:
        code = result.error.code if result.error is not None else "skillscout_invalid_response"
        if code in {"invalid_input", "project_path_error", "configuration_error"}:
            return _error(code, "confirmed", "SkillScraper returned before a catalog operation was started")
        return _error(code, "unknown", "SkillScraper returned an operational error without request termination telemetry")
    candidates = []
    termination_uncertain = False
    for position, item in enumerate(result.recommendations[:3], start=1):
        if item.rank != position:
            return _error("skillscout_candidate_order_error", "confirmed", "no result was recorded")
        content_hash = item.evidence.content_hash
        termination_uncertain = termination_uncertain or (
            item.evidence.evidence_status != "complete"
            or item.evidence.external_audit_status == "unavailable"
        )
        candidates.append(
            {
                "stable_id": item.skill.id,
                "source_slug": item.skill.slug,
                "rank": position,
                "content_hash": content_hash,
                "revision": content_hash,
                "source_url": None,
            }
        )
    return {
        "status": "ok",
        "result": {
            "decision": result.decision.value,
            "candidates": candidates,
            "partial": False,
            "internal_attempt_count": None,
            "termination_status": "unknown" if termination_uncertain else "confirmed",
            "termination_evidence": (
                "SkillScraper returned but one or more nested evidence states were unavailable; "
                "underlying request termination is not independently observable"
                if termination_uncertain
                else "SkillScraper returned a validated terminal prepare_task result"
            ),
        },
    }


def _ordinary_search(payload: dict[str, Any], request: dict[str, Any]) -> dict[str, object]:
    if not _before_deadline(request):
        return {"status": "not_started", "category": "pairing_deadline_expired_before_second_arm_start"}
    if not _invocation_cap_remaining(request):
        return _error("invocation_timeout", "confirmed", "invocation cap expired before search dispatch")
    if set(payload) != {"query", "limit"} or payload.get("limit") != 3:
        return _error("system_input_boundary_error", "confirmed", "no ordinary-search operation was dispatched")
    try:
        from skillscout.catalog import CatalogClient
        from skillscout.config import load_config

        config = replace(load_config(), retry_count=0)
        if not _before_deadline(request):
            return {"status": "not_started", "category": "pairing_deadline_expired_before_second_arm_start"}
        if not _invocation_cap_remaining(request):
            return _error("invocation_timeout", "confirmed", "invocation cap expired before search dispatch")
        with CatalogClient(config) as catalog:
            results = catalog.search(str(payload["query"]), limit=3)
    except Exception as exc:  # noqa: BLE001 - do not return upstream message text.
        return _error(f"ordinary_search_{type(exc).__name__.casefold()}")
    candidates = [
        {
            "stable_id": item.id,
            "source_slug": item.slug,
            "rank": position,
            "content_hash": item.content_hash,
            "revision": item.content_hash,
            "source_url": str(item.source_url) if item.source_url is not None else None,
        }
        for position, item in enumerate(results[:3], start=1)
    ]
    return {
        "status": "ok",
        "result": {
            "decision": None,
            "candidates": candidates,
            "partial": False,
            "internal_attempt_count": 1,
            "termination_status": "confirmed",
            "termination_evidence": "ordinary-search request returned a terminal catalog response",
        },
    }


def _lookup(payload: dict[str, Any], request: dict[str, Any]) -> dict[str, object]:
    is_detail = request.get("operation") == "detail_lookup"
    if not _before_deadline(request, lookup=True):
        return {"status": "not_started", "category": "lookup_deadline_expired_before_dispatch"}
    expected_payload_keys = {
        "arm", "candidate_id", "rank", "kind", "arm_content_hash", "arm_revision"
    }
    if set(payload) != expected_payload_keys or payload.get("kind") != ("detail" if is_detail else "audit"):
        return _error("lookup_input_error", "confirmed", "no evidence lookup was dispatched")
    try:
        from skillscout.application import _placeholder_skill
        from skillscout.catalog import CatalogClient
        from skillscout.config import load_config

        config = replace(load_config(), retry_count=0)
        skill = _placeholder_skill(str(payload["candidate_id"]), config.catalog_base_url)
        if not _before_deadline(request, lookup=True):
            return {"status": "not_started", "category": "lookup_deadline_expired_before_dispatch"}
        with CatalogClient(config) as catalog:
            if is_detail:
                detail = catalog.get_detail(skill)
                sufficient = bool(
                    detail.files
                    and any(isinstance(file.contents, str) and file.contents.strip() for file in detail.files)
                )
                revision = detail.content_hash
                return {
                    "status": "ok",
                    "result": {
                        "success": True,
                        "returned_candidate_id": detail.id,
                        "content_hash": detail.content_hash,
                        "revision": revision,
                        "identity_linkage": "matched" if detail.id == skill.id else "mismatch",
                        "revision_linkage": _revision_linkage(payload.get("arm_content_hash") or payload.get("arm_revision"), revision),
                        "detail_sufficient_for_relevance": sufficient,
                        "termination_status": "confirmed",
                        "termination_evidence": "catalog detail returned terminal identity-bound content",
                    },
                }

            audits, availability, _notes = catalog.get_audits(skill)
    except Exception as exc:  # noqa: BLE001 - do not return untrusted exception messages.
        return _error(f"lookup_{type(exc).__name__.casefold()}")

    expected_revision = payload.get("arm_content_hash") or payload.get("arm_revision")
    observed_revisions = [item.revision for item in audits if item.revision]
    unique_revisions = set(observed_revisions)
    audit_revision = next(iter(unique_revisions)) if len(unique_revisions) == 1 else None
    if availability == "unavailable":
        return {
            "status": "ok",
            "result": {
                "success": False,
                "returned_candidate_id": skill.id,
                "revision": audit_revision,
                "revision_linkage": _revision_linkage(expected_revision, audit_revision),
                "audit_status": "unavailable",
                "audit_matches_revision": None,
                "error_category": "audit_evidence_unavailable",
                "termination_status": "unknown",
                "termination_evidence": "audit lookup returned unavailable without underlying request termination telemetry",
            },
        }
    if availability == "none":
        audit_status = "none"
        matches = None
    elif expected_revision is None or not observed_revisions:
        audit_status = "unbound"
        matches = None
    elif all(item == expected_revision for item in observed_revisions):
        audit_status = "current"
        matches = True
    elif any(item == expected_revision for item in observed_revisions):
        audit_status = "mixed"
        matches = None
    else:
        audit_status = "stale"
        matches = False
    return {
        "status": "ok",
        "result": {
            "success": True,
            "returned_candidate_id": skill.id,
            "revision": audit_revision,
            "revision_linkage": _revision_linkage(expected_revision, audit_revision),
            "audit_status": audit_status,
            "audit_matches_revision": matches,
            "termination_status": "confirmed",
            "termination_evidence": "audit endpoint returned a terminal response; findings were not copied to reviewer data",
        },
    }


def _revision_linkage(expected: object, observed: object) -> str:
    if expected is None and observed is None:
        return "not_exposed"
    if expected is None or observed is None:
        return "unverified"
    return "matched" if expected == observed else "mismatch"


def dispatch(request: dict[str, Any]) -> dict[str, object]:
    if not isinstance(request, dict) or not isinstance(request.get("payload"), dict):
        return _error("worker_request_invalid", "confirmed", "no operation was dispatched")
    deadline = request.get("worker_deadline_monotonic_s")
    if deadline is not None and (
        not isinstance(deadline, (int, float)) or isinstance(deadline, bool)
        or not math.isfinite(float(deadline)) or time.monotonic() > float(deadline)
    ):
        return _error("worker_deadline_expired_before_dispatch", "confirmed", "no operation was dispatched")
    operation = request.get("operation")
    payload = request["payload"]
    if operation == "skillscout":
        return _skillscout(payload, request)
    if operation == "ordinary_search":
        return _ordinary_search(payload, request)
    if operation in {"detail_lookup", "audit_lookup"}:
        return _lookup(payload, request)
    return _error("worker_operation_invalid", "confirmed", "no operation was dispatched")


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.readline().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        result: dict[str, object] = _error("worker_request_invalid", "confirmed", "no operation was dispatched")
    else:
        try:
            result = dispatch(request)
        except Exception:  # noqa: BLE001 - last-resort protocol boundary; do not expose exception text.
            result = _error("worker_internal_failure")
    sys.stdout.write(json.dumps(result, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
