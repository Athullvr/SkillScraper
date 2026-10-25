#!/usr/bin/env python3
"""Exercise an installed SkillScout MCP server over the stdio protocol.

Run from a project environment that has the MCP SDK installed and pass a
separate virtual environment containing the built SkillScout wheel:

    python scripts/mcp_stdio_smoke.py --venv /tmp/skillscout-clean-env

The test uses only local tasks and a fixture catalog. It does not need catalog
credentials or make catalog requests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TOOL_NAMES = {"prepare_task", "inspect_skill", "doctor"}
SECRET_MARKER = "skillscout-smoke-secret-marker"


def _structured(result: Any) -> dict[str, Any]:
    value = getattr(result, "structured_content", None)
    if value is None:
        value = getattr(result, "structuredContent", None)
    if value is None and hasattr(result, "model_dump"):
        value = result.model_dump(by_alias=True).get("structuredContent")
    if not isinstance(value, dict):
        raise RuntimeError("MCP tool result did not include structured content")
    return value


def _safe_environment(venv: Path, *, catalog_token: str | None = None) -> dict[str, str]:
    scripts = venv / ("Scripts" if os.name == "nt" else "bin")
    path = os.pathsep.join((str(scripts), os.defpath))
    environment = {
        "PATH": path,
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    if catalog_token is not None:
        environment["SKILLSCOUT_CATALOG_TOKEN"] = catalog_token
    return environment


def _codex_hook_check(command: Path, cwd: Path, environment: dict[str, str]) -> dict[str, Any]:
    """Exercise the installed Codex hook using a documented event fixture."""
    event = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "Rename a local variable in a Python helper function.",
        "cwd": str(cwd),
        "session_id": "synthetic-session",
    }
    hook_environment = dict(environment)
    # A routine task must not parse catalog configuration or contact transport.
    hook_environment["SKILLSCOUT_CANDIDATE_LIMIT"] = "invalid"
    completed = subprocess.run(
        [str(command)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        cwd=cwd,
        env=hook_environment,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"installed Codex hook exited with status {completed.returncode}")
    if completed.stderr:
        raise RuntimeError("installed Codex hook wrote unexpected diagnostics to stderr")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("installed Codex hook did not emit valid JSON") from exc
    output = response.get("hookSpecificOutput")
    if not isinstance(output, dict) or output.get("hookEventName") != "UserPromptSubmit":
        raise RuntimeError("installed Codex hook returned an invalid hook response")
    context = output.get("additionalContext")
    if not isinstance(context, str) or "NO_SKILL_NEEDED" not in context:
        raise RuntimeError("routine Codex hook event did not return NO_SKILL_NEEDED")
    if SECRET_MARKER in completed.stdout:
        raise RuntimeError("installed Codex hook echoed a secret marker")
    return {"routine_decision": "NO_SKILL_NEEDED", "stderr_bytes": 0, "config_read": False}


def _tool_schema(tools: list[Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for tool in tools:
        schema = getattr(tool, "input_schema", None)
        if schema is None:
            schema = getattr(tool, "inputSchema", None)
        result[tool.name] = schema if isinstance(schema, dict) else {}
    return result


async def _sdk_session(
    command: str,
    args: list[str],
    cwd: Path,
    environment: dict[str, str],
    stderr_path: Path,
    *,
    fixture_mode: bool,
) -> dict[str, Any]:
    parameters = StdioServerParameters(
        command=command,
        args=args,
        cwd=cwd,
        env=environment,
    )
    observations: dict[str, Any] = {}
    with stderr_path.open("w+", encoding="utf-8") as errlog:
        async with stdio_client(parameters, errlog=errlog) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await asyncio.wait_for(session.initialize(), timeout=10)
                listed = await asyncio.wait_for(session.list_tools(), timeout=10)
                schemas = _tool_schema(listed.tools)
                if set(schemas) != TOOL_NAMES:
                    raise RuntimeError(f"unexpected MCP tools: {sorted(schemas)}")
                if set(schemas["prepare_task"].get("properties", {})) != {
                    "task",
                    "project_path",
                    "agent",
                    "context_budget_bytes",
                }:
                    raise RuntimeError("prepare_task input schema changed")
                if set(schemas["inspect_skill"].get("properties", {})) != {
                    "skill_id",
                    "project_path",
                }:
                    raise RuntimeError("inspect_skill input schema changed")
                if schemas["prepare_task"].get("required") != ["task"]:
                    raise RuntimeError("prepare_task schema does not require task")
                if schemas["inspect_skill"].get("required") != ["skill_id"]:
                    raise RuntimeError("inspect_skill schema does not require skill_id")
                if schemas["doctor"].get("properties", {}) != {}:
                    raise RuntimeError("doctor should not require input properties")

                doctor = _structured(
                    await asyncio.wait_for(session.call_tool("doctor", {}), timeout=10)
                )
                if doctor.get("status") != "success":
                    raise RuntimeError("doctor tool did not succeed")
                if fixture_mode and doctor.get("catalog_auth_configured") is not True:
                    raise RuntimeError("doctor did not report fixture credential presence")

                if fixture_mode:
                    prepared = _structured(
                        await asyncio.wait_for(
                            session.call_tool(
                                "prepare_task",
                                {
                                    "task": "Implement OAuth authentication for FastAPI",
                                    "project_path": str(cwd),
                                },
                            ),
                            timeout=10,
                        )
                    )
                    inspected = _structured(
                        await asyncio.wait_for(
                            session.call_tool(
                                "inspect_skill",
                                {"skill_id": "fixture/fastapi-oauth", "project_path": str(cwd)},
                            ),
                            timeout=10,
                        )
                    )
                    sanitized_failure = _structured(
                        await asyncio.wait_for(
                            session.call_tool(
                                "inspect_skill", {"skill_id": "fixture/secret-error"}
                            ),
                            timeout=10,
                        )
                    )
                    missing_credential = _structured(
                        await asyncio.wait_for(
                            session.call_tool(
                                "inspect_skill", {"skill_id": "fixture/missing-creds"}
                            ),
                            timeout=10,
                        )
                    )
                    if prepared.get("status") != "success" or prepared.get("decision") != "USE":
                        raise RuntimeError("fixture prepare_task call did not return USE")
                    if inspected.get("status") != "success" or inspected.get("decision") != "REVIEW":
                        raise RuntimeError("fixture inspect_skill call did not return REVIEW")
                    if (
                        sanitized_failure.get("status") != "error"
                        or sanitized_failure.get("error", {}).get("code") != "catalog_data_error"
                    ):
                        raise RuntimeError("fixture inspection failure was not safely classified")
                    if (
                        missing_credential.get("status") != "error"
                        or missing_credential.get("error", {}).get("code")
                        != "catalog_credentials_missing"
                    ):
                        raise RuntimeError("fixture missing-credential failure was not classified")
                    missing_message = missing_credential.get("error", {}).get("message", "")
                    if not all(
                        name in missing_message
                        for name in ("VERCEL_OIDC_TOKEN", "SKILLSCOUT_CATALOG_TOKEN")
                    ):
                        raise RuntimeError("missing-credential error did not name its corrective action")
                    if (
                        missing_credential.get("decision") is not None
                        or "no catalog request was sent"
                        not in missing_credential.get("next_action", "")
                    ):
                        raise RuntimeError("missing-credential result did not preserve uncertainty")
                    if SECRET_MARKER in json.dumps(
                        [prepared, inspected, sanitized_failure, missing_credential, doctor]
                    ):
                        raise RuntimeError("a fixture-backed tool result echoed a configured secret marker")
                    doctor_after_failure = _structured(
                        await asyncio.wait_for(session.call_tool("doctor", {}), timeout=10)
                    )
                    if doctor_after_failure.get("status") != "success":
                        raise RuntimeError("server did not continue after a fixture catalog failure")
                    observations.update(
                        {
                            "fixture_prepare_decision": prepared["decision"],
                            "fixture_inspection_decision": inspected["decision"],
                            "sanitized_failure": sanitized_failure["error"]["code"],
                            "missing_credential_error": missing_credential["error"]["code"],
                            "missing_credential_next_step": "configure an authorized catalog credential",
                            "doctor_after_fixture_error": doctor_after_failure["status"],
                        }
                    )
                else:
                    routine = _structured(
                        await asyncio.wait_for(
                            session.call_tool(
                                "prepare_task",
                                {"task": "Fix a typo in the README", "project_path": str(cwd)},
                            ),
                            timeout=10,
                        )
                    )
                    invalid = _structured(
                        await asyncio.wait_for(
                            session.call_tool("prepare_task", {"task": ""}), timeout=10
                        )
                    )
                    doctor_after_error = _structured(
                        await asyncio.wait_for(session.call_tool("doctor", {}), timeout=10)
                    )
                    if routine.get("status") != "success" or routine.get("decision") != "NO_SKILL_NEEDED":
                        raise RuntimeError("routine prepare_task call did not return NO_SKILL_NEEDED")
                    if invalid.get("status") != "error" or invalid.get("error", {}).get("code") != "invalid_input":
                        raise RuntimeError("invalid prepare_task input was not returned as a structured error")
                    if doctor_after_error.get("status") != "success":
                        raise RuntimeError("server did not continue after structured tool errors")
                    observations.update(
                        {
                            "routine_decision": routine["decision"],
                            "invalid_input_error": invalid["error"]["code"],
                            "doctor_after_errors": doctor_after_error["status"],
                        }
                    )

                serialized = json.dumps(observations | {"doctor": doctor, "schemas": schemas})
                if SECRET_MARKER in serialized:
                    raise RuntimeError("a tool result echoed a configured secret marker")
                observations.update(
                    {
                        "protocol_version": getattr(
                            initialized, "protocol_version", None
                        )
                        or getattr(initialized, "protocolVersion", None),
                        "tools": sorted(schemas),
                        "doctor_status": doctor["status"],
                    }
                )

    stderr_text = stderr_path.read_text(encoding="utf-8")
    if SECRET_MARKER in stderr_text:
        raise RuntimeError("server diagnostics echoed a configured secret marker")
    if not fixture_mode and "HTTP Request:" in stderr_text:
        raise RuntimeError("the installed-server smoke unexpectedly invoked HTTP transport")
    observations["stderr_bytes"] = len(stderr_text.encode("utf-8"))
    if stderr_text:
        observations["stderr_preview"] = stderr_text[-1_000:]
    return observations


def _fixture_server_code() -> str:
    return "\n".join(
        [
            "from datetime import datetime, timezone",
            "from skillscout import application",
            "from skillscout.catalog import CatalogCredentialsMissingError, CatalogDataError",
            "from skillscout.models import AuditFinding, CatalogSkill, SkillDetail, SkillFile",
            "skill = CatalogSkill(id='fixture/fastapi-oauth', name='FastAPI OAuth',",
            "    source='fixture', slug='fastapi-oauth',",
            "    description='Implement OAuth authentication for FastAPI and validate token claims.')",
            "detail = SkillDetail(id=skill.id, source=skill.source, slug=skill.slug,",
            "    hash='stdio-fixture-revision', files=[SkillFile(path='SKILL.md', contents=",
            "    '# FastAPI OAuth\\n\\nConfigure OAuth authentication in FastAPI and validate token claims.')])",
            "class SearchResults(list):",
            "    complete = True",
            "class FixtureCatalog:",
            "    def __init__(self, config): pass",
            "    def __enter__(self): return self",
            "    def __exit__(self, *args): return None",
            "    def search(self, query, *, limit): return SearchResults([skill])",
            "    def get_detail(self, candidate):",
            "        if candidate.id == 'fixture/missing-creds':",
            "            raise CatalogCredentialsMissingError('fixture has no credential')",
            "        if candidate.id == 'fixture/secret-error':",
            "            raise CatalogDataError('fixture response echoed ' + __import__('os').environ['SKILLSCOUT_CATALOG_TOKEN'])",
            "        return detail",
            "    def get_audits(self, candidate):",
            "        return ([AuditFinding(provider='smoke-fixture', status='pass',",
            "            auditedAt=datetime.now(timezone.utc), riskLevel='low',",
            "            revision=detail.content_hash)], 'available', [])",
            "application.CatalogClient = FixtureCatalog",
            "from skillscout.mcp_server import run_stdio",
            "run_stdio()",
        ]
    )


async def _raw_protocol_check(
    python: Path, cwd: Path, environment: dict[str, str], timeout: float
) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        str(python),
        "-m",
        "skillscout",
        "mcp",
        cwd=cwd,
        env=environment,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin and process.stdout and process.stderr

    async def send(message: str) -> None:
        process.stdin.write(message.encode("utf-8") + b"\n")
        await process.stdin.drain()

    async def receive() -> dict[str, Any]:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
        if not line:
            raise RuntimeError("MCP server closed stdout before replying")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError("MCP stdout returned a non-object JSON-RPC value")
        return value

    responses: list[dict[str, Any]] = []
    try:
        await send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "skillscout-smoke", "version": "1"},
                    },
                }
            )
        )
        initialized = await receive()
        if initialized.get("id") != 1 or "result" not in initialized:
            raise RuntimeError("MCP initialize did not return a JSON-RPC result")
        await send(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

        await send("{malformed-json")
        await send(
            json.dumps(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            )
        )
        while not any(item.get("id") == 2 for item in responses):
            responses.append(await receive())
            if len(responses) > 4:
                raise RuntimeError("malformed input prevented recovery to the next request")
        tool_response = next(item for item in responses if item.get("id") == 2)
        if {tool["name"] for tool in tool_response.get("result", {}).get("tools", [])} != TOOL_NAMES:
            raise RuntimeError("tools/list failed after a malformed JSON-RPC line")

        await send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "doctor", "arguments": {}},
                }
            )
        )
        doctor = await receive()
        if doctor.get("id") != 3 or "result" not in doctor:
            raise RuntimeError("server did not process a request after malformed input")

        # MCP stdio sessions shut down by closing the input stream. There is no
        # JSON-RPC shutdown request in the MCP lifecycle protocol.
        process.stdin.close()
        await process.stdin.wait_closed()
        await asyncio.wait_for(process.wait(), timeout=timeout)
        if process.returncode != 0:
            raise RuntimeError(f"MCP server exited with status {process.returncode}")
        stderr = await process.stderr.read()
        if SECRET_MARKER.encode() in stderr:
            raise RuntimeError("server diagnostics echoed a configured secret marker")
        return {
            "malformed_json_recovery": "pass",
            "sequential_raw_requests": 3,
            "graceful_eof_shutdown": "pass",
            "process_exit_code": process.returncode,
            "stderr_bytes": len(stderr),
        }
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                process.kill()
                await process.wait()


async def _run(venv: Path, timeout: float) -> dict[str, Any]:
    scripts = venv / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    server = scripts / ("skillscout-mcp.exe" if os.name == "nt" else "skillscout-mcp")
    cli = scripts / ("skillscout.exe" if os.name == "nt" else "skillscout")
    hook = scripts / ("skillscout-codex-hook.exe" if os.name == "nt" else "skillscout-codex-hook")
    if not python.is_file() or not server.is_file() or not cli.is_file() or not hook.is_file():
        raise RuntimeError("the supplied environment is missing installed SkillScout entry points")

    with tempfile.TemporaryDirectory(prefix="skillscout-mcp-smoke-") as directory:
        cwd = Path(directory)
        (cwd / "pyproject.toml").write_text(
            '[project]\nname="stdio-smoke-project"\ndependencies=["fastapi>=0.100"]\n',
            encoding="utf-8",
        )
        clean_env = _safe_environment(venv)
        fixture_env = _safe_environment(venv, catalog_token=SECRET_MARKER)
        normal = await asyncio.wait_for(
            _sdk_session(
                str(server), [], cwd, clean_env, cwd / "normal.stderr", fixture_mode=False
            ),
            timeout=timeout,
        )
        fixture = await asyncio.wait_for(
            _sdk_session(
                str(python),
                ["-c", _fixture_server_code()],
                cwd,
                fixture_env,
                cwd / "fixture.stderr",
                fixture_mode=True,
            ),
            timeout=timeout,
        )
        raw = await asyncio.wait_for(
            _raw_protocol_check(python, cwd, clean_env, timeout=min(timeout, 10)),
            timeout=timeout,
        )
        hook_result = _codex_hook_check(hook, cwd, clean_env)
    return {
        "installed_entrypoint": normal,
        "fixture_backed_tools": fixture,
        "raw_protocol": raw,
        "codex_hook_fixture": hook_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", required=True, type=Path, help="fresh environment containing the wheel")
    parser.add_argument("--timeout", type=float, default=20, help="per-process timeout in seconds")
    args = parser.parse_args()
    try:
        report = asyncio.run(_run(args.venv.resolve(), args.timeout))
    except Exception as exc:  # noqa: BLE001 - print bounded smoke failures only.
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"status": "passed", **report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
