# Hosted MCP service

SkillScraper can serve its existing `prepare_task`, `inspect_skill`, and `doctor` tools over MCP Streamable HTTP. Local stdio remains available through `skillscout-mcp`. The hosted path keeps the same tool names, input schemas, output schemas, and descriptions. Its endpoint is `/mcp`.

The implementation uses the installed MCP Python SDK 2.3.0 `MCPServer.streamable_http_app()` API. The app runs in stateless HTTP mode and returns the protocol's JSON response form. The service retains no MCP session identifiers or task history. Tool arguments and task evidence remain in memory for the duration of a request. Remote calls cannot inspect files: omit `project_path`; if supplied, the tool returns a structured `remote_project_context_unavailable` error. Local stdio behavior is unchanged.

The protocol permits JSON responses for Streamable HTTP requests and defines stdio and Streamable HTTP as standard transports. See the [MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) and the [MCP Python SDK 2.3.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0).

## Deployment target

The repository had no Vercel configuration when this work began. Vercel Functions can run Python and handle HTTP streaming, so Streamable HTTP alone does not rule Vercel out. This implementation uses a single Render Docker web service because it gives the service an explicit ASGI process lifetime, one process-local rate-limit table, a managed HTTPS endpoint, health checks, and a rollback path. MCP state is stateless, so the service does not depend on sticky routing or a database. Render's web service and health-check behavior are described in its [official docs](https://render.com/docs/web-services) and [health-check guide](https://render.com/docs/health-checks). Vercel remains a viable future host if an owner prefers it and supplies a reviewed deployment configuration.

`Dockerfile` and `render.yaml` are reproducible deployment artifacts. The Render blueprint selects one `starter` instance, disables automatic deploys, and uses `/readyz` as the health check. Creating or running this service can incur hosting charges. Do not launch it until the owner approves the plan and service creation. The repository implementation was not deployed during this work.

## Required runtime configuration

Configure secrets in Render's Environment settings or another approved runtime secret manager. Do not put secret values in `render.yaml`, the Docker image, client configuration, prompts, or logs.

| Variable | Required | Meaning |
| --- | --- | --- |
| `SKILLSCRAPER_MCP_USERS_JSON` | Yes | JSON object mapping each pilot user ID to a unique bearer token of at least 32 URL-safe characters. Maximum 32 users. |
| `SKILLSCRAPER_MCP_ALLOWED_ORIGINS` | Yes | JSON array of one to ten exact HTTPS origins, for example `["https://skillscraper-mcp.onrender.com"]`. No paths or wildcards. |
| `SKILLSCRAPER_MCP_MAX_CONCURRENT_REQUESTS` | No | Global in-flight MCP request limit; default 16, range 1–64. |
| `SKILLSCRAPER_MCP_PER_USER_CONCURRENT_REQUESTS` | No | Per-user in-flight request limit; default 2, range 1–8. |
| `SKILLSCRAPER_MCP_REQUESTS_PER_MINUTE` | No | Per-user rolling request limit; default 60, range 1–600. |
| `SKILLSCRAPER_MCP_REQUEST_TIMEOUT_SECONDS` | No | Per-request deadline; default 125 seconds, range 1–180. |
| `PORT` | Platform | HTTP listener port; defaults to 10000 for local container runs. |

The service refuses startup if the user token map, allowed-origin list, or any supplied setting is invalid. It accepts native Codex and Claude Code requests without an `Origin` header. If a client sends `Origin`, the origin must exactly match the configured HTTPS allowlist and the `Host` header must match an allowlisted host. Invalid origins receive 403; a mismatched host receives 421.

The SDK caps each MCP request body at 512 KiB. Uvicorn uses one worker, disables access logs, limits connections and graceful-shutdown time, and the tool layer bounds synchronous catalog work to eight workers. Authenticated requests are rate-limited per user. The app exposes only the MCP endpoint plus `/healthz` and `/readyz`; it has no shell, skill-install, or arbitrary-file endpoint.

### Catalog configuration

Catalog configuration keeps the existing environment-variable names and credential precedence:

