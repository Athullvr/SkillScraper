# Public distribution and Codex integration follow-up — 2026-10-24

Historical SkillScout snapshot recorded on 2026-10-24. The current product and distribution name is SkillScraper. Artifact names, package metadata, commands, dates, test results, and evidence below are retained as they were observed; this note does not revise those results.

## Package metadata and clean installs

The existing Hatchling configuration builds both formats. The final artifacts are:

- `/tmp/skillscout-public-dist-final3-20261024/skillscout-0.1.0-py3-none-any.whl`
- `/tmp/skillscout-public-dist-final3-20261024/skillscout-0.1.0.tar.gz`

Metadata remains package `skillscout` version `0.1.0`, Python `>=3.12`, with declared runtime dependencies `httpx`, `mcp`, `pydantic`, `rich`, and `typer`. The wheel contains `skillscout-codex-hook`, `skillscout-mcp`, and `skillscout` entry points, the Codex hook and its dependency-safe bootstrap, the MCP server, and the evaluation package. No runtime dependency was added.

The wheel and source distribution were installed in separate temporary Python 3.12 environments outside the checkout. These commands reproduce the verification (dependency sync may access the configured Python package index):

```bash
UV_CACHE_DIR=/tmp/skillscout-public-uv-cache uv --offline build \
  --out-dir /tmp/skillscout-public-dist-final3-20261024

python3.12 -m venv /tmp/skillscout-public-clean-20261024
UV_CACHE_DIR=/tmp/skillscout-public-uv-cache \
UV_PROJECT_ENVIRONMENT=/tmp/skillscout-public-clean-20261024 \
  uv sync --locked --all-extras --no-install-project
/tmp/skillscout-public-clean-20261024/bin/python -m pip install \
  --no-index --no-deps \
  /tmp/skillscout-public-dist-final3-20261024/skillscout-0.1.0-py3-none-any.whl
/tmp/skillscout-public-clean-20261024/bin/python -m pip check
/tmp/skillscout-public-clean-20261024/bin/skillscout --version
/tmp/skillscout-public-clean-20261024/bin/skillscout doctor

python3.12 -m venv /tmp/skillscout-public-sdist-clean-20261024
UV_CACHE_DIR=/tmp/skillscout-public-uv-cache \
UV_PROJECT_ENVIRONMENT=/tmp/skillscout-public-sdist-clean-20261024 \
  uv --offline sync --locked --all-extras --no-install-project
UV_CACHE_DIR=/tmp/skillscout-public-uv-cache uv pip install --offline \
  --python /tmp/skillscout-public-sdist-clean-20261024/bin/python \
  'hatchling>=1.24,<2.0'
PIP_CACHE_DIR=/tmp/skillscout-public-pip-cache \
  /tmp/skillscout-public-sdist-clean-20261024/bin/python -m pip install \
  --no-index --no-deps --no-build-isolation \
  /tmp/skillscout-public-dist-final3-20261024/skillscout-0.1.0.tar.gz
/tmp/skillscout-public-sdist-clean-20261024/bin/python -m pip check
/tmp/skillscout-public-sdist-clean-20261024/bin/skillscout --version
/tmp/skillscout-public-sdist-clean-20261024/bin/skillscout doctor
```

Both installs reported `SkillScout 0.1.0`, Python 3.12.3 supported, no catalog credential configured, and `pip check` found no broken requirements. `doctor` explicitly reported that it does not make network requests. With `SKILLSCOUT_CANDIDATE_LIMIT=invalid`, `doctor` exited 2 and printed `SKILLSCOUT_CANDIDATE_LIMIT must be an integer` without echoing a value.

Missing-dependency handling was checked in a separate venv with the final wheel installed using `--no-deps`. `skillscout doctor` and `skillscout-mcp` both exited 2 with a message to reinstall the wheel without `--no-deps`. `skillscout-codex-hook` emitted valid JSON containing `REVIEW` and the same reinstall instruction, without a traceback or stderr output. A fixture-backed MCP call for missing credentials returned `catalog_credentials_missing` and named `VERCEL_OIDC_TOKEN` and `SKILLSCOUT_CATALOG_TOKEN` as the corrective configuration options. Credential values were never printed.

The package has **not** been published to PyPI or another registry. The project-supplied wheel or source distribution is required for installation; no registry availability is claimed.

## Stdio MCP and real client

The installed wheel was exercised with the MCP Python SDK (`mcp` 2.3.0, `ClientSession` and `stdio_client`) and with raw JSON-RPC over the installed process's stdin/stdout:

```bash
./.venv/bin/python scripts/mcp_stdio_smoke.py \
  --venv /tmp/skillscout-public-clean-20261024 --timeout 25
```

Result: pass. The SDK negotiated protocol `2025-11-25`, discovered `prepare_task`, `inspect_skill`, and `doctor`, and invoked all three. The normal installed process returned `NO_SKILL_NEEDED` for a routine task, structured `invalid_input` for an invalid argument, and continued to answer `doctor`. Fixture mode exercised successful `USE` and `REVIEW`, missing credentials, and a sanitized upstream failure. Raw stdio recovered from malformed JSON, processed three sequential requests, exited `0` on EOF, and wrote zero stderr bytes. The installed hook entry point also consumed a synthetic Codex event and returned `NO_SKILL_NEEDED` while `SKILLSCOUT_CANDIDATE_LIMIT=invalid`; this confirmed the routine hook path did not load configuration. No catalog transport was invoked.

