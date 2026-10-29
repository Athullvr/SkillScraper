from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import Client, ClientSession
from mcp.client.streamable_http import streamable_http_client

from skillscout import mcp_server
from skillscout.remote_context import REMOTE_REQUEST
from skillscout.remote_server import (
    AuthenticatedGateway,
    RemoteConfigurationError,
    RemoteSettings,
    create_app,
    load_remote_settings,
)

ALICE_TOKEN = "alice_" + "a" * 40
BOB_TOKEN = "bob_" + "b" * 40


@asynccontextmanager
async def _connected_client(app, token: str):
    sdk_app = app.app
    async with (
        sdk_app.router.lifespan_context(sdk_app),
        _client_for_started_app(app, token) as result,
    ):
        yield result


@asynccontextmanager
async def _client_for_started_app(app, token: str):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    ) as http_client, streamable_http_client(
        "http://testserver/mcp", http_client=http_client
    ) as (read_stream, write_stream), ClientSession(read_stream, write_stream) as client:
        yield client, http_client


def _settings(**overrides) -> RemoteSettings:
    return RemoteSettings(
        credentials={"alice": ALICE_TOKEN, "bob": BOB_TOKEN},
        **overrides,
    )


def test_remote_tool_registry_preserves_names_schemas_and_descriptions():
    from skillscout.remote_server import remote_server

    async def compare():
        local_tools = await mcp_server.server.list_tools()
        remote_tools = await remote_server.list_tools()
        assert [tool.name for tool in remote_tools] == ["prepare_task", "inspect_skill", "doctor"]
        assert len(local_tools) == len(remote_tools)
        for local, remote in zip(local_tools, remote_tools, strict=True):
            assert remote.name == local.name
            assert remote.input_schema == local.input_schema
            assert remote.output_schema == local.output_schema
            assert remote.description == local.description
            assert remote.annotations == local.annotations

    asyncio.run(compare())


def test_streamable_http_initializes_and_invokes_all_three_tools(monkeypatch):
    def fake_inspect_app(*args, **kwargs):
        raise mcp_server.CatalogCredentialsMissingError("synthetic missing credential")

    monkeypatch.setattr(mcp_server, "inspect_skill_app", fake_inspect_app)

    async def invoke_tools():
        async with _connected_client(create_app(_settings()), ALICE_TOKEN) as (client, _):
            initialization = await client.initialize()
            assert initialization.protocol_version
            listed = await client.list_tools()
            assert [tool.name for tool in listed.tools] == [
                "prepare_task",
                "inspect_skill",
                "doctor",
            ]

            doctor_result = await client.call_tool("doctor", {})
            assert doctor_result.structured_content["status"] == "success"

            task_result = await client.call_tool(
                "prepare_task",
                {"task": "Rename a local variable in a Python helper function."},
            )
            assert task_result.structured_content["decision"] == "NO_SKILL_NEEDED"
            assert task_result.structured_content["project_context"]["facts"] == []
            assert task_result.structured_content["project_context"]["unknowns"] == [
                "Project files are unavailable to the hosted SkillScraper service."
            ]

            inspect_result = await client.call_tool(
                "inspect_skill", {"skill_id": "acme/skills/demo"}
            )
            assert inspect_result.structured_content["status"] == "error"
            assert inspect_result.structured_content["error"]["code"] == (
                "catalog_credentials_missing"
            )

            async with Client(mcp_server.server) as local_client:
                local_doctor = await local_client.call_tool("doctor", {})
                local_task = await local_client.call_tool(
                    "prepare_task",
                    {"task": "Rename a local variable in a Python helper function."},
                )
                local_inspect = await local_client.call_tool(
                    "inspect_skill", {"skill_id": "acme/skills/demo"}
                )
            assert doctor_result.structured_content == local_doctor.structured_content
            assert task_result.structured_content["status"] == local_task.structured_content["status"]
            assert task_result.structured_content["decision"] == local_task.structured_content[
                "decision"
            ]
            assert task_result.structured_content["next_action"] == local_task.structured_content[
                "next_action"
            ]
            assert inspect_result.structured_content == local_inspect.structured_content

    asyncio.run(invoke_tools())


