from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest
import respx

from skillscout.llm import LLMAdapter, LLMError, redact_secrets


def provider_response(skill_id: str = "acme/skills/fastapi-auth", content: str | None = None):
    structured = content or (
        '{"skill_id":"acme/skills/fastapi-auth","score":0.8,'
        '"rationale":["It covers the requested authentication flow."]}'
    )
    return {
        "choices": [{"message": {"content": structured}}],
    }


def test_llm_sends_only_opted_in_bounded_task_and_candidate(config):
    llm_config = replace(config, llm_api_key="provider-secret", llm_send_task=True)
    with respx.mock(base_url="https://llm.test") as router:
        route = router.post("/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=provider_response())
        )
        with LLMAdapter(llm_config) as adapter:
            assessment = adapter.assess_task_fit(
                task="Add OAuth login",
                skill_id="acme/skills/fastapi-auth",
                skill_name="FastAPI authentication",
                skill_description="Use OAuth with FastAPI.",
            )

    sent = route.calls[0].request.read().decode("utf-8")
    request_payload = json.loads(sent)
    user_content = json.loads(request_payload["messages"][1]["content"])
    assert assessment.score == 0.8
    assert "Add OAuth login" in sent
    assert "Use OAuth with FastAPI." in sent
    assert "project" not in user_content
    assert "provider-secret" not in sent


def test_llm_is_disabled_without_explicit_task_transmission_opt_in(config):
    llm_config = replace(config, llm_api_key="provider-secret", llm_send_task=False)
    with respx.mock(base_url="https://llm.test") as router:
        with LLMAdapter(llm_config) as adapter:
            assessment = adapter.assess_task_fit(
                task="task",
                skill_id="acme/skills/fastapi-auth",
                skill_name="FastAPI authentication",
                skill_description="description",
            )

    assert assessment is None
    assert not router.calls


def test_llm_rejects_malformed_structured_output(config):
    llm_config = replace(config, llm_api_key="provider-secret", llm_send_task=True)
    with respx.mock(base_url="https://llm.test") as router:
        router.post("/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json=provider_response(content='{"skill_id":"wrong","score":4,"rationale":[]}'),
            )
        )
        with LLMAdapter(llm_config) as adapter:
            with pytest.raises(LLMError):
                adapter.assess_task_fit(
                    task="task",
                    skill_id="acme/skills/fastapi-auth",
                    skill_name="FastAPI authentication",
                    skill_description="description",
                )


def test_secret_redaction_masks_tokens_and_assigned_values():
    value = (
        'Set AWS_SECRET_ACCESS_KEY="super-secret value" and use token '
        "sk-1234567890abcdef1234567890"
    )

    redacted = redact_secrets(value)

    assert "super-secret" not in redacted
    assert "value" not in redacted
    assert "sk-1234567890abcdef1234567890" not in redacted

