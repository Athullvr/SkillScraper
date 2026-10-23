# Troubleshooting

- **`skillscout` or `skillscout-mcp` is not found**: activate the virtual environment where you installed the wheel, or add that environment's `bin` directory (`Scripts` on Windows) to the MCP host's `PATH`. From a development checkout, run `uv sync` and use `uv run skillscout`.
- **The MCP host cannot start SkillScout**: run `skillscout doctor`, confirm the configured command is available to the host process, and check the host's MCP process logs (diagnostics go to stderr). If using a virtual environment, point `command` to its installed console script.
- **Python version error**: install Python 3.12+ and ensure uv selects that interpreter.
- **Invalid configuration**: `doctor` and tool errors identify the setting and expected format. Correct that environment variable and restart the MCP server; credential values are not printed.
- **Catalog 401/403**: SkillScout reports whether a credential is missing or the configured credential was rejected or lacks access. The current skills.sh API reference documents bearer-token authentication and no unauthenticated search/detail path. Enable OIDC Federation for a linked Vercel project, run `vercel link` and `vercel env pull`, then expose `VERCEL_OIDC_TOKEN` to the process that starts SkillScout. SkillScout does not load `.env.local` or refresh OIDC tokens; restart with a renewed token after expiry. The audit endpoint explicitly requires authentication.
- **Catalog 429/503**: SkillScout respects `Retry-After` and retries a bounded number of times; retry later if the endpoint remains unavailable.
- **Catalog timeout**: check network access and the configured `SKILLSCOUT_REQUEST_TIMEOUT_SECONDS` (1–120 seconds). SkillScout bounds both each request window and the number of retries.
- **Catalog HTTP error**: permanent request failures are not retried. Check that the configured HTTPS base URL implements the skills.sh v1 API and that the requested skill ID exists.
- **Incomplete search**: if the result page reaches its limit or indicates another page, a low-relevance result remains `REVIEW`. Retry later or review the available catalog results; SkillScout does not treat that page as evidence that no skill exists.
- **`REVIEW` with no audit**: no audit result is known for that skill/revision. This is not a pass; inspect the source and decide manually.
- **`REVIEW` with unknown compatibility**: add project metadata in an allowlisted root file or treat the result as a task-only assessment. SkillScout does not inspect source trees.
- **`REVIEW` with unresolved evidence**: follow the candidate's `unresolved_evidence` and `next_steps` fields. They distinguish missing, unavailable, stale, mismatched, and incomplete evidence. They describe evidence to obtain or review and do not change the decision automatically.
- **Empty evidence packet**: no inspected paragraph matched both a task term and, optionally, declared project technology under the deterministic lexical selector. The input budget may be zero to disable excerpts; otherwise check the packet's `explanation` and `truncated` fields. The packet is not a full skill document.
- **JSON parsing**: use `--format json`; errors are JSON on stdout with `schema_version` and `error` fields.

