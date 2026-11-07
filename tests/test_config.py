from __future__ import annotations

from pathlib import Path

import pytest

from skillscout.config import ConfigurationError, load_config


def test_candidate_limit_is_bounded(monkeypatch):
    monkeypatch.setenv("SKILLSCOUT_CANDIDATE_LIMIT", "5")
    assert load_config().candidate_limit == 5

    monkeypatch.setenv("SKILLSCOUT_CANDIDATE_LIMIT", "6")
    with pytest.raises(ConfigurationError, match="between 1 and 5"):
        load_config()


def test_catalog_urls_cannot_embed_credentials_or_query_parameters(monkeypatch):
    monkeypatch.setenv("SKILLSCOUT_CATALOG_URL", "https://user:pass@example.test")
    with pytest.raises(ConfigurationError, match="without user info"):
        load_config()

    monkeypatch.setenv("SKILLSCOUT_CATALOG_URL", "https://example.test?token=secret")
    with pytest.raises(ConfigurationError, match="without user info"):
        load_config()


def test_catalog_credential_precedence_prefers_compatibility_override(monkeypatch):
    monkeypatch.setenv("SKILLSCOUT_CATALOG_TOKEN", "compatibility-token")
    monkeypatch.setenv("VERCEL_OIDC_TOKEN", "vercel-token")

    assert load_config().catalog_token == "compatibility-token"

    monkeypatch.setenv("SKILLSCOUT_CATALOG_TOKEN", "")
    assert load_config().catalog_token == "vercel-token"


def test_catalog_credentials_are_optional_at_startup(monkeypatch):
    monkeypatch.delenv("SKILLSCOUT_CATALOG_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_OIDC_TOKEN", raising=False)

    assert load_config().catalog_token is None


def test_render_blueprint_uses_the_catalog_token_variable_read_by_config(monkeypatch):
    monkeypatch.delenv("VERCEL_OIDC_TOKEN", raising=False)
    monkeypatch.setenv("SKILLSCOUT_CATALOG_TOKEN", "synthetic-catalog-token")

    assert load_config().catalog_token == "synthetic-catalog-token"

    render_yaml = Path(__file__).parents[1] / "render.yaml"
    render_config = render_yaml.read_text(encoding="utf-8")
    assert "- key: SKILLSCOUT_CATALOG_TOKEN" in render_config
    assert "- key: SKILLSCRAPER_CATALOG_TOKEN" not in render_config


def test_remote_mcp_docs_use_the_configured_codex_token_variable():
    docs = Path(__file__).parents[1] / "docs" / "remote-mcp.md"
    content = docs.read_text(encoding="utf-8")

    assert 'bearer_token_env_var = "SKILLSCRAPER_TOKEN"' in content
    assert '"Authorization": "Bearer ${SKILLSCRAPER_TOKEN}"' in content
    assert "SKILLSCRAPER_MCP_TOKEN" not in content


def test_llm_requires_task_text_opt_in_to_be_active(monkeypatch):
    monkeypatch.setenv("SKILLSCOUT_LLM_API_KEY", "test-key")
    monkeypatch.delenv("SKILLSCOUT_LLM_SEND_TASK", raising=False)

    config = load_config()

    assert config.llm_api_key is not None
    assert not config.llm_send_task


@pytest.mark.parametrize("name", ["SKILLSCOUT_CATALOG_TOKEN", "VERCEL_OIDC_TOKEN", "SKILLSCOUT_LLM_API_KEY"])
def test_http_credentials_reject_whitespace_without_echoing_the_value(monkeypatch, name):
    secret = "fixture credential with spaces"
    monkeypatch.setenv(name, secret)

    with pytest.raises(ConfigurationError) as error:
        load_config()

    assert name in str(error.value)
    assert secret not in str(error.value)


def test_invalid_llm_task_opt_in_is_reported(monkeypatch):
    monkeypatch.setenv("SKILLSCOUT_LLM_SEND_TASK", "sometimes")

    with pytest.raises(ConfigurationError, match="SKILLSCOUT_LLM_SEND_TASK"):
        load_config()


def test_total_request_timeout_is_bounded(monkeypatch):
    monkeypatch.setenv("SKILLSCOUT_REQUEST_TIMEOUT_SECONDS", "45")
    assert load_config().request_timeout_seconds == 45

    monkeypatch.setenv("SKILLSCOUT_REQUEST_TIMEOUT_SECONDS", "121")
    with pytest.raises(ConfigurationError, match="SKILLSCOUT_REQUEST_TIMEOUT_SECONDS"):
        load_config()
