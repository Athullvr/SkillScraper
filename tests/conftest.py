from __future__ import annotations

import pytest

from skillscout.config import Config


@pytest.fixture
def config() -> Config:
    return Config(
        catalog_base_url="https://skills.test",
        catalog_token=None,
        candidate_limit=3,
        connect_timeout_seconds=0.2,
        read_timeout_seconds=0.2,
        max_response_bytes=2_000_000,
        retry_count=2,
        llm_api_key=None,
        llm_base_url="https://llm.test/v1",
        llm_model="test-model",
        llm_send_task=False,
    )

