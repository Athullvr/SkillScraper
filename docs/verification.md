# Verification snapshots

## Routine fast-path and Codex invocation check

### Follow-up protocol (recorded before the follow-up probes)

The fixed evaluation dataset and its predeclared relevance judgments remain unchanged. The task file's recorded SHA-256 is `6bcdd11f33f55193edd3808d02075a22dfa47f86333d099e8432e448010c3649`; the ordinary-search paired comparison remains unrun because authorized catalog access is unavailable. The follow-up below is a regression and host-invocation check, not a recommendation-quality comparison.

The routine probe will invoke the exact local-variable task three times with the repository project path and record each process exit code and JSON decision. Classifier and workflow tests use a catalog stub to observe whether catalog access is attempted; no live catalog request is part of this probe. The Codex comparison will use the same read-only prompt, installed SkillScout command, one-shot MCP registration, and identical temporary project contents; the only changed variable will be the presence of the project-local `AGENTS.md` text. The ephemeral, read-only Codex host's JSON event stream will be inspected for SkillScout MCP tool calls and exit status. No candidate-quality outcome will be inferred from either probe.

### Follow-up results

The original failure was in routine task matching. `prepare_recommendation` reads bounded project metadata, checks `should_skip_catalog_for_task`, and only then constructs a catalog client. The rename pattern recognized “rename a variable” and “rename the variable,” but not “rename a local variable.” A mocked reproduction before the change returned `task_is_routine: false`, invoked catalog search once, and propagated `CatalogUnavailableError`; the CLI's prior three-run observation was the corresponding exit-3 `catalog_error`. The classifier omission caused an unnecessary catalog request. The old generic transport error does not identify the underlying network condition; the CLI correctly kept it an operational error. No authentication or retry behavior changed.

`src/skillscout/ranking.py` now recognizes “local variable” in the rename/change pattern. Existing security-topic exclusions and specialist-task catalog behavior are unchanged. New regression tests cover the exact wording, a similar wording, a security-qualified task, the no-catalog CLI path, operational-error preservation, and the shared MCP path. The exact command `./.venv/bin/python -m skillscout recommend 'Rename a local variable in a Python helper function.' --project . --format json` was run three times; each exited 0 and returned `NO_SKILL_NEEDED` with no candidates and the existing statement that it did not contact the catalog.

The real host comparison used Codex CLI 0.162.1 with the same prompt and identical synthetic project files in ephemeral, read-only sessions, registered to the local `skillscout mcp` stdio command through one-shot command-line settings. The no-instruction control exited 0 in 19.574 seconds and emitted no SkillScout tool events. With the project-local `AGENTS.md`, the host exited 0 in 17.632 seconds and emitted `prepare_task` start and completion events; a read-only replay exited 0 in 11.752 seconds and confirmed the structured result had `status: success` and `decision: NO_SKILL_NEEDED`. Codex's stderr contained only its informational “Reading additional input from stdin...” message. The host did not modify files, no global configuration was written, and no catalog-backed call occurred. This demonstrates invocation for this Codex version and prompt only; it does not guarantee automatic invocation in other tasks or hosts.

The same command arguments were used for both fixtures; `$PROJECT` names the temporary project, and `$SCOUT_COMMAND` is the repository virtual environment's installed `skillscout` executable:

```bash
codex exec --ephemeral --ignore-user-config --json --skip-git-repo-check \
  -s read-only -C "$PROJECT" \
  -c "mcp_servers.skillscout.command=\"$SCOUT_COMMAND\"" \
  -c 'mcp_servers.skillscout.args=["mcp"]' \
  -c "mcp_servers.skillscout.cwd=\"$PROJECT\"" \
  'Task: "Rename a local variable in a Python helper function." Before choosing or using an external specialist coding skill, decide whether one would help. Do not edit files or run shell commands. If an MCP recommendation is available, use it and briefly report the decision.'
```

The 15-task evaluation JSONL and its SHA-256 remain unchanged. These routine probes and host calls are operational checks, not the predeclared catalog recommendation comparison; they do not establish recommendation quality.

