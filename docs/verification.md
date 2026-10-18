# Verification snapshot

**Checked:** 2026-10-18. This snapshot records what ran in the current environment; successful checks are not inferred from older results.

## Automated tests

The full command collected 63 tests: **53 passed, 10 failed, 0 skipped, and 0 collection errors**. The ten failures were existing `tests/test_catalog.py` and `tests/test_llm.py` tests calling `respx.mock`. The available `respx` directory was a pycache-backed namespace with no `mock` API. The project's declared `dev` dependency could not be installed because PyPI DNS lookup failed.

New deterministic transport tests use HTTPX `MockTransport` and do not require respx:

```text
tests/test_catalog_transport.py tests/test_llm_transport.py: 19 passed
```

The other 52 tests passed when the two original respx modules were excluded. The full-suite failure is still a release blocker until the declared development environment can be installed and the catalog/LLM modules pass as written.

## Packaging

- `pyproject.toml` parsed successfully. It declares Python `>=3.12`, HTTPX, Pydantic, Rich, Typer, a `dev` extra with pytest and respx, and Hatchling as the PEP 517 backend.
- `uv sync --extra dev` failed at PyPI DNS resolution. `uv lock --offline` could not resolve uncached HTTPX, so there is no lockfile.
- `uv build --offline` could not find Hatchling in uv's cache. Hatchling 1.32.0 from an existing local environment built `dist/skillscout-0.1.0-py3-none-any.whl` successfully.
- That wheel installed into a clean temporary venv with `pip install --no-deps`; `--help`, `--version`, `doctor`, and routine text/JSON recommendations passed. Imports were supplied from existing local environments, so this proves wheel/entry-point behavior, not a clean install of all declared dependencies.

## Network and evaluation

The bounded `uv sync` attempt failed with `dns error: failed to lookup address information` for `pypi.org`. No live catalog or provider request was made afterward. The 20-task evaluation dataset and comparison protocol are in [evaluation](evaluation/README.md). Two local routine fast-path cases returned `NO_SKILL_NEEDED` in 214.69 ms and 214.60 ms in single process-wall-time samples, with zero LLM cost; no live ranking or ordinary-search baseline was measured, and the two cases are too small to establish general no-skill accuracy.

## Commands for a network-enabled environment

```bash
uv sync --all-extras
uv run pytest
uv run skillscout --help
uv run skillscout --version
uv run skillscout doctor
uv run skillscout recommend "Add OAuth login to a FastAPI service" --project . --format json
uv run skillscout inspect vercel-labs/skills/find-skills --format json
uv build
```

Run the live catalog commands only as the explicit manual check described in [manual-catalog-demo.md](manual-catalog-demo.md). They are not part of pytest.
