# Starting repository constraints

Before implementation, the repository contained no application source, package metadata, README, test suite, or usable Git metadata. The `.git` entry was an empty directory placeholder, so there is no history or branch state to preserve and no commit operation is available. The environment provides Python 3.12.3 as `python3` and uv 0.12.10 as `uv`.

The project is being established as a `src/`-layout Python package. Network behavior in tests is fixture-backed; a live catalog smoke test is a separate manual action documented in `manual-catalog-demo.md`.

During this implementation, uv could not resolve or download PyPI packages because DNS/network access was unavailable and its cache had no project dependencies. As a result, a valid `uv.lock` could not be generated and `uv build --offline` could not fetch the Hatchling build backend. The direct live catalog smoke attempt also failed on network access after bounded retries. These are environment limitations, not skipped success checks; rerun `uv lock`, `uv sync --all-extras`, packaging checks, and the manual demo in a network-enabled environment.

