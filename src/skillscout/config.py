"""Environment-based configuration with safe, bounded defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised when an environment setting is invalid."""


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Config:
    catalog_base_url: str
    catalog_token: str | None
    candidate_limit: int
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_response_bytes: int
    retry_count: int
    llm_api_key: str | None
    llm_base_url: str
    llm_model: str
    llm_send_task: bool


def load_config() -> Config:
    """Load and validate configuration from environment variables."""
    base_url = os.getenv("SKILLSCOUT_CATALOG_URL", "https://skills.sh").rstrip("/")
    parsed_url = urlparse(base_url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ConfigurationError(
            "SKILLSCOUT_CATALOG_URL must be an HTTPS URL without user info, query, or fragment"
        )

    llm_base_url = os.getenv("SKILLSCOUT_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    llm_parsed = urlparse(llm_base_url)
    if (
        llm_parsed.scheme != "https"
        or not llm_parsed.hostname
        or llm_parsed.username
        or llm_parsed.password
        or llm_parsed.query
        or llm_parsed.fragment
    ):
        raise ConfigurationError(
            "SKILLSCOUT_LLM_BASE_URL must be an HTTPS URL without user info, query, or fragment"
        )
    model = os.getenv("SKILLSCOUT_LLM_MODEL", "gpt-4o-mini")
    if not model or len(model) > 200:
        raise ConfigurationError("SKILLSCOUT_LLM_MODEL must contain between 1 and 200 characters")

    return Config(
        catalog_base_url=base_url,
        catalog_token=os.getenv("SKILLSCOUT_CATALOG_TOKEN") or os.getenv("VERCEL_OIDC_TOKEN"),
        candidate_limit=_bounded_int("SKILLSCOUT_CANDIDATE_LIMIT", 3, 1, 5),
        connect_timeout_seconds=5.0,
        read_timeout_seconds=15.0,
        max_response_bytes=2_000_000,
        retry_count=2,
        llm_api_key=os.getenv("SKILLSCOUT_LLM_API_KEY"),
        llm_base_url=llm_base_url,
        llm_model=model,
        llm_send_task=os.getenv("SKILLSCOUT_LLM_SEND_TASK", "").lower() in {"1", "true", "yes"},
    )

