from __future__ import annotations

import httpx
import pytest
from dataclasses import replace

from skillscout.catalog import CatalogClient, CatalogDataError, CatalogUnavailableError
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