def test_stateless_sessions_keep_user_requests_separate_and_issue_no_session_ids():
    async def check():
        app = create_app(_settings())
        sdk_app = app.app
        response_headers = []

        async def record_response(response):
            response_headers.append(response.headers)

        async with (
            sdk_app.router.lifespan_context(sdk_app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {ALICE_TOKEN}"},
                event_hooks={"response": [record_response]},
            ) as alice_http,
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": f"Bearer {BOB_TOKEN}"},
                event_hooks={"response": [record_response]},
            ) as bob_http,
            streamable_http_client(
                "http://testserver/mcp", http_client=alice_http
            ) as (alice_read, alice_write),
            streamable_http_client(
                "http://testserver/mcp", http_client=bob_http
            ) as (bob_read, bob_write),
            ClientSession(alice_read, alice_write) as alice,
            ClientSession(bob_read, bob_write) as bob,
        ):
            await alice.initialize()
            await bob.initialize()
            alice_task = "Rename a local variable in a Python helper function for Alice."
            bob_task = "Rename a local variable in a Python helper function for Bob."
            alice_result, bob_result = await asyncio.gather(
                alice.call_tool("prepare_task", {"task": alice_task}),
                bob.call_tool("prepare_task", {"task": bob_task}),
            )
            assert alice_result.structured_content["task"] == alice_task
            assert bob_result.structured_content["task"] == bob_task
            assert bob_task not in json.dumps(alice_result.structured_content)
            assert alice_task not in json.dumps(bob_result.structured_content)

        assert response_headers
        assert all("mcp-session-id" not in headers for headers in response_headers)

    asyncio.run(check())


def test_remote_project_paths_are_rejected_without_host_file_access(monkeypatch):
    async def should_not_read(_path):
        raise AssertionError("remote request attempted host project access")

    monkeypatch.setattr(mcp_server, "_project_scope", should_not_read)

    async def invoke():
        async with _connected_client(create_app(_settings()), ALICE_TOKEN) as (client, _):
            await client.initialize()
            result = await client.call_tool(
                "prepare_task",
                {
                    "task": "Implement a database connection pool for a web service.",
                    "project_path": "/etc",
                },
            )
            assert result.structured_content["error"]["code"] == (
                "remote_project_context_unavailable"
            )
            assert "/etc" not in json.dumps(result.structured_content)

    asyncio.run(invoke())


def test_remote_project_scope_uses_nonexistent_placeholder_without_filesystem_checks():
    token = REMOTE_REQUEST.set(True)
    try:
        assert mcp_server._project_scope(None).as_posix() == (
            "/__skillscraper_remote_no_project__"
        )
    finally:
        REMOTE_REQUEST.reset(token)


def test_remote_prepare_task_without_catalog_credentials_makes_no_http_request(
    monkeypatch,
):
    import httpx

    monkeypatch.delenv("SKILLSCOUT_CATALOG_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_OIDC_TOKEN", raising=False)
    calls: list[str] = []

    def request_spy(self, method, url, *args, **kwargs):
        calls.append(f"{method} {url}")
        raise AssertionError("catalog HTTP request must not be sent without credentials")

    monkeypatch.setattr(httpx.Client, "request", request_spy)

    async def invoke():
        async with _connected_client(create_app(_settings()), ALICE_TOKEN) as (client, _):
            await client.initialize()
            result = await client.call_tool(
                "prepare_task",
                {"task": "Implement a FastAPI multi-tenant authorization middleware."},
            )
            assert result.structured_content["status"] == "error"
            assert result.structured_content["error"]["code"] == "catalog_credentials_missing"

    asyncio.run(invoke())
    assert calls == []


def test_health_and_readiness_are_public_minimal_and_do_not_echo_configuration():
    async def check():
        app = create_app(_settings())
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            health = await client.get("/healthz")
            ready = await client.get("/readyz")
            assert health.status_code == ready.status_code == 200
            assert health.json() == {"status": "ok"}
            assert ready.json() == {"status": "ready"}
            assert ALICE_TOKEN not in health.text + ready.text

    asyncio.run(check())


def test_origin_allowlist_blocks_browser_origins_and_host_mismatch():
    async def echo(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    async def check():
        gateway = AuthenticatedGateway(
            echo,
            RemoteSettings(
                credentials={"alice": ALICE_TOKEN},
                allowed_origins=("https://mcp.example.test",),
            ),
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=gateway), base_url="https://mcp.example.test"
        ) as client:
            headers = {"Authorization": f"Bearer {ALICE_TOKEN}"}
            assert (await client.post("/mcp", headers=headers)).status_code == 200
            assert (
                await client.post(
                    "/mcp", headers={**headers, "Origin": "https://attacker.example"}
                )
            ).status_code == 403
            assert (
                await client.post(
                    "/mcp", headers={**headers, "Origin": "https://mcp.example.test"}
                )
            ).status_code == 200
            assert (
                await client.post(
                    "/mcp",
                    headers={
                        **headers,
                        "Origin": "https://mcp.example.test",
                        "Host": "attacker.example",
                    },
                )
            ).status_code == 421

    asyncio.run(check())


