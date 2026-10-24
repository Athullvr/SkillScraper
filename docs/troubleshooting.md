# Troubleshooting

## First-run checks

- Check the installed entry point and local settings with `skillscout --version` and `skillscout doctor`. Doctor reports whether credentials are configured, but never tests catalog connectivity or prints their values.
- Confirm the local fast path with `skillscout recommend "Rename a local variable in a helper function." --format json`. The expected decision is `NO_SKILL_NEEDED`; this task does not load catalog settings or make a network request.
- `skillscout-mcp` is a stdio server. It waits for an MCP client and intentionally emits no startup banner on stdout. Run `skillscout-mcp` from a terminal only as a short startup check, then stop it with Ctrl+C. For a handshake and tool-call check against an installed wheel, follow the `mcp_stdio_smoke.py` command in the README.
- SkillScout provides example configuration for Claude Code and Codex CLI in the README. Copy the example into the client configuration you choose and set `command` to the installed `skillscout-mcp` path if it is not on the host's `PATH`. SkillScout does not create or edit client configuration files.

- **`skillscout` or `skillscout-mcp` is not found**: activate the virtual environment where you installed the wheel, or add that environment's `bin` directory (`Scripts` on Windows) to the MCP host's `PATH`. From a development checkout, run `uv sync` and use `uv run skillscout`.
- **The MCP host cannot start SkillScout**: run `skillscout doctor`, confirm the configured command is available to the host process, and check the host's MCP process logs (diagnostics go to stderr). If using a virtual environment, point `command` to its installed console script.
- **An entry point reports missing dependencies**: install or upgrade the wheel without `--no-deps` in the same environment as the entry point, then verify with `python -m pip check`.
- **Python version error**: install Python 3.12+ and ensure uv selects that interpreter.
- **Invalid configuration**: `doctor` and tool errors identify the setting and expected format. Correct that environment variable and restart the MCP server; credential values are not printed.
- **Catalog credentials**: configure `VERCEL_OIDC_TOKEN` or `SKILLSCOUT_CATALOG_TOKEN` in the environment of the MCP host process using your OS or team's secret manager. Do not place the value in shell history, task text, logs, or client configuration. For the documented Vercel flow, enable OIDC Federation and use `vercel link` and `vercel env pull`; securely pass the resulting token to the host process. SkillScout does not load `.env.local` or refresh OIDC tokens. Restart the MCP server after renewing an expired token.
- **Catalog 401/403**: the error distinguishes missing credentials from a rejected credential or insufficient access. Verify that the token is current and available to the process that launches SkillScout. The skills.sh API requires bearer authentication, including for audits; local `doctor` and routine `NO_SKILL_NEEDED` decisions do not require it.
- **Catalog 429/503**: SkillScout respects `Retry-After` and retries a bounded number of times; retry later if the endpoint remains unavailable.
- **Catalog timeout or connection failure**: check the host's network/DNS access and `SKILLSCOUT_REQUEST_TIMEOUT_SECONDS` (1–120 seconds). SkillScout bounds the request window and retry count. Retry later if the connection problem persists; a failure is returned as an operational error, not as `NO_SKILL_NEEDED`.
- **Catalog HTTP error**: permanent request failures are not retried. Check that the configured HTTPS base URL implements the skills.sh v1 API and that the requested skill ID exists.
- **Incomplete search**: if the result page reaches its limit or indicates another page, a low-relevance result remains `REVIEW`. Retry later or review the available catalog results; SkillScout does not treat that page as evidence that no skill exists. A completed empty search and an incomplete empty search have different outcomes.
- **`REVIEW` with no audit**: no audit result is known for that skill/revision. This is not a pass; inspect the source and decide manually.
- **`REVIEW` with unknown compatibility**: add project metadata in an allowlisted root file or treat the result as a task-only assessment. SkillScout does not inspect source trees.
- **`REVIEW` with unresolved evidence**: follow the candidate's `unresolved_evidence` and `next_steps` fields. They distinguish missing, unavailable, stale, mismatched, and incomplete evidence. They describe evidence to obtain or review and do not change the decision automatically.
- **Empty evidence packet**: no inspected paragraph matched both a task term and, optionally, declared project technology under the deterministic lexical selector. The input budget may be zero to disable excerpts; otherwise check the packet's `explanation` and `truncated` fields. The packet is not a full skill document.
- **JSON parsing**: use `--format json`; errors are JSON on stdout with `schema_version` and `error` fields.

