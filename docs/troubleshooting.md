# Troubleshooting

- **`skillscout: command not found`**: run `uv tool install .`, or use `uv run skillscout` from the project checkout after `uv sync`.
- **Python version error**: install Python 3.12+ and ensure uv selects that interpreter.
- **Catalog 401/403**: the current skills.sh API reference documents bearer-token authentication and no unauthenticated search/detail path. Enable OIDC Federation for a linked Vercel project, run `vercel link` and `vercel env pull`, then expose `VERCEL_OIDC_TOKEN` to the process that starts SkillScout. SkillScout does not load `.env.local` or refresh OIDC tokens; restart with a renewed token after expiry. The audit endpoint explicitly requires authentication.
- **Catalog 429/503**: SkillScout respects `Retry-After` and retries a bounded number of times; retry later if the endpoint remains unavailable.
- **`REVIEW` with no audit**: no audit result is known for that skill/revision. This is not a pass; inspect the source and decide manually.
- **`REVIEW` with unknown compatibility**: add project metadata in an allowlisted root file or treat the result as a task-only assessment. SkillScout does not inspect source trees.
- **`REVIEW` with unresolved evidence**: follow the candidate's `unresolved_evidence` and `next_steps` fields. They distinguish missing, unavailable, stale, mismatched, and incomplete evidence. They describe evidence to obtain or review and do not change the decision automatically.
- **Empty evidence packet**: no inspected paragraph matched both a task term and, optionally, declared project technology under the deterministic lexical selector. The input budget may be zero to disable excerpts; otherwise check the packet's `explanation` and `truncated` fields. The packet is not a full skill document.
- **JSON parsing**: use `--format json`; errors are JSON on stdout with `schema_version` and `error` fields.

