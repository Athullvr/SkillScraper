# Manual live catalog demo

This is a separate, explicit network check. It is not part of pytest.

```bash
uv sync --all-extras
uv run skillscout doctor
uv run skillscout recommend "Add OAuth login to a FastAPI service" --project . --format json
uv run skillscout inspect vercel-labs/skills/find-skills --format json
```

The command sends the task query to the skills.sh search API, then requests details for no more than the configured candidate limit. It may return 401/403 if the API requires credentials; set `SKILLSCOUT_CATALOG_TOKEN` or `VERCEL_OIDC_TOKEN` and retry. Audit access can be unavailable even when search and details work; this remains unknown evidence and does not break search.

Review the JSON for stable IDs, source URLs, retrieval timestamps, content hashes, audit revision association, and local findings. The CLI prints proposed install commands but never runs them.

