from __future__ import annotations

import httpx
import pytest

from skillscout.catalog import CatalogClient, CatalogDataError, CatalogUnavailableError


def _catalog(config, handler, delays: list[float] | None = None) -> tuple[CatalogClient, httpx.Client]:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return CatalogClient(
        config,
        client=client,
        sleep=(delays.append if delays is not None else lambda _delay: None),
        random_value=lambda: 0.0,
    ), client


def test_empty_search_response_is_a_valid_empty_result(config):
    catalog, client = _catalog(
        config,
        lambda _request: httpx.Response(200, json={"data": []}),
    )
    try:
        assert catalog.search("FastAPI authentication") == []
    finally:
        client.close()


@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, json={}), httpx.Response(200, json={"data": {}}), httpx.Response(200, content=b"not-json")],
)
def test_malformed_search_envelopes_are_rejected(config, response):
    catalog, client = _catalog(config, lambda _request: response)
    try:
        with pytest.raises(CatalogDataError):
            catalog.search("FastAPI authentication")
    finally:
        client.close()


def test_read_timeout_retries_only_within_configured_bound(config):
    requests = 0
    delays: list[float] = []

    def handler(request):
        nonlocal requests
        requests += 1
        raise httpx.ReadTimeout("read timeout", request=request)

    catalog, client = _catalog(config, handler, delays)
    try:
        with pytest.raises(CatalogUnavailableError, match="bounded retries"):
            catalog.search("FastAPI authentication")
    finally:
        client.close()

    assert requests == config.retry_count + 1
    assert delays == [0.25, 0.5]


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_rate_limits_and_server_errors_have_bounded_retries(config, status_code):
    requests = 0
    delays: list[float] = []

    def handler(_request):
        nonlocal requests
        requests += 1
        headers = {"Retry-After": "0"} if status_code == 429 else {}
        return httpx.Response(status_code, headers=headers)

    catalog, client = _catalog(config, handler, delays)
    try:
        with pytest.raises(CatalogUnavailableError):
            catalog.search("FastAPI authentication")
    finally:
        client.close()

    assert requests == config.retry_count + 1
    assert len(delays) == config.retry_count
    if status_code == 429:
        assert delays == [0.0, 0.0]


def test_remote_protocol_error_is_treated_as_retryable_transport_error(config):
    requests = 0
    delays: list[float] = []

    def handler(request):
        nonlocal requests
        requests += 1
        if requests == 1:
            raise httpx.RemoteProtocolError("truncated response", request=request)
        return httpx.Response(200, json={"data": []})

    catalog, client = _catalog(config, handler, delays)
    try:
        assert catalog.search("FastAPI authentication") == []
    finally:
        client.close()

    assert requests == 2
    assert delays == [0.25]
