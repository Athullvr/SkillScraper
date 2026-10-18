# Starting repository constraints

Before implementation, the repository contained no application source, package metadata, README, test suite, or usable Git metadata. The `.git` entry was an empty directory placeholder, so there is no history or branch state to preserve and no commit operation is available. The environment provides Python 3.12.3 as `python3` and uv 0.12.10 as `uv`.

The project is being established as a `src/`-layout Python package. Network behavior in tests is fixture-backed; a live catalog smoke test is a separate manual action documented in `manual-catalog-demo.md`.

