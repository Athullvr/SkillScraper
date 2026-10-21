from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

from skillscout.evaluation.models import (
    ArmStatus,
    InvocationFailure,
    InvocationNotStarted,
    TerminationStatus,
)
from skillscout.evaluation.protocol import ArmName, build_schedule, load_dataset
from skillscout.evaluation.recorder import EvaluationRecorder, LookupRequest, RunModeError
from skillscout.evaluation.runtime import RuntimeAdapter
from skillscout.config import Config
from skillscout.evaluation.validation import validate_records


ROOT = Path(__file__).parents[1]
TASKS = load_dataset(ROOT / "docs/evaluation/v1/tasks.jsonl").eligible_tasks


def _worker_command(script: str, *args: str) -> list[str]:
    return [sys.executable, "-c", textwrap.dedent(script), *args]


def test_runtime_adapter_keeps_primary_input_boundary_and_revision_linkage(tmp_path: Path):
    observed = tmp_path / "worker-request.json"
    script = r"""
import json, sys
request = json.loads(sys.stdin.buffer.readline())
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"request":request,"remaining_s":request["worker_deadline_monotonic_s"]-__import__("time").monotonic()}, stream)
print(json.dumps({"status":"ok","result":{
    "decision":"REVIEW",
    "partial":False,
    "internal_attempt_count":None,
    "termination_status":"confirmed",
    "termination_evidence":"structured terminal response",
    "candidates":[{"stable_id":"owner/repo/skill","source_slug":"skill","rank":1,
                   "content_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                   "revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                   "source_url":None}]
}}))
"""
    adapter = RuntimeAdapter(worker_command=_worker_command(script, str(observed)))
    dispatch_deadline = time.monotonic() + 300
    invocation_deadline = time.monotonic() + 240
    result = adapter.invoke_skillscout(
        {"task": "Use the documented framework.", "project_path": "/tmp/opaque-context"},
        240,
        not_after_monotonic_s=dispatch_deadline,
        invocation_deadline_monotonic_s=invocation_deadline,
    )
    logged = json.loads(observed.read_text(encoding="utf-8"))
    message = logged["request"]
    assert set(message["payload"]) == {"task", "project_path"}
    assert message["payload"]["task"] == "Use the documented framework."
    assert message["operation"] == "skillscout"
    assert message["worker_deadline_monotonic_s"] <= message["dispatch_deadline_monotonic_s"]
    assert message["worker_deadline_monotonic_s"] <= message["invocation_deadline_monotonic_s"]
    assert 0 < logged["remaining_s"] <= 240
    assert result.response.candidates[0].stable_id == "owner/repo/skill"
    assert result.response.candidates[0].arm_content_hash == result.response.candidates[0].arm_revision
    assert result.termination_status is TerminationStatus.CONFIRMED


def test_runtime_adapter_refuses_a_lookup_after_shared_deadline_without_dispatch(tmp_path: Path):
    marker = tmp_path / "should-not-exist"
    script = r"""
import json, sys
json.loads(sys.stdin.buffer.readline())
open(sys.argv[1], "w").write("dispatched")
print(json.dumps({"status":"ok","result":{"success":True}}))
"""
    adapter = RuntimeAdapter(worker_command=_worker_command(script, str(marker)))
    recorder = EvaluationRecorder(adapter.handlers, lookup=adapter.lookup)
    with pytest.raises(RunModeError, match="production runtime handlers"):
        recorder.run_offline(
            TASKS,
            {task.task_id: f"/tmp/opaque-{task.eligible_index}" for task in TASKS},
            {task.task_id: f"{task.eligible_index:064x}" for task in TASKS},
        )
    request = LookupRequest(
        arm=ArmName.SKILLSCOUT,
        candidate_id="owner/repo/skill",
        rank=1,
        kind="detail",
        arm_content_hash=None,
        arm_revision=None,
        timeout_cap_s=1.0,
        deadline_monotonic_s=time.monotonic() - 0.01,
    )
    with pytest.raises(InvocationNotStarted, match="lookup_deadline_expired"):
        adapter.lookup_evidence(request)
    assert not marker.exists()


