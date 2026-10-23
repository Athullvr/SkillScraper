from __future__ import annotations

import time
from dataclasses import replace

import httpx
import pytest

from skillscout.catalog import (
    CatalogClient,
    CatalogCredentialsMissingError,
    CatalogCredentialsRejectedError,
    CatalogDataError,
    CatalogHTTPError,
    CatalogNotFoundError,
    CatalogRateLimitError,
    CatalogServiceError,
    CatalogUnavailableError,
)
from skillscout.models import CatalogSkill


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


def test_search_error_envelope_is_not_treated_as_an_empty_result(config):
    catalog, client = _catalog(
        config,
        lambda _request: httpx.Response(
            200, json={"data": [], "error": {"code": "temporarily_unavailable"}}
        ),
    )
    try:
        with pytest.raises(CatalogDataError, match="error envelope"):
            catalog.search("FastAPI authentication")
    finally:
        client.close()


def test_search_detail_and_revision_bound_audit_preserve_provenance(config):
    skill_id = "acme/skills/fastapi-auth"
    listing = {
        "id": skill_id,
        "name": "FastAPI authentication",
        "source": "acme/skills",
        "slug": "fastapi-auth",
        "installs": 17,
        "url": "https://skills.sh/acme/skills/fastapi-auth",
        "installUrl": "https://github.com/acme/skills",
    }

    def handler(request):
        if request.url.path.endswith("/skills/search"):
            return httpx.Response(200, json={"data": [listing]})
        if request.url.path.endswith("/acme/skills/fastapi-auth"):
            if "/audit/" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "id": skill_id,
                        "hash": "revision-7",
                        "audits": [
                            {
                                "provider": "FixtureAudit",
                                "status": "pass",
                                "auditedAt": "2026-10-17T12:00:00Z",
                            }
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": skill_id,
                    "source": "acme/skills",
                    "slug": "fastapi-auth",
                    "hash": "revision-7",
                    "files": [{"path": "SKILL.md", "contents": "FastAPI OAuth guidance."}],
                },
            )
        return httpx.Response(404)

    catalog, client = _catalog(config, handler)
    try:
        skill = catalog.search("FastAPI authentication")[0]
        detail = catalog.get_detail(skill)
        audits, state, notes = catalog.get_audits(skill)
    finally:
        client.close()

    assert skill.id == skill_id
    assert str(skill.source_url) == "https://skills.sh/acme/skills/fastapi-auth"
    assert str(skill.install_url) == "https://github.com/acme/skills"
    assert skill.retrieved_at.tzinfo is not None
    assert detail.content_hash == "revision-7"
    assert audits[0].revision == "revision-7"
    assert audits[0].provider == "FixtureAudit"
    assert state == "available"
    assert notes == []


def test_audit_authentication_failure_remains_unknown(config):
    skill = CatalogSkill(
        id="acme/skills/fastapi-auth",
        source="acme/skills",
        slug="fastapi-auth",
        name="FastAPI authentication",
    )
    catalog, client = _catalog(
        config,
        lambda _request: httpx.Response(401, json={"error": "unauthorized"}),
    )
    try:
        audits, state, notes = catalog.get_audits(skill)
    finally:
        client.close()

    assert audits == []
    assert state == "unavailable"
    assert "requires credentials" in notes[0]


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


def test_malformed_catalog_skill_entry_is_rejected(config):
    catalog, client = _catalog(
        config,
        lambda _request: httpx.Response(200, json={"data": [{}]}),
    )
    try:
        with pytest.raises(CatalogDataError, match="expected schema"):
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
        expected_error = CatalogRateLimitError if status_code == 429 else CatalogServiceError
        with pytest.raises(expected_error):
            catalog.search("FastAPI authentication")
    finally:
        client.close()

    assert requests == config.retry_count + 1
    assert len(delays) == config.retry_count
    if status_code == 429:
        assert delays == [0.0, 0.0]


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (301, CatalogHTTPError),
        (400, CatalogHTTPError),
        (404, CatalogNotFoundError),
    ],
)
def test_permanent_http_responses_are_classified_without_retry(config, status_code, expected_error):
    requests = 0

    def handler(_request):
        nonlocal requests
        requests += 1
        return httpx.Response(status_code)

    catalog, client = _catalog(config, handler)
    try:
        with pytest.raises(expected_error) as error:
            catalog.search("FastAPI authentication")
    finally:
        client.close()

    assert error.value.status_code == status_code
    assert requests == 1


