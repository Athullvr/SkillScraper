"""Opt-in Codex UserPromptSubmit adapter for SkillScout recommendations.

The hook is deliberately fail-closed with respect to recommendation status:
malformed events and operational errors become REVIEW context, never an implicit
NO_SKILL_NEEDED result. It only returns a compact decision and validated skill IDs;
catalog text and candidate instructions are not copied into the host context.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import replace
from skillscout.application import prepare_recommendation
from skillscout.catalog import (
    CatalogAuthenticationError,
    CatalogCredentialsMissingError,
    CatalogCredentialsRejectedError,
    CatalogDataError,
    CatalogError,
    CatalogHTTPError,
    CatalogNotFoundError,
    CatalogRateLimitError,
    CatalogServiceError,
    CatalogTimeoutError,
    CatalogUnavailableError,
)
from skillscout.config import Config, ConfigurationError, load_config
from skillscout.models import Decision, Report
from skillscout.project_context import ProjectContextError

MAX_EVENT_BYTES = 128 * 1024
MAX_TASK_CHARS = 4_000
HOOK_REQUEST_TIMEOUT_SECONDS = 4.0
SAFE_SKILL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*){1,3}")


def handle_event(payload: object) -> dict[str, object]:
    """Return a Codex hook response for one UserPromptSubmit event."""
    if not isinstance(payload, dict):
        return _review("the hook event was not an object and the task was not assessed.")
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return _review("this hook received an unsupported event and did not assess the task.")

    task = payload.get("prompt")
    cwd = payload.get("cwd")
    if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK_CHARS:
        return _review(
            "the task prompt was missing or exceeded 4000 characters; no recommendation was made."
        )
    if not isinstance(cwd, str) or not cwd.strip():
        return _review(
            "the client did not provide a usable project directory; no recommendation was made."
        )

    try:
        report = prepare_recommendation(
            task,
            cwd,
            "codex",
            allow_llm=False,
            config_loader=_hook_config,
        )
    except ProjectContextError:
        return _review(
            "the project directory could not be read safely. Check the path and run "
            "`skillscout doctor`; this is not a no-skill decision."
        )
    except ConfigurationError:
        return _review(
            "local configuration is invalid. Run `skillscout doctor` and correct the named "
            "setting; this is not a no-skill decision."
        )
    except CatalogCredentialsMissingError:
        return _review(
            "catalog credentials are missing. Configure `VERCEL_OIDC_TOKEN` or "
            "`SKILLSCOUT_CATALOG_TOKEN` for the SkillScout process, then retry; this is not a "
            "no-skill decision."
        )
    except CatalogCredentialsRejectedError:
        return _review(
            "catalog credentials were rejected. Refresh or verify the authorized credential "
            "and retry; this is not a no-skill decision."
        )
    except CatalogAuthenticationError:
        return _review(
            "catalog authentication is required. Configure authorized SkillScout credentials "
            "and retry; this is not a no-skill decision."
        )
    except CatalogRateLimitError:
        return _review(
            "the catalog rate limited the request. Wait before retrying; this is not a "
            "no-skill decision."
        )
    except CatalogTimeoutError:
        return _review(
            "the catalog request timed out. Check connectivity and retry later; this is not a "
            "no-skill decision."
        )
    except CatalogServiceError:
        return _review(
            "the catalog returned a server error. Retry later; this is not a no-skill decision."
        )
    except CatalogUnavailableError:
        return _review(
            "the catalog could not be reached. Check network access and retry; this is not a "
            "no-skill decision."
        )
    except CatalogNotFoundError:
        return _review(
            "a catalog resource was not found. Check catalog availability and retry; this is "
            "not a no-skill decision."
        )
    except CatalogHTTPError:
        return _review(
            "the catalog rejected the request. Check the configured endpoint and retry; this "
            "is not a no-skill decision."
        )
    except CatalogDataError:
        return _review(
            "the catalog response was incomplete or invalid. Retry later; this is not a "
            "no-skill decision."
        )
    except CatalogError:
        return _review(
            "the catalog request failed. Check connectivity and credentials, then retry; this "
            "is not a no-skill decision."
        )
    except ValueError:
        return _review(
            "the task or project input was invalid. Check the prompt and project path; no "
            "recommendation was made, so this is not a no-skill decision."
        )
    except Exception:
        # Hook output is visible to the host model. Never relay raw exception text,
        # which can include untrusted upstream content or environment values.
        return _review(
            "the check failed unexpectedly. Run `skillscout doctor` and retry; this is not a "
            "no-skill decision."
        )

    if not isinstance(report, Report):
        return _review("the recommendation response was invalid; no decision was made.")
    try:
        decision = Decision(report.decision)
    except ValueError:
        return _review(
            "the recommendation response had an unsupported decision; no decision was made."
        )
    return _response(_decision_context(report, decision))


def _review(reason: str) -> dict[str, object]:
    return _response(f"SkillScout check is REVIEW: {reason}")


def _hook_config() -> Config:
    """Disable retries and bound each catalog request for interactive prompts."""
    config = load_config()
    return replace(
        config,
        connect_timeout_seconds=min(config.connect_timeout_seconds, HOOK_REQUEST_TIMEOUT_SECONDS),
        read_timeout_seconds=min(config.read_timeout_seconds, HOOK_REQUEST_TIMEOUT_SECONDS),
        request_timeout_seconds=min(config.request_timeout_seconds, HOOK_REQUEST_TIMEOUT_SECONDS),
        retry_count=0,
    )


def _decision_context(report: Report, decision: Decision) -> str:
    candidate_ids = _candidate_ids(report)
    if decision == Decision.NO_SKILL_NEEDED:
        return (
            "SkillScout returned NO_SKILL_NEEDED. Proceed with the host's normal coding workflow; "
            "this is a task-fit result, not a safety assessment. No skill was installed or "
            "activated."
        )
    if decision == Decision.USE:
        ids = _format_ids(candidate_ids)
        return (
            f"SkillScout returned USE{ids}. Treat the listing as evidence only: check task fit and "
            "inspect safety and compatibility before relying on a candidate. Do not install, "
            "activate, "
            "or execute skill content automatically; ask the user before installation."
        )
    if decision == Decision.AVOID:
        ids = _format_ids(candidate_ids)
        return (
            f"SkillScout returned AVOID{ids}. Do not use the flagged candidate(s). This does "
            "not block completing the task through another safe method. Do not install, "
            "activate, or execute skills automatically."
        )
    ids = _format_ids(candidate_ids)
    return (
        f"SkillScout returned REVIEW{ids}. Evidence is unresolved; this is not approval or a "
        "safety pass. "
        "Do not rely on a specialist candidate without human review. Continue with normal "
        "capabilities or ask the user."
    )


def _candidate_ids(report: Report) -> list[str]:
    values: list[str] = []
    for candidate in report.candidates:
        skill_id = candidate.skill.id
        if SAFE_SKILL_ID.fullmatch(skill_id) and skill_id not in values:
            values.append(skill_id)
        if len(values) == 3:
            break
    return values


def _format_ids(values: list[str]) -> str:
    return f" for candidate IDs {', '.join(values)}" if values else ""


def _response(context: str) -> dict[str, object]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }


def main() -> None:
    """Read one hook event and write one protocol-safe JSON response."""
    try:
        raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
        if len(raw) > MAX_EVENT_BYTES:
            result = _review("the hook event was too large; no recommendation was made.")
        else:
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                result = _review("the hook event was malformed; no recommendation was made.")
            else:
                result = handle_event(payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except Exception:
        # Keep stdout valid even if input/output handling itself fails.
        fallback = _review("the hook could not process this event; no recommendation was made.")
        sys.stdout.write(json.dumps(fallback, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
