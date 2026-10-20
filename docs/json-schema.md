# JSON report schema

`recommend --format json` emits a Pydantic-validated object with `schema_version: "1.0"`. The public shape is generated from `skillscout.models.Report` with aliases enabled and is stored in `report.schema.json`. For example, catalog provenance fields serialize as `url`, `installUrl`, `sourceType`, and `hash`. `inspect --format json` follows `inspection.schema.json`; JSON command errors follow `error.schema.json`. The version remains `1.0` for compatible additive changes; incompatible changes require a new version.

The MCP tools have separate response schemas in `mcp-prepare.schema.json` and `mcp-inspection.schema.json`. This increment adds optional/defaulted evidence-status, unresolved-evidence, next-step, and recommendation excerpt-packet fields without changing `schema_version: "1.0"`; existing consumers can ignore those additional fields. `prepare_task.context_budget_bytes` defaults to 1200, accepts `0` to disable excerpts, and is hard-capped at 2400 UTF-8 bytes of excerpt bodies.

The top-level fields are:

- `schema_version`, `generated_at`, `task`, and `agent`.
- `project`: root path, directly detected metadata filenames, detected/inferred facts, and unknowns.
- `decision`: `NO_SKILL_NEEDED`, `USE`, `REVIEW`, or `AVOID`.
- `summary`, `candidates`, and `limitations`.

Each candidate includes catalog identity/provenance, retrieval time, task-fit score and rationale, project compatibility, detail revision/hash, files and inspection timestamp, external audit findings, local heuristic findings, decision reasons, limitations, and a proposed install command. See the [synthetic example report](example-report.md).

JSON is written to stdout without Rich formatting so it can be piped to other tools. Timestamps are timezone-aware ISO 8601 values.

