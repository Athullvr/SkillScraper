from __future__ import annotations

import json

import httpx
import pytest
from dataclasses import replace

from skillscout.llm import LLMAdapter, LLMError


def _adapter(config, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LLMAdapter(config, client=client), client


def _assess(adapter):
    return adapter.assess_task_fit(
        task="Implement FastAPI OAuth login",
        skill_id="acme/skills/fastapi-auth",
        skill_name="FastAPI authentication",
        skill_description="Use FastAPI and OAuth.",
    )


def test_missing_llm_credentials_does_not_send_a_request(config):
    requests = 0

    def handler(_request):
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    adapter, client = _adapter(replace(config, llm_api_key=None, llm_send_task=True), handler)
    try:
        assert _assess(adapter) is None
    finally:
        client.close()

    assert requests == 0


def test_llm_requires_explicit_task_transmission_opt_in(config):
    requests = 0

    def handler(_request):
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    adapter, client = _adapter(
        replace(config, llm_api_key="configured-key", llm_send_task=False),
        handler,
    )
    try:
        assert _assess(adapter) is None
    finally:
        client.close()

    assert requests == 0


def test_opted_in_llm_request_is_bounded_and_excludes_project_data(config):
    response_body = {
        "choices": [
            {
                "message": {
                    "content": '{"skill_id":"acme/skills/fastapi-auth","score":0.7,"rationale":["Task match"]}'
                }
            }
        ]
    }
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=response_body)

    adapter, client = _adapter(
        replace(config, llm_api_key="configured-key", llm_send_task=True),
        handler,
    )
    task = "api_key=super-secret-value Implement FastAPI OAuth " + "details " * 500
    description = "Candidate guidance " + "bounded " * 500
    try:
        assessment = adapter.assess_task_fit(
            task=task,
            skill_id="acme/skills/fastapi-auth",
            skill_name="FastAPI authentication",
            skill_description=description,
        )
    finally:
        client.close()

    request_body = json.loads(captured[0].content)
    user_content = json.loads(request_body["messages"][1]["content"])
    assert assessment is not None and assessment.score == 0.7
    assert len(user_content["task"]) <= 2_000
    assert len(user_content["candidate"]["description_untrusted"]) <= 2_000
    assert "super-secret-value" not in captured[0].content.decode()
    assert "project" not in user_content


def test_llm_provider_failure_is_reported_for_deterministic_fallback(config):
    def handler(_request):
        return httpx.Response(503, json={"error": "unavailable"})

    adapter, client = _adapter(
        replace(config, llm_api_key="configured-key", llm_send_task=True),
        handler,
    )
    try:
        with pytest.raises(LLMError, match="HTTP 503"):
            _assess(adapter)
    finally:
        client.close()


def test_llm_rejects_valid_json_with_wrong_schema(config):
    body = {
        "choices": [
            {"message": {"content": '{"skill_id":"acme/skills/fastapi-auth","score":2,"rationale":[]}'}},
        ]
    }
    adapter, client = _adapter(
        replace(config, llm_api_key="configured-key", llm_send_task=True),
        lambda _request: httpx.Response(200, json=body),
    )
    try:
        with pytest.raises(LLMError):
            _assess(adapter)
    finally:
        client.close()
