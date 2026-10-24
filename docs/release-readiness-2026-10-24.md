# SkillScout release readiness — 2026-10-24

## Distribution and first run

Built the wheel with the existing Hatchling configuration and installed it outside the checkout in a fresh Python 3.12.3 environment. The final artifact is `/tmp/skillscout-release-mcp-hrobb58j/final-dist/skillscout-0.1.0-py3-none-any.whl`.

```bash
uv build --wheel --out-dir /tmp/skillscout-release-mcp-hrobb58j/final-dist
python3 -m venv /tmp/skillscout-release-mcp-hrobb58j/venv
UV_PROJECT_ENVIRONMENT=/tmp/skillscout-release-mcp-hrobb58j/venv uv sync --locked --all-extras --no-install-project
/tmp/skillscout-release-mcp-hrobb58j/venv/bin/python -m pip install --no-index --force-reinstall --no-deps /tmp/skillscout-release-mcp-hrobb58j/final-dist/skillscout-0.1.0-py3-none-any.whl
/tmp/skillscout-release-mcp-hrobb58j/venv/bin/python -m pip check
/tmp/skillscout-release-mcp-hrobb58j/venv/bin/skillscout --version
/tmp/skillscout-release-mcp-hrobb58j/venv/bin/skillscout doctor
```

Results: wheel build succeeded; pip reported no broken requirements; version output was `SkillScout 0.1.0`; doctor exited successfully and reported that catalog credentials were not configured. Doctor remained local and made no network call. The wheel has 40 members, `Requires-Python: >=3.12`, the declared runtime dependencies, both console entry points (`skillscout` and `skillscout-mcp`), and the MCP, typed-package, and evaluation modules required at runtime. Its packaged README includes the updated installation guidance.

SkillScout is not published on PyPI. Developers need a project-supplied wheel, Python 3.12+, and access to their configured package index for any dependencies not already installed. Installation and upgrade commands are in the [README](../README.md).

## MCP stdio integration

The test uses the real MCP Python client SDK (`mcp` 2.3.0: `stdio_client` and `ClientSession`) against the installed wheel from a temporary working directory, plus raw JSON-RPC over subprocess stdin/stdout:

```bash
.venv/bin/python scripts/mcp_stdio_smoke.py --venv /tmp/skillscout-release-mcp-hrobb58j/venv --timeout 25
```

It passed with protocol version `2025-11-25`. Tool discovery returned `prepare_task`, `inspect_skill`, and `doctor`; the schemas expose the expected properties, required fields, and empty `doctor` input. The normal installed process returned `NO_SKILL_NEEDED` for a routine task, handled invalid arguments, and continued to answer `doctor`. A separate fixture server exercised successful `USE` and `REVIEW` results, a mocked missing-credential error, and a mocked malformed-detail error whose synthetic secret marker did not appear in results or stderr. The raw session recovered after malformed JSON, processed `tools/list` and `doctor` sequentially, and exited with code 0 on EOF. The test enforces timeouts and cleanup; a process scan found no remaining installed-server subprocesses. All final smoke paths had zero stderr bytes, and the normal path asserts that it invokes no HTTP transport.

Claude Code and Codex CLI executables are installed in this environment, but no client-app session was run. A live session could invoke a model and would require an isolated client configuration. The installed distribution was tested through the MCP SDK and raw stdio protocol; no live Claude/Codex app integration is claimed. Example configuration for both clients is documented without modifying their configuration.

## Catalog failure paths and fast path

Fixture and mocked-transport coverage includes missing and rejected credentials, connection refusal, read and total-request timeouts, 429 rate limits, 5xx responses, permanent HTTP errors, malformed/oversized responses, retry bounds, and incomplete pagination. Checks confirm that authentication and permanent HTTP errors are not retried, transient retries are bounded, incomplete empty searches remain `REVIEW`, inspection failures remain operational errors, and unknown safety/compatibility evidence does not become a positive claim. MCP-facing errors give next steps without returning upstream exception text or credential values. The final fixture smoke also checked that a synthetic credential in an upstream exception did not reach tool output or diagnostics.

The reproducible benchmark is `scripts/benchmark_paths.py`; it uses 30 samples per task across three task phrasings in each group, excludes two warmups, and makes no network requests. First baseline on Python 3.12.3, Linux x86_64:

| Path | Samples | Median | p95 | Min | Max | Config loads / catalog requests |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Routine local tasks | 90 | 0.008 ms | 0.012 ms | 0.005 ms | 0.241 ms | 0 / 0 |
| Catalog-backed pipeline with in-memory fixture | 90 | 0.307 ms | 0.348 ms | 0.292 ms | 0.357 ms | 90 config loads; 90 each search/detail/audit |

The fixture timings measure application work only, not network latency or process startup. The local path loaded no configuration and created no catalog client. This records a baseline; no performance target or ranking change was introduced.

## Documentation and tests

README and troubleshooting now cover clean wheel installation and upgrade, Windows entry-point paths, runtime requirements, MCP startup behavior, Claude Code and Codex CLI configuration, local versus catalog-backed operations, safe credential injection, and authentication/network/timeout/incomplete-search errors. Client configuration examples are instructions only; SkillScout does not edit client settings.

Focused command: `.venv/bin/python -m pytest -q tests/test_catalog_transport.py tests/test_routine_fastpath.py tests/test_mcp_server.py` — **87 passed**.

Full suite: `.venv/bin/python -m pytest -q` — **257 passed**. `git diff --check` passed. The calibration dataset was not changed and pending examples were not used for tuning.

## No-live-catalog constraint incident

An early version of the stdio smoke called `inspect_skill` without credentials. That path attempted an unauthenticated search followed by a detail request. Across seven pre-correction smoke runs, the server made 14 GET requests to the live skills.sh API (seven search and seven detail requests); all returned HTTP 401. No catalog data or credentials were returned. This was an unintended violation of the no-live-request constraint. The test was corrected to inject missing-credential and inspection failures through a local fixture, and the final smoke asserts that its normal installed-server path invokes no HTTP transport. No further catalog calls were made after that correction.

## Remaining limits

- No Claude Code or Codex CLI session was started; only the real MCP SDK client and raw stdio protocol were exercised.
- No live authenticated catalog behavior was tested. Developers who need catalog-backed recommendation or inspection must provide an authorized credential to the SkillScout process through a secure environment/secret manager.
- The benchmark's catalog timings are in-memory fixture timings, not production network performance.