def test_missing_invalid_and_revoked_tokens_have_the_same_safe_error():
    async def check():
        app = create_app(_settings())
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            missing = await client.post("/mcp", content="{}")
            invalid = await client.post(
                "/mcp",
                content="{}",
                headers={"Authorization": "Bearer " + "z" * 40},
            )
            revoked_app = create_app(RemoteSettings(credentials={"alice": "c" * 40}))
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=revoked_app),
                base_url="http://testserver",
            ) as revoked_client:
                revoked = await revoked_client.post(
                    "/mcp",
                    content="{}",
                    headers={"Authorization": f"Bearer {ALICE_TOKEN}"},
                )
            assert missing.status_code == invalid.status_code == revoked.status_code == 401
            assert missing.text == invalid.text == revoked.text
            assert ALICE_TOKEN not in missing.text
            assert "valid" not in missing.text.lower()

    asyncio.run(check())


def test_rate_limits_are_per_user_and_revoke_on_settings_replacement():
    async def echo(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    async def check():
        gateway = AuthenticatedGateway(
            echo,
            RemoteSettings(
                credentials={"alice": ALICE_TOKEN, "bob": BOB_TOKEN},
                requests_per_minute=1,
            ),
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=gateway), base_url="http://testserver"
        ) as client:
            alice_headers = {"Authorization": f"Bearer {ALICE_TOKEN}"}
            bob_headers = {"Authorization": f"Bearer {BOB_TOKEN}"}
            assert (await client.post("/mcp", headers=alice_headers)).status_code == 200
            assert (await client.post("/mcp", headers=alice_headers)).status_code == 429
            assert (await client.post("/mcp", headers=bob_headers)).status_code == 200

            rotated = AuthenticatedGateway(
                echo, RemoteSettings(credentials={"alice": "c" * 40})
            )
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=rotated), base_url="http://testserver"
            ) as rotated_client:
                assert (
                    await rotated_client.post("/mcp", headers=alice_headers)
                ).status_code == 401
                assert (
                    await rotated_client.post(
                        "/mcp", headers={"Authorization": "Bearer " + "c" * 40}
                    )
                ).status_code == 200

    asyncio.run(check())


def test_malformed_unsupported_and_oversized_requests_are_bounded():
    async def check():
        app = create_app(_settings())
        async with app.app.router.lifespan_context(app.app), httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {ALICE_TOKEN}"},
        ) as client:
            malformed = await client.post("/mcp", content="{")
            unsupported = await client.put("/mcp", content="{}")
            oversized = await client.post("/mcp", content=" " * (512 * 1024 + 1))
            assert malformed.status_code in {400, 422}
            assert unsupported.status_code == 405
            assert oversized.status_code in {413, 400}
            assert ALICE_TOKEN not in malformed.text + unsupported.text + oversized.text

    asyncio.run(check())


def test_gateway_timeout_disconnect_and_concurrency_limits():
    async def check():
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_app(scope, receive, send):
            started.set()
            await release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        gateway = AuthenticatedGateway(
            slow_app,
            RemoteSettings(
                credentials={"alice": ALICE_TOKEN},
                max_concurrent_requests=1,
                per_user_concurrent_requests=1,
                request_timeout_seconds=2,
            ),
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=gateway), base_url="http://testserver"
        ) as client:
            headers = {"Authorization": f"Bearer {ALICE_TOKEN}"}
            first = asyncio.create_task(client.post("/mcp", headers=headers))
            await started.wait()
            second = await client.post("/mcp", headers=headers)
            assert second.status_code == 503
            release.set()
            assert (await first).status_code == 200

        timeout_release = asyncio.Event()

        async def timeout_app(scope, receive, send):
            await timeout_release.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        timeout_gateway = AuthenticatedGateway(
            timeout_app,
            RemoteSettings(
                credentials={"alice": ALICE_TOKEN}, request_timeout_seconds=0.01
            ),
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=timeout_gateway), base_url="http://testserver"
        ) as client:
            timed_out = await client.post(
                "/mcp", headers={"Authorization": f"Bearer {ALICE_TOKEN}"}
            )
            assert timed_out.status_code == 504

        disconnect_started = asyncio.Event()

        async def disconnect_app(scope, receive, send):
            disconnect_started.set()
            await asyncio.Event().wait()

        disconnect_gateway = AuthenticatedGateway(
            disconnect_app,
            RemoteSettings(credentials={"alice": ALICE_TOKEN}, request_timeout_seconds=2),
        )
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"authorization", f"Bearer {ALICE_TOKEN}".encode())],
            "query_string": b"",
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }

        async def receive():
            await asyncio.Event().wait()

        async def send(_message):
            return None

        request = asyncio.create_task(disconnect_gateway(scope, receive, send))
        await disconnect_started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert disconnect_gateway._global_slots._value == 16

    asyncio.run(check())