## Live catalog validation and evaluation v1

**Checked:** 2026-10-20. The repository environment is Python 3.12.3, SkillScout 0.1.0, MCP SDK 2.3.0, HTTPX 0.28.1, and Pydantic 2.14.1. `skillscout doctor` reported that catalog credentials are not configured and optional LLM use is disabled. A process-environment check found neither `SKILLSCOUT_CATALOG_TOKEN` nor `VERCEL_OIDC_TOKEN`; token values were never read into output.

### Catalog and evidence

The adapter calls the documented HTTPS skills.sh v1 search (`/api/v1/skills/search`), detail (`/api/v1/skills/{id}`), and audit (`/api/v1/skills/audit/{id}`) endpoints. The current [skills.sh API reference](https://www.skills.sh/docs/api) documents bearer authentication for API examples, identifies missing/invalid/expired Vercel OIDC tokens as HTTP 401, and explicitly requires authentication for audit requests. It does not describe an unauthenticated search or detail route or a separate API key. The documented local credential setup is a linked Vercel project with OIDC Federation enabled and a fresh `VERCEL_OIDC_TOKEN` obtained through `vercel link` and `vercel env pull` ([Vercel OIDC documentation](https://vercel.com/docs/oidc)). SkillScout reads that variable or a bearer-token override from its process environment, does not parse `.env.local`, and does not refresh expiring tokens.

Stable identity is the catalog `id` (`source/slug`), corroborated by `source` and `slug`. The API describes a detail response's `hash` as a SHA-256 digest of the returned skill file content; SkillScout uses it as the inspected revision. Audit findings carry `provider`, `status`, `auditedAt`, risk/category fields, and an optional revision; only findings tied to the same detail hash can count as revision-bound audit evidence. A hash identifies content but is not a publisher signature. The code and mocked transport tests validate these fields and conservative mismatch/missing-evidence handling; no current authorized live search, detail, audit, or hash match was observed in this environment.

The last recorded live auth probe on 2026-10-18 returned HTTP 401 `authentication_required`. That is historical evidence, not a new live result. On 2026-10-20, the CLI's local-variable routine probe ended with generic `catalog_error` / “catalog request failed after bounded retries”; it yielded no HTTP status or response payload, so its cause is not claimed to be authentication. The exact missing prerequisite for an authorized live validation is a valid bearer credential made available to the SkillScout process. No authentication bypass was attempted and no candidate ID, content, hash, audit, or install command was invented.

### Actual coding-agent host

Codex CLI 0.162.1 was available and logged in. The server was registered only for the one-shot runs through inline `-c mcp_servers.skillscout...` overrides; no agent configuration file was written. With `--ephemeral` and a read-only sandbox, the real host initialized, discovered and invoked `doctor`, `prepare_task`, and `inspect_skill`. Codex parsed a valid doctor response, `NO_SKILL_NEEDED` for the routine task, and an `invalid_input` result for a malformed skill ID. The explicit run exited 0 in 17.694 seconds. No catalog-backed host call was attempted.

A second Codex run asked whether a README typo needed a specialist skill without naming SkillScout. It exited 0 in 12.547 seconds and made no SkillScout MCP call, despite the server-provided instructions. For reliable consideration before choosing a specialist skill, add host-side instructions or explicitly request `prepare_task`. This is one observation for a routine task, not a claim about every host or every specialist task. The server's MCP instructions are guidance, not an invocation guarantee.

### Evaluation v1

[`evaluation/v1/tasks.jsonl`](evaluation/v1/tasks.jsonl) is a separate 15-task set (5 routine, 5 skill-may-help, 5 weak/incomplete/suspicious) with per-task human relevance criteria. The fixture-only critical case is not a live catalog candidate. The dataset SHA-256 is `6bcdd11f33f55193edd3808d02075a22dfa47f86333d099e8432e448010c3649`. The earlier 20-task exploratory set is unchanged.

Each of the five routine tasks was run three times through `skillscout recommend` from the repository root, using the SkillScout repository as the project argument for every task. This was a classifier/operational smoke probe, not a project-context compatibility evaluation. Four task cases returned `NO_SKILL_NEEDED` on all repetitions (12/12). “Rename a local variable in a Python helper function” did not match the routine classifier and returned exit 3 / `catalog_error` on all three attempts; that is an operational failure, not a valid no-skill decision. Per-run timings and outcomes are in [`evaluation/v1/results.json`](evaluation/v1/results.json).

The 14-task ordinary-search/SkillScout live comparison, details, audits, and evidence completeness are **not run** because no authorized catalog credential was available. Useful selection, unnecessary recommendations, relevant skills missed, review/avoid rates, paired latency, and live provenance metrics remain unmeasured. The ordinary-search baseline was not replaced with fixture data. No actual developer tasks or comparative coding outcomes were measured.

The v1 files were added without runtime dependencies or catalog/server changes. See [`evaluation/v1/README.md`](evaluation/v1/README.md) for the paired comparison protocol, scoring criteria, limitations, and exact setup prerequisite.

### Commands and results

```bash
./.venv/bin/skillscout doctor
./.venv/bin/python -m pytest -q tests/test_evaluation_dataset.py tests/test_catalog.py tests/test_catalog_transport.py tests/test_mcp_server.py
./.venv/bin/python -m pytest -q
UV_CACHE_DIR=/tmp/skillscout-v3-uv-cache uv run --offline --with ruff ruff check tests/test_evaluation_dataset.py
UV_CACHE_DIR=/tmp/skillscout-v3-uv-cache uv run --offline --with ruff ruff format --check tests/test_evaluation_dataset.py
git diff --check
```

Results: doctor exited 0 and reported catalog credentials and optional LLM disabled; the focused suite passed **66 tests**; the complete suite passed **108 tests**; Ruff check passed and its format check reported the file already formatted; `git diff --check` passed. `docs/evaluation/v1/results.json` is valid JSON and the task file parses as 15 JSONL records. No package build was run because no runtime, dependency, or entry-point files changed.

The explicit host run used the real Codex CLI with inline configuration and no persisted configuration:

```bash
codex exec --ephemeral --ignore-user-config --json -s read-only -C "$PWD" \
  -c "mcp_servers.skillscout.command=\"$PWD/.venv/bin/skillscout\"" \
  -c 'mcp_servers.skillscout.args=["mcp"]' \
  -c "mcp_servers.skillscout.cwd=\"$PWD\"" \
  'Use only the skillscout MCP server. Call doctor, then prepare_task with task exactly "Fix a typo in the README installation section." (default project path), then inspect_skill with skill_id exactly "not-a-valid-id". Do not call other tools or modify files. Summarize each returned status, decision, or error code.'
```

This command completed successfully after the sandbox blocked Codex's local app-server startup; the approved run was ephemeral and used the read-only sandbox. A separate same-host run asked whether a README typo needed a specialist skill without naming SkillScout; it completed without calling the MCP server. Both command inputs and the structured results are summarized in the v1 result snapshot.

## SkillScout v3 MCP stdio connectivity

**Checked:** 2026-10-20. No server or CLI code change was needed. The README's installed launch command (`skillscout mcp`) and local stdio transport worked as documented once tested outside the nested-process sandbox.

### Environment and previous timeout

The lockfile and repository environment both resolve MCP SDK `2.3.0`; the active interpreter was `/home/athulvr/Documents/My Projects/SkillScraper/.venv/bin/python` (Python 3.12.3, resolving to `/usr/bin/python3`) with AnyIO `4.15.1`. Pytest's configured `pythonpath = ["src"]` imported SkillScout from `src/skillscout`. The existing `test_stdio_process_starts_without_non_protocol_startup_output` launches `[sys.executable, "-m", "skillscout", "mcp"]` from the repository root and checks that it stays alive with empty startup stdout/stderr. That test does not perform MCP initialization.

Inside the sandbox, the SDK `Client`/`stdio_client` handshake timed out for both the installed SkillScout command and an isolated server built with the same SDK, in both auto and legacy modes. A raw JSON-RPC initialize request also received no stdout response; those diagnostic processes were terminated after the timeout (`-15`). The same sandboxed nested-process behavior affected a child running the trivial `anyio.to_thread.run_sync(lambda: 123)` probe, while the top-level AnyIO probe completed. The first diagnostic attempt used `io.StringIO` for the SDK's stderr sink and failed immediately because the SDK requires a file descriptor; replacing it with a file-backed sink reproduced the timeout. Together with the successful unsandboxed handshakes below, this points to the sandbox's nested-process/AnyIO worker behavior rather than a SkillScout protocol defect. No timeout increase or server workaround was applied.

### Clean wheel install and SDK client

The wheel was built, then a fresh environment was synchronized from the checked-in lockfile and overlaid with the built wheel:

```bash
UV_CACHE_DIR=/tmp/skillscout-v3-uv-cache uv build --out-dir /tmp/skillscout-v3-wheel-verify
UV_CACHE_DIR=/tmp/skillscout-v3-uv-cache UV_PROJECT_ENVIRONMENT=/tmp/skillscout-v3-clean-env-verify uv sync --all-extras --no-editable --locked
UV_CACHE_DIR=/tmp/skillscout-v3-uv-cache uv pip install --python /tmp/skillscout-v3-clean-env-verify/bin/python --no-deps /tmp/skillscout-v3-wheel-verify/skillscout-0.1.0-py3-none-any.whl
/tmp/skillscout-v3-clean-env-verify/bin/python /tmp/skillscout-v3-verify.py /tmp/skillscout-v3-clean-env-verify
```

The SDK-backed verification used `mcp.Client` with the supported `stdio_client` transport and a file-backed stderr capture. The isolated minimal SDK server initialized in **0.570 s** in auto mode (protocol `2026-07-28`) and **0.519 s** in legacy mode (protocol `2025-11-25`); each exposed `ping`, returned `pong`, wrote zero stderr bytes, and exited `0` on shutdown.

The installed wheel loaded from `/tmp/skillscout-v3-clean-env-verify/lib/python3.12/site-packages/skillscout` with SDK `2.3.0`. The installed `skillscout mcp` command initialized in **0.728 s** in auto mode, exposed `doctor`, `inspect_skill`, and `prepare_task`, and returned a schema-valid successful `doctor` result and `NO_SKILL_NEEDED` for a routine typo task. Invalid `prepare_task` and `inspect_skill` inputs returned structured `invalid_input` errors. With `SKILLSCOUT_CATALOG_URL=https://127.0.0.1:1` (a local no-listener endpoint), catalog-backed preparation returned `status: error`, `decision: null`, `error: catalog_unavailable`; valid-ID inspection returned the same operational error. These failures were not reported as `NO_SKILL_NEEDED`. All SkillScout process runs wrote zero stderr bytes and recorded exit code `0`. Successful SDK initialization, discovery, and calls also confirmed stdout carried parseable MCP protocol messages without startup text. This used no live catalog, credentials, network listener, installation of a skill, or skill execution.

### Regression and style checks

```bash
.venv/bin/python -m pytest -q
```

Result: **107 passed, 0 failed, 0 skipped**.

The full-tree checks were:

```bash
UV_CACHE_DIR=/tmp/skillscout-v3-uv-cache uv run --with ruff ruff check src tests
UV_CACHE_DIR=/tmp/skillscout-v3-uv-cache uv run --offline --with ruff ruff format --check src tests
```

`ruff check src tests` reported **29 findings** outside the seven MCP implementation/test files checked below. The full-tree format check reported **21 files would be reformatted, 12 already formatted**. Focused lint and format checks on those seven files passed:

```bash
UV_CACHE_DIR=/tmp/skillscout-v3-uv-cache uv run --offline --with ruff ruff check src/skillscout/application.py src/skillscout/mcp_models.py src/skillscout/mcp_server.py src/skillscout/ranking.py src/skillscout/evidence_explanations.py src/skillscout/evidence_packet.py tests/test_mcp_server.py
UV_CACHE_DIR=/tmp/skillscout-v3-uv-cache uv run --offline --with ruff ruff format --check src/skillscout/application.py src/skillscout/mcp_models.py src/skillscout/mcp_server.py src/skillscout/ranking.py src/skillscout/evidence_explanations.py src/skillscout/evidence_packet.py tests/test_mcp_server.py
git diff --check
```

Ruff was run temporarily and was not added to project dependencies. The repository has no configured type-check command. The existing CLI regression suite ran as part of pytest; no CLI, policy, dependency, transport, or installation code was changed for this verification.

## SkillScout v2 evidence packet update

This earlier v2 snapshot records the state before the stdio handshake diagnosis above.

**Checked:** 2026-10-20. This snapshot covers actionable evidence explanations and task-scoped MCP excerpts.

### Tests, lint, and format

```bash
.venv/bin/python -m pytest -q
```

Result: **107 passed, 0 failed, 0 skipped**. The suite uses fixtures and mocked catalog transports; it does not require credentials or live catalog access.

Ruff was not installed or declared in the project dev extra, so it was run temporarily from the package cache without changing project dependencies:

```bash
UV_CACHE_DIR=/tmp/skillscout-v2-uv-cache uv run --with ruff ruff check src/skillscout/application.py src/skillscout/mcp_models.py src/skillscout/mcp_server.py src/skillscout/ranking.py src/skillscout/evidence_explanations.py src/skillscout/evidence_packet.py tests/test_mcp_server.py
UV_CACHE_DIR=/tmp/skillscout-v2-uv-cache uv run --with ruff ruff format --check src/skillscout/application.py src/skillscout/mcp_models.py src/skillscout/mcp_server.py src/skillscout/ranking.py src/skillscout/evidence_explanations.py src/skillscout/evidence_packet.py tests/test_mcp_server.py
```

Both passed: Ruff reported all checks passed and seven files already formatted.

### Build, CLI, and MCP

```bash
UV_CACHE_DIR=/tmp/skillscout-v2-uv-cache uv build --out-dir /tmp/skillscout-v2-final2-wheel
UV_CACHE_DIR=/tmp/skillscout-v2-uv-cache uv pip install --target /tmp/skillscout-v2-final2-target --no-deps /tmp/skillscout-v2-final2-wheel/skillscout-0.1.0-py3-none-any.whl
```

The build produced the source archive and wheel. The wheel was installed into a temporary target using the runtime dependencies already present in the project virtual environment; this was not a fresh dependency-resolution install.

Installed-wheel checks from `/tmp` confirmed the package loaded from `/tmp/skillscout-v2-final2-target`, MCP tool discovery returned `doctor`, `inspect_skill`, and `prepare_task`, and a routine task returned `NO_SKILL_NEEDED`. Starting the installed `skillscout mcp` process left it running with empty stdout and stderr before a client connected. In-process MCP SDK tests also passed in the full suite.

The SDK `stdio_client` initialization did **not** complete in this environment: `initialize` timed out after five seconds for both the installed SkillScout server and a minimal MCP SDK stdio server. This indicates the local stdio test harness/runtime could not complete the wire handshake; a successful end-to-end stdio handshake is not claimed here.

These CLI commands were run:

```bash
PYTHONPATH=src .venv/bin/python -m skillscout --help
PYTHONPATH=src .venv/bin/python -m skillscout --version
PYTHONPATH=src .venv/bin/python -m skillscout doctor
PYTHONPATH=src .venv/bin/python -m skillscout recommend 'Fix a typo in README' --format json
PYTHONPATH=src .venv/bin/python -m skillscout inspect owner/../repo/skill --format json
```

Help, version, doctor, and routine recommendation exited 0; the routine recommendation was `NO_SKILL_NEEDED`. The invalid inspect ID returned the versioned `input_error` JSON and exited 2. Existing CLI behavior remained covered by the full regression suite.

### Catalog authentication

An unauthenticated read-only request to the documented skills.sh search route returned **HTTP 401**, JSON error code `authentication_required`. The initial sandboxed probe failed DNS; the same request was retried with network permission and returned the 401 response. No credentials were available, so authorized search/detail/audit behavior was not tested.

The current [skills.sh API reference](https://www.skills.sh/docs/api) documents bearer authentication for API requests and explicitly requires it for audits; no unauthenticated search/detail path is documented. It documents Vercel OIDC as the credential path. Vercel's [OIDC setup documentation](https://vercel.com/docs/oidc) describes linking a local project and using `vercel env pull` for a short-lived `VERCEL_OIDC_TOKEN`. SkillScout reads that variable from its process environment; it does not load `.env.local` or refresh the token. Fixture-based tests remain independent of credentials and external access.

No dependency, lockfile, or installation behavior changes were made. No live catalog result, skill content, hash, or audit evidence was verified. No install command was executed.

---

## Earlier verification snapshot (2026-10-18)

**Checked:** 2026-10-18. This records commands run against the current working tree. The pre-existing edits in the evaluation docs, implementation notes, verification notes, `pyproject.toml`, and catalog/LLM tests were reviewed and preserved. `uv.lock` was generated during this verification.

## Test suite

The original ten failures were in `tests/test_catalog.py` and `tests/test_llm.py`. The baseline `pyproject.toml` declared `respx>=0.21,<1.0` in the `dev` extra, and the tests used its `respx.mock` API. In the effective test environment, `respx` resolved to an empty namespace directory: `find_spec("respx").origin` was `None`, there was no installed `respx` distribution/version, and `hasattr(respx, "mock")` was false. This indicates the optional test dependency had not been installed; it does not indicate a bad version pin or an incompatible API. The existing HTTPX `MockTransport` pattern already covered the same adapter behaviors, so the catalog and LLM tests were moved to that transport, their intent and assertions were retained, and the unused `respx` dev dependency was removed.

Exact command, run from `/tmp` so tests import the installed wheel rather than `src/`:

```bash
/tmp/skillscout-clean-env/bin/python -m pytest -q --override-ini='pythonpath=' '/home/athulvr/Documents/My Projects/SkillScraper/tests'
```

Result: **64 passed, 0 failed, 0 skipped, 0 collection errors** (`64 passed in 0.30s`). The imported package path was `/tmp/skillscout-clean-env/lib/python3.12/site-packages/skillscout/__init__.py`.

The test environment was Python 3.12.3 with HTTPX 0.28.1, Pydantic 2.14.1, Rich 14.3.4, Typer 0.27.3, and pytest 9.1.1. Deterministic transport tests cover response validation, stable-ID matching, detail hashes, audit revision binding, authentication/missing-audit states, retry bounds and `Retry-After`. No live requests are made by pytest.

## Lockfile, wheel, and clean installation

The project uses Hatchling as its PEP 517 backend (`hatchling.build`, `hatchling>=1.24,<2.0`). Its runtime dependencies are HTTPX, Pydantic, Rich, and Typer; the `dev` extra adds pytest. Wheel metadata confirms these requirements, `Requires-Python: >=3.12`, and the `skillscout = skillscout.cli:app` entry point.

These commands succeeded:

```bash
UV_CACHE_DIR=/tmp/skillscout-final-uv-cache uv lock
UV_CACHE_DIR=/tmp/skillscout-final-uv-cache uv build --out-dir /tmp/skillscout-release-wheel
UV_CACHE_DIR=/tmp/skillscout-final-uv-cache UV_PROJECT_ENVIRONMENT=/tmp/skillscout-clean-env uv sync --all-extras --no-editable
```

`uv lock` generated `uv.lock`. The build produced both `skillscout-0.1.0.tar.gz` and `skillscout-0.1.0-py3-none-any.whl`. The sync created a fresh environment and installed SkillScout plus its runtime and development dependencies (23 packages). PyPI access succeeded for this run.

For comparison, this fresh environment installed only the wheel:

```bash
UV_CACHE_DIR=/tmp/skillscout-final-uv-cache uv venv --python /usr/bin/python3 /tmp/skillscout-nodeps-env
UV_CACHE_DIR=/tmp/skillscout-final-uv-cache uv pip install --python /tmp/skillscout-nodeps-env/bin/python --no-deps /tmp/skillscout-release-wheel/skillscout-0.1.0-py3-none-any.whl
/tmp/skillscout-nodeps-env/bin/skillscout --help
```

The install command succeeded, but `--help` exited 1 with `ModuleNotFoundError: No module named 'typer'`. `--no-deps` installs the wheel alone; it does not install the required runtime libraries. The clean `uv sync --all-extras --no-editable` installation succeeded with all declared dependencies, and the suite and CLI checks below ran in that environment.

## Installed CLI

All of these commands used `/tmp/skillscout-clean-env/bin/skillscout` and ran from `/tmp`:

```bash
/tmp/skillscout-clean-env/bin/skillscout --help
/tmp/skillscout-clean-env/bin/skillscout --version
/tmp/skillscout-clean-env/bin/skillscout doctor
/tmp/skillscout-clean-env/bin/skillscout recommend 'Write a simple unit test for one validation function.' --project '/home/athulvr/Documents/My Projects/SkillScraper' --format text
/tmp/skillscout-clean-env/bin/skillscout recommend 'Fix a typo in the README installation section.' --project '/home/athulvr/Documents/My Projects/SkillScraper' --format json
/tmp/skillscout-clean-env/bin/skillscout inspect owner/../repo/skill --project '/home/athulvr/Documents/My Projects/SkillScraper' --format json
```

`--help`, `--version` (`SkillScout 0.1.0`), `doctor`, text recommendation, and JSON recommendation exited 0. The two recommendations returned `NO_SKILL_NEEDED` through the local fast path. The invalid inspect ID exited 2 and returned a versioned JSON `input_error`. Doctor showed that catalog credentials and the optional LLM path were not configured.

## Live skills.sh integration

The explicit smoke command reached the API but could not authenticate:

```bash
/tmp/skillscout-clean-env/bin/skillscout recommend 'Add OAuth 2.1 login and token refresh to the FastAPI service.' --project '/home/athulvr/Documents/My Projects/SkillScraper' --format json
```

It exited 3 with a JSON `catalog_authentication_error`. A direct unauthenticated search request returned HTTP 401, `content-type: application/json`, and `{"error":"authentication_required",...}` requiring a Vercel OIDC bearer token. The [skills.sh API reference](https://www.skills.sh/docs/api) documents authentication and the search/detail/audit response shapes; its audit endpoint also requires authentication.

No search results were returned. Actual search/detail payloads, catalog candidate identity, detail hash, audit availability and revision matching, and a live proposed install command therefore remain unverified. A live retry was not exercised because the request ended with a non-retryable 401. The deterministic suite verifies bounded retries and command generation. **No proposed install command was executed.**

## Recommendation evaluation

The outcomes and relevance criteria were recorded in [evaluation](evaluation/README.md) before this verification's recommendation measurements. The live authentication failure prevented SkillScout's catalog-backed path and the ordinary search baseline from running on the 17 catalog-dependent cases; the baseline was not run on the two routine cases either. Task records identify those measurements as blocked by catalog authentication. No candidate hypotheses were treated as catalog IDs.

The two routine no-skill tasks ran three times each using the installed package. All six executions returned `NO_SKILL_NEEDED`, with no catalog or provider calls. The validation-test task measured 176.02, 177.25, and 174.03 ms (median 176.02 ms); the README typo task measured 173.97, 174.00, and 178.87 ms (median 174.00 ms). This is 2/2 correct expected no-skill task cases in a small local fast-path subset, not broad recommendation evaluation. The synthetic critical-risk case is covered by deterministic policy tests only.

Top-three precision/recall, ordinary-search comparison, catalog-backed no-skill accuracy, and incorrect recommendations on catalog tasks remain unmeasured. The 20-task dataset is exploratory and does not support statistical significance or a general quality claim. See [task-level records](evaluation/tasks.jsonl).
