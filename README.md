# SkillScout

SkillScout is a local-first Python CLI for deciding whether a catalog skill adds clear value to a coding task. It ranks a small set of candidates, reads bounded project metadata, inspects catalog evidence, and reports limitations before a developer decides whether to install anything.

SkillScout never installs, activates, imports, or executes candidate files. Proposed install commands are inert report text.

## Install and run

Requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install .
skillscout doctor
skillscout recommend "Add Supabase OAuth login to the FastAPI service" --project . --agent codex
skillscout recommend "Migrate this app from Django to FastAPI" --format json
skillscout inspect vercel-labs/agent-skills/find-skills
```

For development:

```bash
uv sync --all-extras
uv run skillscout doctor
uv run pytest
```

The standard tests use fixture responses and make no network requests. See [the separate manual catalog demo](docs/manual-catalog-demo.md) for an explicit live request.

## Commands

### `skillscout recommend "<task>"`

Assesses whether a skill adds value beyond an agent's ordinary coding ability. Routine edits can return `NO_SKILL_NEEDED` without contacting the catalog. Other tasks use the skills.sh search endpoint, inspect no more than three candidates by default, and return evidence for each inspected candidate.

Options:

- `--project PATH`: project directory to scan. Defaults to the current directory.
- `--agent codex`: target agent used in the proposed install command. Defaults to `codex`.
- `--format text|json`: terminal report or versioned JSON. Defaults to `text`.

The candidate limit is configurable with `SKILLSCOUT_CANDIDATE_LIMIT` (1–5, default 3). Ranking weights are initial hypotheses, not validated truth.

### `skillscout inspect <skill-id>`

Fetches the requested skill detail, scans the bounded returned file set, and reports external audits if available. It does not assess task fit and therefore does not return `USE` as a task recommendation. `--project` and `--format text|json` are also supported.

### `skillscout doctor`

Checks the Python version and local configuration. It does not contact the catalog or LLM provider.

## Decisions and exit codes

`USE` means the task fit, compatibility metadata, complete content inspection, and revision-bound external audit meet SkillScout's current policy. It is not a safety guarantee.

`REVIEW` means a human should resolve unknowns, warnings, stale evidence, or compatibility gaps. `AVOID` means a current critical local heuristic finding or a current failing/high-risk external audit created a hard block. `NO_SKILL_NEEDED` means no inspected candidate demonstrated enough additional task value.

| Exit code | Meaning |
| --- | --- |
| `0` | Command completed, including `NO_SKILL_NEEDED`, `USE`, and `REVIEW` |
| `2` | Invalid input/configuration, unsupported Python, or invalid project path |
| `3` | Catalog authentication, network, HTTP, or response validation error |
| `4` | A current hard block produced `AVOID` |

Typer uses exit code `2` for invalid command syntax. JSON-mode errors use `{"schema_version":"1.0","error":{"code":"...","message":"..."}}` on stdout.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SKILLSCOUT_CATALOG_URL` | `https://skills.sh` | HTTPS skills.sh-compatible API base |
| `SKILLSCOUT_CATALOG_TOKEN` | unset | Optional bearer token for catalog endpoints |
| `VERCEL_OIDC_TOKEN` | unset | Fallback catalog bearer token |
| `SKILLSCOUT_CANDIDATE_LIMIT` | `3` | Detail records to inspect, bounded to 1–5 |
| `SKILLSCOUT_LLM_API_KEY` | unset | Optional OpenAI-compatible provider key |
| `SKILLSCOUT_LLM_BASE_URL` | `https://api.openai.com/v1` | Optional provider API base |
| `SKILLSCOUT_LLM_MODEL` | `gpt-4o-mini` | Optional provider model identifier |
| `SKILLSCOUT_LLM_SEND_TASK` | `false` | Explicitly allow sending task text for LLM task-fit help |

Catalog requests use explicit connect/read timeouts, response size caps, and bounded retries. Audit lookup failures are recorded as unknown evidence and do not stop search. The API may require credentials; configure a token if search or detail requests return 401/403.

## Privacy

Project awareness reads only selected metadata files directly in the project root (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`), checks a short allowlist of lockfiles/config markers, and caps metadata reads at 64 KiB per file. It does not traverse source trees, execute scripts, install dependencies, or upload project source.

The deterministic path sends the task query to skills.sh for search. The optional LLM adapter is disabled unless an API key is configured and `SKILLSCOUT_LLM_SEND_TASK=1` explicitly opts into sending task text. Only the redacted task and bounded candidate description are eligible for transmission. Project metadata and source files are not sent.

## Security model and limitations

The scanner checks retrieved text for suspicious instruction overrides, credential access near outbound transfer, obfuscated execution, destructive commands, direct shell pipelines, and unreferenced executable-looking files. These checks are heuristic and can miss risks or flag benign content. `USE` requires a complete snapshot, a content hash, project compatibility evidence, no medium/high local warning, and passing external audits bound to that exact hash.

Unknown audit status, unbound audit revisions, missing hashes, incomplete details, and uncertain compatibility remain unknown and lead to `REVIEW`. A hash change prevents old audit evidence from satisfying the current revision gate. External audit providers may have their own gaps. Review the source and audit details before manually installing a skill.

See [the security model](docs/security-model.md), [JSON schema notes](docs/json-schema.md), [an example report](docs/example-report.md), and [troubleshooting](docs/troubleshooting.md).

