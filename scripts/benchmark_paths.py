#!/usr/bin/env python3
"""Measure local routine and catalog-backed recommendation paths with fixtures.

Run with ``uv run python scripts/benchmark_paths.py``. Catalog calls are served
by an in-memory fixture; this script never loads environment configuration or
opens a network connection for routine tasks.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import time
from pathlib import Path
from typing import Any

from skillscout.application import prepare_recommendation
from skillscout.config import Config
from skillscout.models import CatalogSkill, ProjectSnapshot, SkillDetail, SkillFile


ROUTINE_TASKS = (
    "Fix a spelling typo in the README.",
    "Rename a local variable in a Python helper function.",
    "Update the docstring for a helper function.",
)
CATALOG_TASKS = (
    "Implement OAuth authentication in FastAPI.",
    "Build a FastAPI login flow using OAuth.",
    "Add OAuth login support to a FastAPI API.",
)


def _config() -> Config:
    return Config(
        catalog_base_url="https://catalog.fixture.invalid",
        catalog_token=None,
        candidate_limit=3,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_response_bytes=2_000_000,
        retry_count=0,
        llm_api_key=None,
        llm_base_url="https://llm.fixture.invalid/v1",
        llm_model="benchmark-fixture",
        llm_send_task=False,
    )


SKILL = CatalogSkill(
    id="fixture/fastapi-oauth",
    name="FastAPI OAuth implementation",
    source="fixture",
    slug="fastapi-oauth",
    description="Implement OAuth authentication and login flows in FastAPI applications.",
)
DETAIL = SkillDetail(
    id=SKILL.id,
    source=SKILL.source,
    slug=SKILL.slug,
    hash="benchmark-fixture-revision",
    files=[
        SkillFile(
            path="SKILL.md",
            contents=(
                "# FastAPI OAuth\n\n"
                "Implement OAuth authentication and login flows in FastAPI applications.\n"
            ),
        )
    ],
)


class FixtureCatalog:
    def __init__(self, counters: dict[str, int]) -> None:
        counters["catalog_instances"] += 1
        self.counters = counters

    def __enter__(self) -> FixtureCatalog:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def search(self, _query: str, *, limit: int) -> list[CatalogSkill]:
        self.counters["search_requests"] += 1
        return [SKILL][:limit]

    def get_detail(self, _candidate: CatalogSkill) -> SkillDetail:
        self.counters["detail_requests"] += 1
        return DETAIL

    def get_audits(self, _candidate: CatalogSkill) -> tuple[list[Any], str, list[str]]:
        self.counters["audit_requests"] += 1
        return [], "unavailable", ["Fixture benchmark does not provide external audit evidence."]


def _measure(
    tasks: tuple[str, ...], samples_per_task: int, project: Path, *, catalog_backed: bool
) -> tuple[list[float], dict[str, int]]:
    counters = {
        "config_loads": 0,
        "catalog_instances": 0,
        "search_requests": 0,
        "detail_requests": 0,
        "audit_requests": 0,
    }
    snapshot = ProjectSnapshot(path=str(project))

    def config_loader() -> Config:
        counters["config_loads"] += 1
        if not catalog_backed:
            raise AssertionError("routine path unexpectedly loaded catalog configuration")
        return _config()

    def catalog_factory(_config: Config) -> FixtureCatalog:
        if not catalog_backed:
            raise AssertionError("routine path unexpectedly created a catalog client")
        return FixtureCatalog(counters)

    args = {
        "project_path": project,
        "allow_llm": False,
        "config_loader": config_loader,
        "project_context_reader": lambda _path: snapshot,
        "catalog_client_factory": catalog_factory,
    }

    # Warm the interpreter and imports before collecting the baseline.
    warmup = prepare_recommendation(tasks[0], **args)
    expected = "REVIEW" if catalog_backed else "NO_SKILL_NEEDED"
    if warmup.decision.value != expected:
        raise RuntimeError(f"unexpected {expected} benchmark fixture decision: {warmup.decision}")
    counters.update({key: 0 for key in counters})

    latencies_ms: list[float] = []
    for task in tasks:
        for _ in range(samples_per_task):
            started = time.perf_counter_ns()
            result = prepare_recommendation(task, **args)
            latencies_ms.append((time.perf_counter_ns() - started) / 1_000_000)
            if result.decision.value != expected:
                raise RuntimeError(f"unexpected benchmark decision: {result.decision}")
    return latencies_ms, counters


def _summary(samples_ms: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples_ms)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return {
        "samples": len(ordered),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(p95, 3),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
        "mean_ms": round(statistics.fmean(ordered), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-per-task", type=int, default=30)
    args = parser.parse_args()
    if args.samples_per_task < 1 or args.samples_per_task > 1_000:
        parser.error("--samples-per-task must be between 1 and 1000")

    project = Path.cwd()
    local_samples, local_counts = _measure(
        ROUTINE_TASKS, args.samples_per_task, project, catalog_backed=False
    )
    catalog_samples, catalog_counts = _measure(
        CATALOG_TASKS, args.samples_per_task, project, catalog_backed=True
    )
    print(
        json.dumps(
            {
                "benchmark": "in-process recommendation path baseline",
                "python": platform.python_version(),
                "platform": platform.platform(),
                "warmup_samples_excluded": 2,
                "routine_local": {
                    "tasks": list(ROUTINE_TASKS),
                    "latency": _summary(local_samples),
                    "operations": local_counts,
                },
                "catalog_backed_fixture": {
                    "tasks": list(CATALOG_TASKS),
                    "latency": _summary(catalog_samples),
                    "operations": catalog_counts,
                    "note": "In-memory catalog fixture; not a network latency measurement.",
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