@pytest.mark.parametrize(
    ("token", "expected_error"),
    [
        (None, CatalogCredentialsMissingError),
        ("fixture-token-never-log", CatalogCredentialsRejectedError),
    ],
)
def test_authentication_failures_distinguish_missing_and_rejected_credentials(
    config, token, expected_error
):
    from dataclasses import replace

    configured = replace(config, catalog_token=token)
    requests = 0

    def handler(_request):
        nonlocal requests
        requests += 1
        return httpx.Response(401)

    catalog, client = _catalog(configured, handler)
    try:
        with pytest.raises(expected_error) as error:
            catalog.search("FastAPI authentication")
    finally:
        client.close()

    assert requests == 1
    assert "fixture-token-never-log" not in str(error.value)


def test_rate_limit_with_excessive_retry_after_fails_without_unbounded_wait(config):
    requests = 0
    delays: list[float] = []

    def handler(_request):
        nonlocal requests
        requests += 1
        return httpx.Response(429, headers={"Retry-After": "301"})

    catalog, client = _catalog(config, handler, delays)
    try:
        with pytest.raises(CatalogRateLimitError, match="bounded retry window"):
            catalog.search("FastAPI authentication")
    finally:
        client.close()

    assert requests == 1
    assert delays == []


def test_local_protocol_failure_is_not_retried(config):
    requests = 0
    delays: list[float] = []

    def handler(request):
        nonlocal requests
        requests += 1
        raise httpx.LocalProtocolError("invalid local request", request=request)

    catalog, client = _catalog(config, handler, delays)
    try:
        with pytest.raises(CatalogUnavailableError, match="verify the catalog URL"):
            catalog.search("FastAPI authentication")
    finally:
        client.close()

    assert requests == 1
    assert delays == []


def test_total_request_deadline_bounds_slow_response_streams(config):
    slow_config = replace(config, request_timeout_seconds=0.001, retry_count=0)
    requests = 0

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            time.sleep(0.01)
            yield b'{"data":[]}'

        def close(self):
            return None

    def handler(_request):
        nonlocal requests
        requests += 1
        return httpx.Response(200, stream=SlowStream())

    catalog, client = _catalog(slow_config, handler)
    try:
        with pytest.raises(CatalogUnavailableError, match="configured timeout"):
            catalog.search("FastAPI authentication")
    finally:
        client.close()

    assert requests == 1


def test_search_marks_full_or_paged_result_windows_incomplete(config):
    skill = {
        "id": "acme/skills/fastapi-auth",
        "name": "FastAPI Auth",
        "source": "acme/skills",
        "slug": "fastapi-auth",
        "installs": 3,
    }
    payload = {"data": [skill], "hasMore": True}
    catalog, client = _catalog(
        config,
        lambda _request: httpx.Response(200, json=payload),
    )
    try:
        results = catalog.search("FastAPI authentication", limit=1)
    finally:
        client.close()

    assert isinstance(results, list)
    assert len(results) == 1
    assert not results.complete


def test_explicit_end_of_search_page_marks_full_limit_complete(config):
    skill = {
        "id": "acme/skills/fastapi-auth",
        "name": "FastAPI Auth",
        "source": "acme/skills",
        "slug": "fastapi-auth",
        "installs": 3,
    }
    catalog, client = _catalog(
        config,
        lambda _request: httpx.Response(200, json={"data": [skill], "hasMore": False}),
    )
    try:
        results = catalog.search("FastAPI authentication", limit=1)
    finally:
        client.close()

    assert results.complete


def test_invalid_search_completeness_metadata_is_rejected(config):
    catalog, client = _catalog(
        config,
        lambda _request: httpx.Response(200, json={"data": [], "hasMore": "yes"}),
    )
    try:
        with pytest.raises(CatalogDataError, match="hasMore metadata"):
            catalog.search("FastAPI authentication")
    finally:
        client.close()


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


def test_retry_after_is_respected_for_server_errors(config):
    requests = 0
    delays: list[float] = []

    def handler(_request):
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(503, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"data": []})

    catalog, client = _catalog(config, handler, delays)
    try:
        assert catalog.search("FastAPI authentication") == []
    finally:
        client.close()

    assert requests == 2
    assert delays == [3.0]


def test_oversized_catalog_body_is_rejected(config):
    small_config = replace(config, max_response_bytes=16)
    catalog, client = _catalog(
        small_config,
        lambda _request: httpx.Response(200, content=b"{" + b" " * 40 + b"}"),
    )
    try:
        with pytest.raises(CatalogDataError, match="size limit"):
            catalog.search("FastAPI authentication")
    finally:
        client.close()