def test_sensitive_headers_prompts_and_exceptions_are_not_logged_or_returned(caplog):
    secret = "private-bearer-token-" + "q" * 40
    prompt = "private-task-text-" + "w" * 40

    async def raising_app(scope, receive, send):
        raise RuntimeError(f"{secret} {prompt}")

    async def check():
        gateway = AuthenticatedGateway(
            raising_app, RemoteSettings(credentials={"alice": ALICE_TOKEN})
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=gateway), base_url="http://testserver"
        ) as client:
            bad_auth = await client.post(
                "/mcp", headers={"Authorization": f"Bearer {secret}"}, content=prompt
            )
            assert bad_auth.status_code == 401
            failed = await client.post(
                "/mcp", headers={"Authorization": f"Bearer {ALICE_TOKEN}"}, content=prompt
            )
            assert failed.status_code == 500
            assert secret not in failed.text and prompt not in failed.text

    with caplog.at_level(logging.DEBUG):
        asyncio.run(check())
    assert secret not in caplog.text
    assert prompt not in caplog.text


def test_remote_configuration_validation_is_fail_closed():
    with pytest.raises(RemoteConfigurationError, match="SKILLSCRAPER_MCP_USERS_JSON is required"):
        load_remote_settings({})

    malformed_values = [
        "[]",
        "{\"alice\":\"short\"}",
        "{\"alice\":\"a\"}",
        "{\"alice\":\"a\",\"alice\":\"b\"}",
        "{\"alice\":\"a\",\"bob\":\"a\"}",
    ]
    for value in malformed_values:
        with pytest.raises(RemoteConfigurationError):
            load_remote_settings({"SKILLSCRAPER_MCP_USERS_JSON": value})

    users_json = json.dumps({"alice": ALICE_TOKEN})
    with pytest.raises(RemoteConfigurationError, match="SKILLSCRAPER_MCP_ALLOWED_ORIGINS"):
        load_remote_settings({"SKILLSCRAPER_MCP_USERS_JSON": users_json})
    with pytest.raises(RemoteConfigurationError, match="HTTPS origins"):
        load_remote_settings(
            {
                "SKILLSCRAPER_MCP_USERS_JSON": users_json,
                "SKILLSCRAPER_MCP_ALLOWED_ORIGINS": '["http://mcp.example.test"]',
            }
        )

    valid = load_remote_settings(
        {
            "SKILLSCRAPER_MCP_USERS_JSON": users_json,
            "SKILLSCRAPER_MCP_ALLOWED_ORIGINS": '["https://mcp.example.test"]',
            "SKILLSCRAPER_MCP_MAX_CONCURRENT_REQUESTS": "4",
            "SKILLSCRAPER_MCP_PER_USER_CONCURRENT_REQUESTS": "2",
            "SKILLSCRAPER_MCP_REQUESTS_PER_MINUTE": "10",
            "SKILLSCRAPER_MCP_REQUEST_TIMEOUT_SECONDS": "30",
        }
    )
    assert valid.max_concurrent_requests == 4
    assert valid.per_user_concurrent_requests == 2
    assert valid.requests_per_minute == 10
    assert valid.request_timeout_seconds == 30
    assert valid.allowed_origins == ("https://mcp.example.test",)


def test_remote_entrypoint_port_validation(monkeypatch):
    from skillscout.remote_entrypoint import _port_from_environment

    monkeypatch.setenv("PORT", "10000")
    assert _port_from_environment() == 10_000
    for value in ("zero", "0", "65536"):
        monkeypatch.setenv("PORT", value)
        with pytest.raises(ValueError, match="PORT"):
            _port_from_environment()


def test_remote_startup_fails_before_listening_when_required_config_is_missing(
    monkeypatch, caplog
):
    from skillscout.remote_entrypoint import main

    monkeypatch.delenv("SKILLSCRAPER_MCP_USERS_JSON", raising=False)
    monkeypatch.delenv("SKILLSCRAPER_MCP_ALLOWED_ORIGINS", raising=False)
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2
    assert "SKILLSCRAPER_MCP_USERS_JSON is required" in caplog.text
    assert ALICE_TOKEN not in caplog.text


def test_remote_startup_rejects_invalid_catalog_configuration_without_network(
    monkeypatch, caplog
):
    from skillscout.remote_entrypoint import main

    monkeypatch.setenv("SKILLSCRAPER_MCP_USERS_JSON", json.dumps({"alice": ALICE_TOKEN}))
    monkeypatch.setenv("SKILLSCRAPER_MCP_ALLOWED_ORIGINS", '["https://mcp.example.test"]')
    monkeypatch.setenv("SKILLSCOUT_CATALOG_URL", "http://invalid.example")
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2
    assert "SKILLSCOUT_CATALOG_URL" in caplog.text
    assert "http://invalid.example" not in caplog.text
