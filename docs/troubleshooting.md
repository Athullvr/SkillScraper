# Troubleshooting

- **`skillscout: command not found`**: run `uv tool install .`, or use `uv run skillscout` from the project checkout after `uv sync`.
- **Python version error**: install Python 3.12+ and ensure uv selects that interpreter.
- **Catalog 401/403**: set `SKILLSCOUT_CATALOG_TOKEN` or `VERCEL_OIDC_TOKEN` if the catalog endpoint requires authentication.
- **Catalog 429/503**: SkillScout respects `Retry-After` and retries a bounded number of times; retry later if the endpoint remains unavailable.
- **`REVIEW` with no audit**: no audit result is known for that skill/revision. This is not a pass; inspect the source and decide manually.
- **`REVIEW` with unknown compatibility**: add project metadata in an allowlisted root file or treat the result as a task-only assessment. SkillScout does not inspect source trees.
- **JSON parsing**: use `--format json`; errors are JSON on stdout with `schema_version` and `error` fields.