| Variable | Meaning |
| --- | --- |
| `SKILLSCOUT_CATALOG_TOKEN` | Preferred server-side catalog bearer credential. |
| `VERCEL_OIDC_TOKEN` | Existing fallback catalog credential when `SKILLSCOUT_CATALOG_TOKEN` is empty or unset. |
| `SKILLSCOUT_CATALOG_URL` | Optional HTTPS catalog base URL; defaults to `https://skills.sh`. |
| `SKILLSCOUT_CANDIDATE_LIMIT` | Optional candidate bound; defaults to 3 and remains capped at 5. |
| `SKILLSCOUT_REQUEST_TIMEOUT_SECONDS` | Existing catalog/LLM total request timeout setting; defaults to 30 seconds and is bounded by the config loader. |
| `SKILLSCOUT_LLM_API_KEY` | Optional LLM credential. MCP calls explicitly keep the LLM path disabled. |
| `SKILLSCOUT_LLM_BASE_URL` | Optional HTTPS LLM endpoint; defaults to `https://api.openai.com/v1`. |
| `SKILLSCOUT_LLM_MODEL` | Optional model label; defaults to `gpt-4o-mini`. |
| `SKILLSCOUT_LLM_SEND_TASK` | Existing opt-in setting for task transmission to the LLM path; remote MCP tool calls do not use that path. |

The deployment does not require a catalog credential to start. Routine tasks can receive the existing deterministic `NO_SKILL_NEEDED` result without a catalog call. Catalog-backed requests with no configured credential return `catalog_credentials_missing`; the service does not turn that error into an empty search or a recommendation. A catalog credential supplied in a task prompt is never used as configuration. Existing task-query redaction still applies before catalog search. No authorized live catalog test was performed.

## Provisioning, rotation, and revocation

Create one independent token for each pilot user. A local generator can produce a suitable random token without placing it in command history:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Store the generated value in a password manager, then enter it only in the host's secret manager. Set `SKILLSCRAPER_MCP_USERS_JSON` there, for example as `{"friend-a":"<friend-a-token>","friend-b":"<friend-b-token>"}`. Do not save this example with real values. Give each friend only their own token over a private channel.

To rotate a token, generate a replacement, update that user's entry in the host secret store, and deploy the environment change. Once the new instance is ready, the old token no longer authenticates. To revoke a user, remove their entry and redeploy. Restarting with the changed secret does not rewrite or delete historical data because the service does not persist sessions or prompts. Requests already in progress may finish while an old instance is shutting down.

Catalog access is a separate server-side credential shared by the hosted service. Friends do not receive it, and an MCP bearer token does not grant catalog access outside this service.

## Codex CLI

