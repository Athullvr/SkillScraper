from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from typing import Iterator

import httpx
import pytest

from skillscout.llm import LLMAdapter, LLMError, redact_secrets


def provider_response(skill_id: str = "acme/skills/fastapi-auth", content: str | None = None):
    structured = content or (
        '{"skill_id":"acme/skills/fastapi-auth","score":0.8,'
        '"rationale":["It covers the requested authentication flow."]}'
    )
    return {
        "choices": [{"message": {"content": structured}}],
    }


@contextmanager
def mocked_adapter(config, handler) -> Iterator[LLMAdapter]:
    expected_url = httpx.URL(f"{config.llm_base_url.rstrip('/')}/chat/completions")

    def dispatch(request):
        assert request.method == "POST"
        assert request.url == expected_url
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(dispatch))
    try:
        with LLMAdapter(config, client=client) as adapter:
            yield adapter
    finally:
        client.close()


def test_llm_sends_only_opted_in_bounded_task_and_candidate(config):
    llm_config = replace(config, llm_api_key="provider-secret", llm_send_task=True)
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=provider_response())

    with mocked_adapter(llm_config, handler) as adapter:
        assessment = adapter.assess_task_fit(
            task="Add OAuth login",
            skill_id="acme/skills/fastapi-auth",
            skill_name="FastAPI authentication",
            skill_description="Use OAuth with FastAPI.",
        )

    sent = captured[0].content.decode("utf-8")
    request_payload = json.loads(sent)
    user_content = json.loads(request_payload["messages"][1]["content"])
    assert assessment.score == 0.8
    assert "Add OAuth login" in sent
    assert "Use OAuth with FastAPI." in sent
    assert "project" not in user_content
    assert "provider-secret" not in sent


def test_llm_is_disabled_without_explicit_task_transmission_opt_in(config):
    llm_config = replace(config, llm_api_key="provider-secret", llm_send_task=False)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=provider_response())

    with mocked_adapter(llm_config, handler) as adapter:
        assessment = adapter.assess_task_fit(
            task="task",
            skill_id="acme/skills/fastapi-auth",
            skill_name="FastAPI authentication",
            skill_description="description",
        )

    assert assessment is None
    assert calls == []


def test_llm_rejects_malformed_structured_output(config):
    llm_config = replace(config, llm_api_key="provider-secret", llm_send_task=True)
    with mocked_adapter(
        llm_config,
        lambda _request: httpx.Response(
            200,
            json=provider_response(content='{"skill_id":"wrong","score":4,"rationale":[]}'),
        ),
    ) as adapter:
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
        "sk-1234567890abcdef1234567890; Authorization: Bearer short-token-value"
    )

    redacted = redact_secrets(value)

    assert "super-secret" not in redacted
    assert "value" not in redacted
    assert "sk-1234567890abcdef1234567890" not in redacted
    assert "short-token-value" not in redacted

