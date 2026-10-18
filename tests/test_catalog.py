from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
import respx

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


def test_search_validates_and_preserves_catalog_provenance(config):
    with respx.mock(base_url=BASE) as router:
        router.get("/api/v1/skills/search").mock(
            return_value=httpx.Response(200, json={"data": [listing()]})
        )
        with CatalogClient(config) as catalog:
            found = catalog.search("FastAPI authentication", limit=4)

    assert found[0].id == "acme/skills/fastapi-auth"
    assert str(found[0].source_url) == "https://skills.sh/acme/skills/fastapi-auth"
    assert str(found[0].install_url) == "https://github.com/acme/skills"
    assert found[0].retrieved_at.tzinfo is not None


def test_detail_and_audit_revision_are_validated_and_bound(config):
    with respx.mock(base_url=BASE) as router:
        router.get("/api/v1/skills/search").mock(
            return_value=httpx.Response(200, json={"data": [listing()]})
        )
        router.get("/api/v1/skills/acme/skills/fastapi-auth").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "acme/skills/fastapi-auth",
                    "source": "acme/skills",
                    "slug": "fastapi-auth",
                    "hash": "revision-2",
                    "files": [{"path": "SKILL.md", "contents": "---\nname: FastAPI Auth\n---"}],
                },
            )
        )
        router.get("/api/v1/skills/audit/acme/skills/fastapi-auth").mock(
            return_value=httpx.Response(
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
        )
        with CatalogClient(config) as catalog:
            skill = catalog.search("FastAPI auth")[0]
            detail = catalog.get_detail(skill)
            audits, state, notes = catalog.get_audits(skill)

    assert detail.content_hash == "revision-2"
    assert detail.files[0].path == "SKILL.md"
    assert state == "available"
    assert audits[0].revision == "revision-2"
    assert notes == []


def test_audit_authentication_failure_is_unknown_not_a_search_failure(config):
    with respx.mock(base_url=BASE) as router:
        router.get("/api/v1/skills/search").mock(
            return_value=httpx.Response(200, json={"data": [listing()]})
        )
        router.get("/api/v1/skills/audit/acme/skills/fastapi-auth").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        with CatalogClient(config) as catalog:
            skill = catalog.search("FastAPI auth")[0]
            audits, state, notes = catalog.get_audits(skill)

    assert audits == []
    assert state == "unavailable"
    assert "requires credentials" in notes[0]


def test_transient_failure_retries_and_respects_retry_after(config):
    delays: list[float] = []
    with respx.mock(base_url=BASE) as router:
        route = router.get("/api/v1/skills/search")
        route.side_effect = [
            httpx.Response(503, headers={"Retry-After": "3"}),
            httpx.Response(200, json={"data": [listing()]}),
        ]
        with CatalogClient(config, sleep=delays.append, random_value=lambda: 0.0) as catalog:
            found = catalog.search("FastAPI auth")

    assert found
    assert delays == [3.0]
    assert route.call_count == 2


def test_oversized_response_is_rejected(config):
    small_config = replace(config, max_response_bytes=32)
    with respx.mock(base_url=BASE) as router:
        router.get("/api/v1/skills/search").mock(
            return_value=httpx.Response(200, content=b"{" + b" " * 80 + b"}")
        )
        with CatalogClient(small_config) as catalog:
            with pytest.raises(CatalogDataError, match="size limit"):
                catalog.search("FastAPI auth")


def test_malformed_catalog_entry_is_rejected(config):
    with respx.mock(base_url=BASE) as router:
        router.get("/api/v1/skills/search").mock(return_value=httpx.Response(200, json={"data": [{}]}))
        with CatalogClient(config) as catalog:
            with pytest.raises(CatalogDataError, match="expected schema"):
                catalog.search("FastAPI auth")

