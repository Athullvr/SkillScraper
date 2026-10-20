# Verification snapshots

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