def test_runtime_timeout_terminates_local_worker_and_quarantines_adapter(tmp_path: Path):
    marker = tmp_path / "worker.pid"
    script = r"""
import json, os, sys, time
json.loads(sys.stdin.buffer.readline())
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    stream.write(str(os.getpid()))
    stream.flush()
time.sleep(20)
"""
    adapter = RuntimeAdapter(
        worker_command=_worker_command(script, str(marker)),
        terminate_grace_s=0.1,
    )
    plan = build_schedule(TASKS)[0]
    recorder = EvaluationRecorder(adapter.handlers, invocation_timeout_s=1)
    pair = recorder.record_pair(
        plan,
        TASKS[0],
        "/tmp/opaque",
        "a" * 64,
        run_id="runtime-timeout",
    )
    failed = pair.arms[plan.arm_order[0]]
    assert failed.status is ArmStatus.FAILED
    assert failed.error_category == "invocation_timeout"
    assert failed.underlying_termination is TerminationStatus.NOT_CONFIRMED
    assert "upstream request cancellation is not observable" in failed.termination_evidence
    assert pair.run_stop_required is True
    assert pair.arms[plan.arm_order[1]].status is ArmStatus.NOT_RUN
    assert adapter.quarantined is True
    assert adapter.last_local_termination_confirmed is True
    assert adapter.last_worker_exitcode is not None
    pid = int(marker.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with pytest.raises(InvocationFailure, match="runtime_adapter_quarantined"):
        adapter.invoke_ordinary_search({"query": "offline", "limit": 3}, 240)


def test_runtime_adapter_maps_terminal_worker_errors_without_fabricating_responses():
    script = r"""
import json
json.loads(__import__("sys").stdin.buffer.readline())
print(json.dumps({"status":"error","category":"catalog_unavailable",
                  "termination_status":"unknown",
                  "termination_evidence":"request termination not observable"}))
"""
    adapter = RuntimeAdapter(worker_command=_worker_command(script))
    with pytest.raises(InvocationFailure) as error:
        adapter.invoke_ordinary_search({"query": "offline", "limit": 3}, 240)
    assert error.value.category == "catalog_unavailable"
    assert error.value.termination_status is TerminationStatus.UNKNOWN
    assert adapter.quarantined is True


def test_runtime_adapter_quarantines_a_valid_response_with_uncertain_termination():
    script = r"""
import json
json.loads(__import__("sys").stdin.buffer.readline())
print(json.dumps({"status":"ok","result":{
    "decision":"REVIEW","partial":False,"internal_attempt_count":None,
    "termination_status":"unknown",
    "termination_evidence":"nested request termination was not observable",
    "candidates":[]
}}))
"""
    adapter = RuntimeAdapter(worker_command=_worker_command(script))
    response = adapter.invoke_skillscout(
        {"task": "Offline task.", "project_path": "/tmp/opaque"},
        240,
    )
    assert response.termination_status is TerminationStatus.UNKNOWN
    assert adapter.quarantined is True
    with pytest.raises(InvocationFailure, match="runtime_adapter_quarantined"):
        adapter.invoke_ordinary_search({"query": "Offline task.", "limit": 3}, 240)


def test_runtime_worker_uses_the_existing_skillscout_mcp_operation(monkeypatch):
    from skillscout import mcp_server
    from skillscout.evaluation.runtime_worker import _skillscout
    from skillscout.models import Decision

    received = {}

    async def prepare_task(**kwargs):
        received.update(kwargs)
        return SimpleNamespace(
            status="success",
            decision=Decision.REVIEW,
            recommendations=[
                SimpleNamespace(
                    rank=1,
                    skill=SimpleNamespace(id="owner/repo/skill", slug="skill"),
                    evidence=SimpleNamespace(
                        content_hash="b" * 64,
                        evidence_status="complete",
                        external_audit_status="current",
                    ),
                )
            ],
        )

    monkeypatch.setattr(mcp_server, "prepare_task", prepare_task)
    payload = {"task": "Implement a handler.", "project_path": "/tmp/opaque"}
    result = _skillscout(payload, {"dispatch_deadline_monotonic_s": time.monotonic() + 10})
    assert received == payload
    assert result["status"] == "ok"
    assert result["result"]["decision"] == "REVIEW"
    assert result["result"]["candidates"][0]["stable_id"] == "owner/repo/skill"
    assert result["result"]["candidates"][0]["content_hash"] == "b" * 64
    assert result["result"]["termination_status"] == "confirmed"


def test_runtime_worker_does_not_confirm_termination_for_unavailable_nested_evidence(monkeypatch):
    from skillscout import mcp_server
    from skillscout.evaluation.runtime_worker import _skillscout
    from skillscout.models import Decision

    async def prepare_task(**_kwargs):
        return SimpleNamespace(
            status="success",
            decision=Decision.REVIEW,
            recommendations=[
                SimpleNamespace(
                    rank=1,
                    skill=SimpleNamespace(id="owner/repo/skill", slug="skill"),
                    evidence=SimpleNamespace(
                        content_hash="b" * 64,
                        evidence_status="incomplete",
                        external_audit_status="unavailable",
                    ),
                )
            ],
        )

    monkeypatch.setattr(mcp_server, "prepare_task", prepare_task)
    result = _skillscout(
        {"task": "Use a useful skill.", "project_path": "/tmp/opaque"},
        {"dispatch_deadline_monotonic_s": time.monotonic() + 10},
    )
    assert result["status"] == "ok"
    assert result["result"]["termination_status"] == "unknown"
    assert "request termination is not independently observable" in result["result"]["termination_evidence"]


def test_runtime_worker_keeps_baseline_to_one_search_attempt(monkeypatch):
    import skillscout.catalog
    import skillscout.config
    from skillscout.evaluation.runtime_worker import _ordinary_search
    from skillscout.models import CatalogSkill

    observed = {}
    config = Config(
        catalog_base_url="https://skills.test",
        catalog_token=None,
        candidate_limit=3,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_response_bytes=1024,
        retry_count=2,
        llm_api_key=None,
        llm_base_url="https://llm.test/v1",
        llm_model="model",
        llm_send_task=False,
    )

    class FakeCatalog:
        def __init__(self, runtime_config):
            observed["retry_count"] = runtime_config.retry_count

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def search(self, query, *, limit):
            observed["query"] = query
            observed["limit"] = limit
            return [
                CatalogSkill(
                    id="owner/repo/skill",
                    source="owner/repo",
                    slug="skill",
                    name="Example",
                )
            ]

    monkeypatch.setattr(skillscout.config, "load_config", lambda: config)
    monkeypatch.setattr(skillscout.catalog, "CatalogClient", FakeCatalog)
    result = _ordinary_search(
        {"query": "Implement a handler.", "limit": 3},
        {"dispatch_deadline_monotonic_s": time.monotonic() + 10},
    )
    assert observed == {"retry_count": 0, "query": "Implement a handler.", "limit": 3}
    assert result["result"]["internal_attempt_count"] == 1
    assert result["result"]["candidates"][0]["stable_id"] == "owner/repo/skill"


def test_runtime_worker_keeps_audit_unavailability_unknown(monkeypatch):
    import skillscout.catalog
    import skillscout.config
    from skillscout.evaluation.runtime_worker import _lookup
    from skillscout.models import SkillDetail, SkillFile

    config = Config(
        catalog_base_url="https://skills.test",
        catalog_token=None,
        candidate_limit=3,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_response_bytes=1024,
        retry_count=2,
        llm_api_key=None,
        llm_base_url="https://llm.test/v1",
        llm_model="model",
        llm_send_task=False,
    )

    class FakeCatalog:
        def __init__(self, runtime_config):
            assert runtime_config.retry_count == 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_audits(self, _skill):
            return [], "unavailable", ["network details are not returned"]

    monkeypatch.setattr(skillscout.config, "load_config", lambda: config)
    monkeypatch.setattr(skillscout.catalog, "CatalogClient", FakeCatalog)
    payload = {
        "arm": "skillscout",
        "candidate_id": "owner/repo/skill",
        "rank": 1,
        "kind": "audit",
        "arm_content_hash": "c" * 64,
        "arm_revision": "c" * 64,
    }
    request = {
        "operation": "audit_lookup",
        "lookup_deadline_monotonic_s": time.monotonic() + 10,
    }
    result = _lookup(payload, request)
    assert result["status"] == "ok"
    assert result["result"]["success"] is False
    assert result["result"]["audit_status"] == "unavailable"
    assert result["result"]["termination_status"] == "unknown"


def test_runtime_worker_links_detail_lookup_to_exact_candidate_revision(monkeypatch):
    import skillscout.application
    import skillscout.catalog
    import skillscout.config
    from skillscout.evaluation.runtime_worker import _lookup
    from skillscout.models import SkillDetail, SkillFile

    revision = "d" * 64
    detail = SkillDetail(
        id="owner/repo/skill",
        source="owner/repo",
        slug="skill",
        hash=revision,
        files=[SkillFile(path="SKILL.md", contents="A useful skill description." )],
    )
    config = Config(
        catalog_base_url="https://skills.test",
        catalog_token=None,
        candidate_limit=3,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        max_response_bytes=1024,
        retry_count=2,
        llm_api_key=None,
        llm_base_url="https://llm.test/v1",
        llm_model="model",
        llm_send_task=False,
    )

    class FakeCatalog:
        def __init__(self, runtime_config):
            assert runtime_config.retry_count == 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_detail(self, skill):
            assert skill.id == detail.id
            return detail

    monkeypatch.setattr(skillscout.config, "load_config", lambda: config)
    monkeypatch.setattr(skillscout.catalog, "CatalogClient", FakeCatalog)
    result = _lookup(
        {
            "arm": "ordinary_search",
            "candidate_id": detail.id,
            "rank": 1,
            "kind": "detail",
            "arm_content_hash": revision,
            "arm_revision": revision,
        },
        {"operation": "detail_lookup", "lookup_deadline_monotonic_s": time.monotonic() + 10},
    )
    assert result["status"] == "ok"
    assert result["result"]["returned_candidate_id"] == detail.id
    assert result["result"]["content_hash"] == revision
    assert result["result"]["revision_linkage"] == "matched"
    assert result["result"]["detail_sufficient_for_relevance"] is True


def test_uncertain_termination_on_a_returned_primary_result_stops_the_pair():
    from skillscout.evaluation.models import Decision, InvocationReturn, PrimaryResponse
    from skillscout.evaluation.recorder import InvocationHandlers

    calls: list[ArmName] = []

    def invoke(arm: ArmName):
        def call(_payload, _cap):
            calls.append(arm)
            return InvocationReturn(
                PrimaryResponse(decision=Decision.REVIEW if arm is ArmName.SKILLSCOUT else None),
                TerminationStatus.UNKNOWN,
                "a nested request did not expose termination telemetry",
            )

        return call

    plan = build_schedule(TASKS)[0]
    pair = EvaluationRecorder(InvocationHandlers(invoke(ArmName.SKILLSCOUT), invoke(ArmName.ORDINARY_SEARCH))).record_pair(
        plan, TASKS[0], "/tmp/opaque-context", "a" * 64, run_id="uncertain-return"
    )
    assert calls == [plan.arm_order[0]]
    assert pair.run_stop_required is True
    assert pair.arms[plan.arm_order[1]].status.value == "not_run"


def test_runtime_worker_error_stop_is_validated_as_a_run_stop():
    from skillscout.evaluation.models import Decision, InvocationReturn, PrimaryResponse
    from skillscout.evaluation.recorder import InvocationHandlers

    def terminal_unknown(_payload, _cap):
        return InvocationReturn(
            PrimaryResponse(decision=Decision.REVIEW),
            TerminationStatus.UNKNOWN,
            "termination unknown",
        )

    recorder = EvaluationRecorder(InvocationHandlers(terminal_unknown, terminal_unknown))
    tasks = TASKS
    # The real pinned fixture pack keeps its four unresolved contexts blocked.
    # This callback stops during the first pair, before any later context runs.
    pairs = recorder.run_with_pinned_fixtures(
        ROOT / "docs/evaluation/v1/contexts",
        tasks,
    )
    assert len(pairs) == 42
    assert pairs[0].run_stop_required is True
    assert all(
        arm.invocation_start_monotonic_s is None
        for pair in pairs[1:]
        for arm in pair.arms.values()
    )
    assert validate_records(pairs, tasks) == []

