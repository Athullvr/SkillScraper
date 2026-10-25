# Manual live catalog demo

This is a separate, explicit network check. It is not part of pytest. Before running it, provide an authorized `VERCEL_OIDC_TOKEN` or `SKILLSCOUT_CATALOG_TOKEN` to the SkillScout process through an approved secret manager or environment-injection mechanism. If neither credential is configured, catalog operations stop locally with an actionable missing-credential error and send no request. `doctor` remains local-only.

```bash
uv sync --all-extras
uv run skillscout doctor
uv run skillscout recommend "Add OAuth login to a FastAPI service" --project . --format json
uv run skillscout inspect vercel-labs/skills/find-skills --format json
```

The command sends the task query to the skills.sh search API, then requests details for no more than the configured candidate limit. The [skills.sh API reference](https://www.skills.sh/docs/api) documents bearer authentication for API calls and explicitly says audit requests require authentication; it does not document an unauthenticated search or detail path. Its documented credential is a Vercel OIDC token. Enable OIDC Federation for a linked Vercel project, run `vercel link` and `vercel env pull`, then make `VERCEL_OIDC_TOKEN` available in the environment that starts SkillScout. `vercel env pull` writes `.env.local`, which SkillScout does not read automatically. Refresh the token and restart SkillScout after expiry. Never paste the token into task text or commit it. `SKILLSCOUT_CATALOG_TOKEN` is a bearer-token override if the endpoint accepts it, not a documented separate skills.sh API key. Audit access can be unavailable even when search and details work; this remains unknown evidence and does not break search.

Review the JSON for stable IDs, source URLs, retrieval timestamps, content hashes, audit revision association, and local findings. The CLI prints proposed install commands but never runs them.