A real Codex CLI session was run against the final wheel-installed `skillscout-mcp` command. Environment: Codex CLI `0.162.1`, ephemeral session, read-only sandbox, ignored user configuration, and one-shot command-line MCP overrides. Codex discovered and invoked `doctor` (success), `prepare_task` for the routine variable-renaming task (`NO_SKILL_NEEDED`), and `inspect_skill` with an invalid ID (`invalid_input`). It summarized the three structured results and completed the turn. This test made no catalog request and wrote no client configuration. The client connection was real; the prompts and project directory were deliberately synthetic.

Exact client command:

```bash
codex exec --ephemeral --ignore-user-config --json --skip-git-repo-check \
  -s read-only -C /tmp/skillscout-codex-integration \
  -c 'mcp_servers.skillscout.command="/tmp/skillscout-public-clean-20261024/bin/skillscout-mcp"' \
  -c 'mcp_servers.skillscout.cwd="/tmp/skillscout-codex-integration"' \
  'Use only the skillscout MCP server. Call doctor, then prepare_task with task exactly "Rename a local variable in a Python helper function." and project_path "/tmp/skillscout-codex-integration", then inspect_skill with skill_id exactly "not-a-valid-id". Do not call other tools or modify files. Summarize the decision and result status or error code for each call.'
```

The test above is a real MCP client integration, not a live test of the new prompt hook. The hook's stdin/stdout event contract is covered by unit tests and by the installed-command fixture smoke. It was not registered or trusted in a Codex session because that requires an explicit client configuration change and Codex hook review. Claude Code was not exercised.

## Opt-in Codex prompt routing

Codex documents command hooks in `~/.codex/hooks.json`, `~/.codex/config.toml`, or project equivalents. Its `UserPromptSubmit` event receives the prompt and working directory; it has no matcher, and a JSON `hookSpecificOutput.additionalContext` is added as developer context. Non-managed hooks require review and trust through `/hooks`. See the [official Codex hooks guide](https://developers.openai.com/codex/hooks) and [Codex MCP guide](https://developers.openai.com/codex/mcp).

The new `skillscout-codex-hook` entry point uses the existing recommendation workflow with optional LLM use disabled. It is never auto-registered. It adds decision context only; it cannot force the model to follow a recommendation. `USE` prompts fit, safety, and compatibility review; `NO_SKILL_NEEDED` means continue normally and is not a safety assessment; `REVIEW` explicitly means unresolved evidence, not approval; `AVOID` rejects the flagged candidate while leaving another safe way to complete the task. None of these outcomes install, activate, or execute a skill. Operational errors and malformed events become `REVIEW`, not `NO_SKILL_NEEDED`.

Routine tasks remain on the local path and do not load catalog configuration. Other prompts may send the redacted task query to the catalog. Redaction is heuristic. The hook does not use an LLM, disables retries, and caps each catalog request at four seconds; a multi-candidate decision can take longer. The README warns that the event runs on every prompt, and developers should not enable it for sensitive task text.

This is the least intrusive verified routing option for the first client: an explicit, user-configured prompt hook that supplies evidence before the model turn. Codex's hook context can influence the workflow, but the integration does not promise automatic skill activation or model compliance. The supported MCP server remains independently available for explicit tool calls.

## Tests, data preservation, and release limits

Focused suite:

```bash
./.venv/bin/python -m pytest -q \
  tests/test_codex_hook.py tests/test_mcp_server.py \
  tests/test_catalog_transport.py tests/test_routine_fastpath.py
```

Result: **118 passed**. Full suite: `.venv/bin/python -m pytest -q` — **288 passed**. No test failures or skips in the final runs. `git diff --check` passed. The hook fixture, SDK smoke, raw protocol test, and Codex prompt used synthetic tasks; there were no live catalog requests.

The proposed calibration split remains 40 examples: 32 development and 8 held-out. All 40 have `assessment_status: pending`, `label_provenance: pending`, null reviewer, and null expected labels. The dataset-level `data_kind: human_reviewed` is the schema's review-workflow marker; the calibration README explicitly says it does not mean any example was reviewed. The count of genuine human-reviewed examples is **0**. No calibration files were changed, no pending or held-out examples were used for tuning, and no recommendation accuracy claim is made.

The remaining distribution blocker is the absence of an authorized public registry release. The implementation also needs a live Codex hook registration/trust check by a developer who chooses to enable it. Neither limit blocks installation of a supplied artifact or ordinary MCP use. The current README includes installation/upgrade, stdio MCP configuration, local diagnostics, catalog setup, hook setup, and error guidance.

An initial update to the smoke script expected a `prepare_task`-style next action from an `inspect_skill` credential error. That assertion was corrected to match the actual inspection contract; the final installed-package smoke then passed. The first sandboxed SDK smoke also timed out during nested process initialization; running the same smoke outside the nested-process sandbox passed.
