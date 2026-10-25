from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from typing import Iterator

import httpx
import pytest

from skillscout.catalog import CatalogClient, CatalogDataError

BASE = "https://skills.test"


def listing(skill_id: str = "acme/skills/fastapi-auth") -> dict:
    source, slug = skill_id.rsplit("/", 1)
    return {
        "id": skill_id,
        "slug": slug,
        "name": "FastAPI Auth",
        "source": source,
        "installs": 42,
        "sourceType": "github",
        "installUrl": "https://github.com/acme/skills",
        "url": f"https://skills.sh/{skill_id}",
    }


@contextmanager
def mocked_catalog(config, handler, *, sleep=lambda _delay: None) -> Iterator[CatalogClient]:
    def dispatch(request):
        assert request.method == "GET"
        assert request.url.host == httpx.URL(BASE).host
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(dispatch))
    try:
        with CatalogClient(config, client=client, sleep=sleep, random_value=lambda: 0.0) as catalog:
            yield catalog
    finally:
        client.close()


def test_search_validates_and_preserves_catalog_provenance(config):
    def handler(request):
        assert request.url.path == "/api/v1/skills/search"
        assert request.url.params["q"] == "FastAPI authentication"
        assert request.url.params["limit"] == "4"
        return httpx.Response(200, json={"data": [listing()]})

    with mocked_catalog(config, handler) as catalog:
        found = catalog.search("FastAPI authentication", limit=4)

    assert found[0].id == "acme/skills/fastapi-auth"
    assert str(found[0].source_url) == "https://skills.sh/acme/skills/fastapi-auth"
    assert str(found[0].install_url) == "https://github.com/acme/skills"
    assert found[0].retrieved_at.tzinfo is not None


def test_detail_and_audit_revision_are_validated_and_bound(config):
    def handler(request):
        if request.url.path == "/api/v1/skills/search":
            return httpx.Response(200, json={"data": [listing()]})
        if request.url.path == "/api/v1/skills/acme/skills/fastapi-auth":
            return httpx.Response(
                200,
                json={
                    "id": "acme/skills/fastapi-auth",
                    "source": "acme/skills",
                    "slug": "fastapi-auth",
                    "hash": "revision-2",
                    "files": [{"path": "SKILL.md", "contents": "---\nname: FastAPI Auth\n---"}],
                },
            )
        if request.url.path == "/api/v1/skills/audit/acme/skills/fastapi-auth":
            return httpx.Response(
                200,
                json={
                    "id": "acme/skills/fastapi-auth",
                    "hash": "revision-2",
                    "audits": [
                        {
                            "provider": "Socket",
                            "status": "pass",
                            "summary": "No issues",
                            "auditedAt": "2026-10-17T12:00:00Z",
                        }
                    ],
                },
            )
        return httpx.Response(404)

    with mocked_catalog(config, handler) as catalog:
        skill = catalog.search("FastAPI auth")[0]
        detail = catalog.get_detail(skill)
        audits, state, notes = catalog.get_audits(skill)

    assert detail.content_hash == "revision-2"
    assert detail.files[0].path == "SKILL.md"
    assert state == "available"
    assert audits[0].revision == "revision-2"
    assert notes == []


def test_audit_authentication_failure_is_unknown_not_a_search_failure(config):
    def handler(request):
        if request.url.path == "/api/v1/skills/search":
            return httpx.Response(200, json={"data": [listing()]})
        assert request.url.path == "/api/v1/skills/audit/acme/skills/fastapi-auth"
        return httpx.Response(401, json={"error": "unauthorized"})

    with mocked_catalog(config, handler) as catalog:
        skill = catalog.search("FastAPI auth")[0]
        audits, state, notes = catalog.get_audits(skill)

    assert audits == []
    assert state == "unavailable"
    assert "configured catalog credential was rejected" in notes[0]


def test_missing_audit_404_is_recorded_as_no_result(config):
    def handler(request):
        if request.url.path == "/api/v1/skills/search":
            return httpx.Response(200, json={"data": [listing()]})
        assert request.url.path == "/api/v1/skills/audit/acme/skills/fastapi-auth"
        return httpx.Response(404, json={"error": "not_found"})

    with mocked_catalog(config, handler) as catalog:
        skill = catalog.search("FastAPI auth")[0]
        audits, state, notes = catalog.get_audits(skill)

    assert audits == []
    assert state == "none"
    assert "No external audit" in notes[0]


def test_excessive_audit_findings_are_unknown_not_passed(config):
    def handler(request):
        if request.url.path == "/api/v1/skills/search":
            return httpx.Response(200, json={"data": [listing()]})
        return httpx.Response(
            200,
            json={
                "id": "acme/skills/fastapi-auth",
                "audits": [
                    {"provider": "Fixture", "status": "pass"} for _ in range(101)
                ],
            },
        )

    with mocked_catalog(config, handler) as catalog:
        skill = catalog.search("FastAPI auth")[0]
        audits, state, notes = catalog.get_audits(skill)

    assert audits == []
    assert state == "unavailable"
    assert "supported finding count" in notes[0]


def test_audit_error_envelope_is_unknown_not_empty(config):
    def handler(request):
        if request.url.path == "/api/v1/skills/search":
            return httpx.Response(200, json={"data": [listing()]})
        return httpx.Response(
            200,
            json={
                "id": "acme/skills/fastapi-auth",
                "success": False,
                "audits": [],
            },
        )

    with mocked_catalog(config, handler) as catalog:
        skill = catalog.search("FastAPI auth")[0]
        audits, state, notes = catalog.get_audits(skill)

    assert audits == []
    assert state == "unavailable"
    assert "signaled failure" in notes[0]


def test_transient_failure_retries_and_respects_retry_after(config):
    delays: list[float] = []
    responses = [
        httpx.Response(503, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"data": [listing()]}),
    ]
    requests = 0

    def handler(_request):
        nonlocal requests
        assert _request.url.path == "/api/v1/skills/search"
        requests += 1
        return responses.pop(0)

    with mocked_catalog(config, handler, sleep=delays.append) as catalog:
        found = catalog.search("FastAPI auth")

    assert found
    assert delays == [3.0]
    assert requests == 2


def test_oversized_response_is_rejected(config):
    small_config = replace(config, max_response_bytes=32)
    with mocked_catalog(
        small_config,
        lambda request: (
            httpx.Response(200, content=b"{" + b" " * 80 + b"}")
            if request.url.path == "/api/v1/skills/search"
            else httpx.Response(404)
        ),
    ) as catalog:
        with pytest.raises(CatalogDataError, match="size limit"):
            catalog.search("FastAPI auth")


def test_malformed_catalog_entry_is_rejected(config):
    with mocked_catalog(
        config,
        lambda request: (
            httpx.Response(200, json={"data": [{}]})
            if request.url.path == "/api/v1/skills/search"
            else httpx.Response(404)
        ),
    ) as catalog:
        with pytest.raises(CatalogDataError, match="expected schema"):
            catalog.search("FastAPI auth")