Codex supports Streamable HTTP MCP servers and a bearer token sourced from an environment variable; see the [Codex MCP configuration guide](https://developers.openai.com/codex/mcp).

Set the following in `~/.codex/config.toml`, merging with existing settings:

```toml
[mcp_servers.skillscout]
url = "https://YOUR_HOST/mcp"
bearer_token_env_var = "SKILLSCRAPER_MCP_TOKEN"
tool_timeout_sec = 150
```

The token value is not stored in the TOML file. Make `SKILLSCRAPER_MCP_TOKEN` available to the Codex process through an OS credential manager or a secure shell environment. To enter it without putting the value in shell history:

```bash
read -r -s -p "SkillScraper token: " SKILLSCRAPER_MCP_TOKEN
printf '\n'
export SKILLSCRAPER_MCP_TOKEN
codex
unset SKILLSCRAPER_MCP_TOKEN
```

Verify with `codex mcp list` and `/mcp` in the Codex TUI. Ask Codex to call `doctor`, then check that the tool list includes `prepare_task`, `inspect_skill`, and `doctor`. Remove the client entry with `codex mcp remove skillscout`; this disconnects the client but does not revoke the server token. Revoke it through the host secret store as described above.

## Claude Code

Claude Code supports remote HTTP MCP configuration and environment-variable expansion in MCP headers. Its official [MCP guide](https://code.claude.com/docs/en/mcp) documents `type: "http"`, bearer headers, and environment variables.

Add this entry to the `mcpServers` object in a user-level `~/.claude.json` or a trusted project's `.mcp.json`, preserving existing servers:

```json
{
  "mcpServers": {
    "skillscout": {
      "type": "http",
      "url": "https://YOUR_HOST/mcp",
      "headers": {
        "Authorization": "Bearer ${SKILLSCRAPER_MCP_TOKEN}"
      }
    }
  }
}
```

The file contains only the environment-variable reference, not the token. If using a project `.mcp.json`, Claude Code may require you to approve the project server when the folder is untrusted. Make the token environment variable available to the `claude` process using a secure environment or credential manager. The same hidden-input shell example above can be used by replacing `codex` with `claude`.

Verify with `claude mcp list` and `/mcp`; check the server is connected and the three tools are listed, then call `doctor`. Remove the entry with `claude mcp remove skillscout`. Revoke the bearer token separately in the host secret store.

These instructions follow the clients' documented configuration formats. Neither Codex nor Claude Code was connected to a deployed SkillScraper endpoint during this work, so actual client compatibility remains unverified.

## Operational procedures

### Before first deployment

1. Obtain owner approval for the Render service plan and any hosting or domain costs. `render.yaml` chooses a paid `starter` plan but does not itself create a service.
2. Create the service from the repository's Render Blueprint. Keep automatic deploys disabled.
3. Set `SKILLSCRAPER_MCP_USERS_JSON` with one token per pilot user.
4. Set `SKILLSCRAPER_MCP_ALLOWED_ORIGINS` to the exact Render HTTPS origin shown after service creation, such as `["https://skillscraper-mcp.onrender.com"]`. Include a custom origin only if the owner configures that domain.
5. Add `SKILLSCOUT_CATALOG_TOKEN` through the host secret manager only if authorized catalog access is available. Do not substitute a token pasted into a task.
6. Deploy only after reviewing the environment values and the hosting cost. Confirm `/healthz` and `/readyz` respond with only `{"status":"ok"}` and `{"status":"ready"}`.
7. From an authorized host, verify missing and invalid tokens receive the same 401 response; then connect one pilot user and call `doctor` and a routine `prepare_task`.
8. Test a catalog-backed request only when a valid catalog credential is authorized. Record its live status separately from the automated test report.

### Rollback

Render supports rolling back to a prior deploy from its dashboard. Keep automatic deploys off; if a release fails health checks or pilot verification, stop onboarding and roll back to the last approved deploy. If a credential may have leaked, revoke or rotate it in the host secret store before or during rollback. Do not restore a compromised token from a prior deployment. See [Render web services](https://render.com/docs/web-services) for platform operations. No rollback was exercised because no service was deployed.

### Troubleshooting

| Symptom | Check |
| --- | --- |
| `/readyz` is not 200 | Startup configuration is invalid or the process is not ready. Check host logs for a setting name and validation message; secret values are not printed. |
| MCP returns 401 | Confirm the client process can read its own `SKILLSCRAPER_MCP_TOKEN`, the user token is current, and the Authorization header is being sent. Missing, invalid, and revoked tokens deliberately share one response. |
| MCP returns 403 or 421 | Check that a client-sent `Origin` is on the exact HTTPS allowlist and its `Host` matches. Native Codex/Claude Code CLI connections normally need no Origin allowlist entry. |
| Tools do not appear in Codex | Check `codex mcp list`, the URL ends in `/mcp`, and the bearer environment variable is set in the Codex process environment. Restart Codex after environment changes. |
| Tools do not appear in Claude Code | Check `claude mcp list`, `/mcp`, workspace trust, JSON syntax, and whether `${SKILLSCRAPER_MCP_TOKEN}` is available to the Claude Code process. Restart after changing the environment. |
| `catalog_credentials_missing` | Add an authorized server-side `SKILLSCOUT_CATALOG_TOKEN`. This is an operational error, not an empty result or a quality label. |
| Tool call times out | Check catalog service availability and the configured request timeout. The read-only tools can be retried; a timeout is not a recommendation decision. |

## Privacy, state, and dependencies

- The service receives task text and tool parameters over HTTPS. It processes them in memory. Remote calls cannot read local project files; `project_path` is rejected.
- Routine task wording can be evaluated locally without contacting the catalog. Catalog-backed `prepare_task` sends the existing redacted task query to the configured catalog. `inspect_skill` sends the requested skill ID and makes catalog requests. Task redaction is heuristic, so do not send secrets or sensitive task text.
- The MCP path disables the optional LLM workflow. It does not transmit task text to an LLM provider.
- The application has no database, review ledger, persistent disk, or prompt-retention feature. It does not log request bodies, task text, tokens, raw authorization headers, or exception text. Uvicorn access logging is disabled. Application logs are limited to safe startup, rate-limit, timeout, and exception-type diagnostics; platform infrastructure may retain its own service and health-check logs under the host's policy.
- Rate-limit counters and active request state exist only in process memory and disappear on restart. No cross-user session data or per-user private configuration is stored.
- Required external services are the Render web service and the skills.sh catalog when catalog-backed features are used. Cost factors include compute plan, runtime, bandwidth/egress, optional domain registration/DNS, and the catalog provider's account/access terms. This design adds no database, analytics, or LLM API dependency for MCP calls.

## Release and verification status

The automated ASGI tests use the installed MCP SDK and local fake transports. They are not a live remote-endpoint test, a real Codex or Claude Code session, a live catalog test, or human-reviewed recommendation-quality evidence. For current results and remaining blockers, see [hosted MCP readiness](hosted-mcp-readiness-2026-10-29.md).
