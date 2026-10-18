from __future__ import annotations

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


def test_llm_requires_task_text_opt_in_to_be_active(monkeypatch):
    monkeypatch.setenv("SKILLSCOUT_LLM_API_KEY", "test-key")
    monkeypatch.delenv("SKILLSCOUT_LLM_SEND_TASK", raising=False)

    config = load_config()

    assert config.llm_api_key is not None
    assert not config.llm_send_task
