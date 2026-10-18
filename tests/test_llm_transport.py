from __future__ import annotations

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
