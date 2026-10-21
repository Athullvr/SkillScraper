from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from skillscout.evaluation.crosswalk import CandidateCrosswalk
from skillscout.evaluation.fixtures import (
    FIXTURE_LOCK_NAME,
    PROTECTED_AUDIT_TEXT,
    FixtureError,
    materialize_opaque_contexts,
    has_protected_audit_disclosure,
    verify_fixture_pack,
)
from skillscout.evaluation.metrics import compute_metrics
from skillscout.evaluation.models import (
    ArmStatus,
    CandidateObservation,
    Decision,
    DecisionConformity,
    InvocationFailure,
    InvocationReturn,
    LinkageStatus,
    LookupStatus,
    PrimaryResponse,
    RelevanceLabel,
    ReviewResolution,
    SlotStatus,
    TerminationStatus,
)
from skillscout.evaluation.protocol import (
    DATASET_SHA256,
    ArmName,
    build_schedule,
    load_dataset,
)
from skillscout.evaluation.recorder import (
    EvaluationRecorder,
    EvidenceLookup,
    InvocationHandlers,
    LookupObservation,
    RunModeError,
)
from skillscout.evaluation.reviews import (
    EvidenceExcerpt,
    ReviewError,
    ReviewKind,
    ReviewLedger,
    ReviewPhase,
    build_candidate_relevance_packet,
    build_decision_conformity_packet,
    decision_conformity_outcome,
    apply_candidate_review_labels,
    decision_outcomes_by_pair,
    prepare_review_order,
    shuffle_packets,
)
from skillscout.evaluation.store import RunManifest, read_run, write_run
from skillscout.evaluation.validation import validate_records


ROOT = Path(__file__).parents[1]
TASKS = load_dataset(ROOT / "docs/evaluation/v1/tasks.jsonl").eligible_tasks


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def utcnow(self) -> datetime:
        return datetime(2026, 10, 21, tzinfo=timezone.utc) + timedelta(seconds=self.value)

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ScriptClock(FakeClock):
    def __init__(self, values: list[float]) -> None:
        super().__init__()
        self.values = iter(values)

    def monotonic(self) -> float:
        return next(self.values)


def _handlers(clock: FakeClock, *, with_candidate: bool = False, requests: list | None = None):
    def invoke(arm: ArmName):
        def run(payload, cap):
            if requests is not None:
                requests.append((arm, payload, cap))
            clock.advance(0.01)
            candidate = (
                (CandidateObservation(
                    stable_id=f"candidate-{arm.value}",
                    source_slug="demo",
                    rank=1,
                    arm_content_hash="a" * 64,
                    arm_revision="rev-1",
                    source_url="https://catalog.invalid/skill",
                ),)
                if with_candidate else ()
            )
            decision = Decision.REVIEW if arm is ArmName.SKILLSCOUT else None
            return InvocationReturn(
                PrimaryResponse(candidates=candidate, decision=decision, internal_attempt_count=1),
                TerminationStatus.CONFIRMED,
                "offline test runner completed",
            )
        return run

    return InvocationHandlers(
        skillscout=invoke(ArmName.SKILLSCOUT),
        ordinary_search=invoke(ArmName.ORDINARY_SEARCH),
    )


def _lookup(clock: FakeClock) -> EvidenceLookup:
    def call(request):
        clock.advance(0.02)
        return LookupObservation(
            success=True,
            returned_candidate_id=request.candidate_id,
            content_hash=request.arm_content_hash,
            revision="rev-1",
            identity_linkage=LinkageStatus.MATCHED,
            revision_linkage=LinkageStatus.MATCHED,
            detail_sufficient_for_relevance=request.kind == "detail",
            termination_status=TerminationStatus.CONFIRMED,
        )
    return call


def _all_context_inputs(tmp_path: Path):
    paths = {task.task_id: tmp_path / f"opaque-context-{task.eligible_index}" for task in TASKS}
    hashes = {task.task_id: f"{task.eligible_index:064x}" for task in TASKS}
    return paths, hashes


def _full_offline_run(tmp_path: Path, *, with_candidate: bool = False):
    clock = FakeClock()
    recorder = EvaluationRecorder(
        _handlers(clock, with_candidate=with_candidate),
        lookup=_lookup(clock),
        clock=clock,
    )
    paths, hashes = _all_context_inputs(tmp_path)
    return recorder, recorder.run_offline(TASKS, paths, hashes, run_id="offline-test")


def test_dataset_and_fixed_schedule_are_loaded_without_exposing_evaluator_fields():
    dataset = load_dataset(ROOT / "docs/evaluation/v1/tasks.jsonl")
    schedule = build_schedule(dataset.eligible_tasks)
    assert dataset.sha256 == DATASET_SHA256
    assert len(dataset.eligible_tasks) == 14
    assert len(schedule) == 42
    assert sum(plan.arm_order[0] is ArmName.ORDINARY_SEARCH for plan in schedule) == 21


def test_fixture_pack_is_pinned_and_materializes_opaque_paths():
    contexts = ROOT / "docs/evaluation/v1/contexts"
    verified = verify_fixture_pack(contexts, TASKS)
    assert verified["fixture_count"] == 14
    assert (contexts / FIXTURE_LOCK_NAME).is_file()
    with materialize_opaque_contexts(contexts, TASKS) as paths:
        assert set(paths) == {task.task_id for task in TASKS}
        for task in TASKS:
            assert task.task_id not in str(paths[task.task_id])
            assert paths[task.task_id].is_dir()


def test_recorder_runs_offline_from_pinned_contexts_and_blocks_only_incomplete_tasks():
    clock = FakeClock()
    recorder = EvaluationRecorder(_handlers(clock), clock=clock)
    pairs = recorder.run_with_pinned_fixtures(
        ROOT / "docs/evaluation/v1/contexts",
        TASKS,
        run_id="pinned-offline-test",
    )
    assert len(pairs) == 42
    assert sum(pair.status.value == "blocked" for pair in pairs) == 12
    assert sum(pair.status.value == "complete" for pair in pairs) == 30
    assert validate_records(pairs, TASKS) == []


def test_fixture_manifest_detects_content_drift(tmp_path: Path):
    (tmp_path / "README.md").write_text("approved", encoding="utf-8")
    manifest_path = tmp_path / "MANIFEST.sha256"
    from skillscout.evaluation.fixtures import write_manifest

    write_manifest(tmp_path)
    (tmp_path / "README.md").write_text("changed", encoding="utf-8")
    with pytest.raises(FixtureError, match="manifest mismatch"):
        from skillscout.evaluation.fixtures import verify_manifest

        verify_manifest(tmp_path)


def test_system_facing_inputs_are_allowlisted_and_context_path_is_opaque(tmp_path: Path):
    requests: list = []
    clock = FakeClock()
    recorder = EvaluationRecorder(_handlers(clock, requests=requests), clock=clock)
    paths, hashes = _all_context_inputs(tmp_path)
    records = recorder.run_offline(TASKS, paths, hashes)
    assert len(records) == 42
    assert len(requests) == 84
    for arm, payload, cap in requests:
        assert cap == 240
        assert PROTECTED_AUDIT_TEXT[0] not in str(payload)
        if arm is ArmName.ORDINARY_SEARCH:
            assert set(payload) == {"query", "limit"}
            assert payload["limit"] == 3
        else:
            assert set(payload) == {"task", "project_path"}
            assert not any(task.task_id in payload["project_path"] for task in TASKS)
    with pytest.raises(RunModeError):
        recorder.run_offline(TASKS, paths, hashes, catalog_mode="live")


def test_system_context_path_cannot_leak_task_id(tmp_path: Path):
    clock = FakeClock()
    recorder = EvaluationRecorder(_handlers(clock), clock=clock)
    paths, hashes = _all_context_inputs(tmp_path)
    paths[TASKS[0].task_id] = tmp_path / TASKS[0].task_id
    with pytest.raises(ValueError, match="must not disclose evaluator task IDs"):
        recorder.run_offline(TASKS, paths, hashes)


@pytest.mark.parametrize(
    "second_start,expected_status,expected_invoked",
    [(300.0, "complete", True), (301.0, "partial", False)],
)
def test_pairing_deadline_is_inclusive_and_late_second_arm_is_not_dispatched(
    second_start: float,
    expected_status: str,
    expected_invoked: bool,
):
    # First arm starts at 0, returns exactly at the 240-second cap; the explicit
    # pre-dispatch clock then places the second start at/after the 300-second edge.
    clock = ScriptClock([0.0, 240.0, second_start, second_start, second_start + 1.0, second_start + 1.0])
    calls: list[ArmName] = []

    def arm_run(arm):
        def run(payload, cap):
            calls.append(arm)
            return InvocationReturn(PrimaryResponse(decision=Decision.REVIEW if arm is ArmName.SKILLSCOUT else None), TerminationStatus.CONFIRMED, "done")
        return run

    recorder = EvaluationRecorder(
        InvocationHandlers(arm_run(ArmName.SKILLSCOUT), arm_run(ArmName.ORDINARY_SEARCH)),
        clock=clock,
    )
    plan = build_schedule(TASKS)[0]
    pair = recorder.record_pair(plan, TASKS[0], "/tmp/opaque-context", "a" * 64, run_id="edge")
    second = plan.arm_order[1]
    assert (pair.arms[second].invocation_start_monotonic_s is not None) is expected_invoked
    assert pair.status.value == expected_status
    if expected_invoked:
        assert pair.pairing_interval_s == 300.0
        assert pair.pairing_within_window is True
    else:
        assert pair.arms[second].status is ArmStatus.NOT_RUN
        assert pair.arms[second].invocation_start_utc is None
        assert pair.pairing_interval_s is None
        assert calls == [plan.arm_order[0]]


def test_unconfirmed_invocation_timeout_stops_second_arm_and_all_later_invocations(tmp_path: Path):
    calls: list[ArmName] = []
    clock = FakeClock()

    def run(arm):
        def invoke(payload, cap):
            calls.append(arm)
            raise InvocationFailure(
                "invocation_timeout",
                termination_status=TerminationStatus.UNKNOWN,
                termination_evidence="harness stopped waiting; request termination unverified",
            )
        return invoke

    recorder = EvaluationRecorder(
        InvocationHandlers(run(ArmName.SKILLSCOUT), run(ArmName.ORDINARY_SEARCH)),
        clock=clock,
    )
    paths, hashes = _all_context_inputs(tmp_path)
    pairs = recorder.run_offline(TASKS, paths, hashes)
    assert len(pairs) == 42
    first = pairs[0]
    assert first.status.value == "partial"
    assert first.run_stop_required is True
    assert first.arms[first.arm_order[0]].status is ArmStatus.FAILED
    assert first.arms[first.arm_order[0]].underlying_termination is TerminationStatus.UNKNOWN
    assert first.arms[first.arm_order[1]].status is ArmStatus.NOT_RUN
    assert all(arm.invocation_start_monotonic_s is None for pair in pairs[1:] for arm in pair.arms.values())
    assert len(calls) == 1
    assert validate_records(pairs, TASKS) == []


def test_unexpected_harness_failure_is_not_a_decision_and_stops_when_termination_unknown():
    clock = FakeClock()
    calls = []

    def fail(payload, cap):
        calls.append(payload)
        raise RuntimeError("synthetic harness crash")

    recorder = EvaluationRecorder(InvocationHandlers(fail, fail), clock=clock)
    plan = build_schedule(TASKS)[0]
    pair = recorder.record_pair(plan, TASKS[0], "/tmp/opaque", "b" * 64, run_id="harness")
    failed = pair.arms[plan.arm_order[0]]
    assert failed.status is ArmStatus.FAILED
    assert failed.decision is None
    assert failed.error_category == "harness_failure:RuntimeError"
    assert pair.arms[plan.arm_order[1]].status is ArmStatus.NOT_RUN
    assert len(calls) == 1


def test_failed_operational_invocation_with_unknown_termination_stops_the_next_arm(tmp_path: Path):
    clock = FakeClock()
    calls: list[ArmName] = []

    def fail(payload, cap):
        calls.append(ArmName.SKILLSCOUT if "project_path" in payload else ArmName.ORDINARY_SEARCH)
        raise InvocationFailure(
            "transport_error",
            termination_status=TerminationStatus.NOT_CONFIRMED,
            termination_evidence="transport dropped before termination could be observed",
        )

    recorder = EvaluationRecorder(InvocationHandlers(fail, fail), clock=clock)
    paths, hashes = _all_context_inputs(tmp_path)
    pairs = recorder.run_offline(TASKS, paths, hashes)
    assert len(pairs) == 42
    assert calls == [pairs[0].arm_order[0]]
    assert pairs[0].run_stop_required is True
    assert pairs[0].arms[pairs[0].arm_order[1]].status is ArmStatus.NOT_RUN
    assert validate_records(pairs, TASKS) == []


def test_unavailable_lookup_handler_is_recorded_as_no_attempt_and_validates():
    clock = FakeClock()
    recorder = EvaluationRecorder(_handlers(clock, with_candidate=True), clock=clock, lookup=None)
    paths, hashes = _all_context_inputs(Path("/tmp"))
    pairs = recorder.run_offline(TASKS, paths, hashes, run_id="no-lookup-handler")
    detail = pairs[0].arms[pairs[0].arm_order[0]].slots[0].detail
    assert detail is not None
    assert detail.status is LookupStatus.FAILED
    assert detail.attempt_count == 0
    assert detail.error_category == "lookup_handler_unavailable"
    assert validate_records(pairs, TASKS) == []


def test_late_primary_response_is_supplemental_and_never_becomes_a_decision():
    clock = ScriptClock([0.0, 240.01])

    def late_response(payload, cap):
        return InvocationReturn(
            PrimaryResponse(
                candidates=(CandidateObservation("late-candidate", rank=1),),
                decision=Decision.USE,
            ),
            TerminationStatus.UNKNOWN,
            "late response; termination unverified",
        )

    recorder = EvaluationRecorder(InvocationHandlers(late_response, late_response), clock=clock)
    plan = build_schedule(TASKS)[0]
    pair = recorder.record_pair(plan, TASKS[0], "/tmp/opaque", "e" * 64, run_id="late-primary")
    arm = pair.arms[plan.arm_order[0]]
    assert arm.status is ArmStatus.FAILED
    assert arm.primary_response_valid is False
    assert arm.decision is None
    assert arm.late_primary_response is True
    assert arm.supplemental_late_response["decision"] == Decision.USE.value
    assert pair.arms[plan.arm_order[1]].status is ArmStatus.NOT_RUN


def test_credential_like_candidate_metadata_is_rejected_without_persisting_the_value():
    clock = FakeClock()
    secret = "api_key=offline_secret_value"

    def invoke(payload, cap):
        clock.advance(0.01)
        decision = Decision.REVIEW if "project_path" in payload else None
        return InvocationReturn(
            PrimaryResponse(
                candidates=(CandidateObservation(stable_id=secret, rank=1),),
                decision=decision,
            ),
            TerminationStatus.CONFIRMED,
            "completed",
        )

    pair = EvaluationRecorder(InvocationHandlers(invoke, invoke), clock=clock).record_pair(
        build_schedule(TASKS)[0], TASKS[0], "/tmp/opaque", "f" * 64, run_id="redact"
    )
    serialized = json.dumps(pair.to_dict())
    assert secret not in serialized
    assert all(arm.error_category == "sensitive_candidate_metadata_redacted" for arm in pair.arms.values())


def test_unconfirmed_lookup_timeout_stops_later_lookups_and_invocations(tmp_path: Path):
    clock = FakeClock()
    calls = []

    def failing_lookup(request):
        calls.append(request)
        raise InvocationFailure(
            "lookup_timeout",
            termination_status=TerminationStatus.NOT_CONFIRMED,
            termination_evidence="lookup harness timed out; request termination not confirmed",
        )

    recorder = EvaluationRecorder(
        _handlers(clock, with_candidate=True),
        lookup=failing_lookup,
        clock=clock,
    )
    paths, hashes = _all_context_inputs(tmp_path)
    pairs = recorder.run_offline(TASKS, paths, hashes)
    assert len(calls) == 1
    assert pairs[0].run_stop_required is True
    assert pairs[0].run_stop_reason == "review_required_after_unconfirmed_lookup_termination"
    assert all(arm.invocation_start_monotonic_s is not None for arm in pairs[0].arms.values())
    assert all(arm.invocation_start_monotonic_s is None for pair in pairs[1:] for arm in pair.arms.values())
    assert validate_records(pairs, TASKS) == []


def test_shared_lookup_budget_starts_after_both_terminal_arms_and_late_evidence_is_supplemental():
    clock = FakeClock()
    calls = []

    def late_lookup(request):
        calls.append(request)
        clock.advance(601.0)
        return LookupObservation(
            success=True,
            returned_candidate_id=request.candidate_id,
            content_hash=request.arm_content_hash,
            identity_linkage=LinkageStatus.MATCHED,
            revision_linkage=LinkageStatus.MATCHED,
            detail_sufficient_for_relevance=True,
            termination_status=TerminationStatus.CONFIRMED,
        )

    recorder = EvaluationRecorder(_handlers(clock, with_candidate=True), lookup=late_lookup, clock=clock)
    plan = build_schedule(TASKS)[0]
    pair = recorder.record_pair(plan, TASKS[0], "/tmp/opaque", "c" * 64, run_id="late")
    terminal_max = max(pair.arms[arm].invocation_end_monotonic_s for arm in ArmName)
    assert pair.lookup_budget_start_monotonic_s >= terminal_max
    assert pair.lookup_deadline_monotonic_s - pair.lookup_budget_start_monotonic_s == 600
    assert len(calls) == 1
    first_slot = pair.arms[pair.arm_order[0]].slots[0]
    assert first_slot.detail.status is LookupStatus.AVAILABLE
    assert first_slot.detail.late is True
    assert first_slot.audit.status is LookupStatus.NOT_STARTED_AFTER_DEADLINE


def test_lookup_callbacks_receive_remaining_shared_budget_as_timeout_cap():
    clock = FakeClock()
    caps: list[float] = []

    def lookup(request):
        assert request.timeout_cap_s is not None
        caps.append(request.timeout_cap_s)
        clock.advance(0.25)
        return LookupObservation(success=True, returned_candidate_id=request.candidate_id)

    recorder = EvaluationRecorder(_handlers(clock, with_candidate=True), lookup=lookup, clock=clock)
    pair = recorder.record_pair(
        build_schedule(TASKS)[0], TASKS[0], "/tmp/opaque", "a" * 64, run_id="lookup-cap"
    )
    assert len(caps) == 4
    assert caps[0] == pytest.approx(600.0)
    assert caps == sorted(caps, reverse=True)
    assert pair.lookup_deadline_monotonic_s - pair.lookup_budget_start_monotonic_s == pytest.approx(600.0)


def test_missing_candidate_identity_or_observed_revision_keeps_detail_unknown(tmp_path: Path):
    clock = FakeClock()

    def lookup(request):
        clock.advance(0.01)
        return LookupObservation(
            success=True,
            returned_candidate_id=None,
            content_hash=request.arm_content_hash,
            revision=None,
            identity_linkage=LinkageStatus.MATCHED,
            revision_linkage=LinkageStatus.MATCHED,
            detail_sufficient_for_relevance=True,
        )

    recorder = EvaluationRecorder(_handlers(clock, with_candidate=True), lookup=lookup, clock=clock)
    paths, hashes = _all_context_inputs(tmp_path)
    pairs = recorder.run_offline(TASKS, paths, hashes, run_id="unlinked-detail")
    for pair in pairs:
        for arm in pair.arms.values():
            for slot in arm.slots:
                if slot.status is SlotStatus.OCCUPIED:
                    assert slot.detail is not None
                    assert slot.detail.identity_linkage is LinkageStatus.UNVERIFIED
                    assert slot.detail.revision_linkage is LinkageStatus.UNVERIFIED
                    slot.relevance_label = RelevanceLabel.RELEVANT
                    slot.review_resolution = ReviewResolution.RESOLVED
                    assert arm.evidence_ready_monotonic_s is None
    report = compute_metrics(pairs, TASKS)
    assert report["slot_outcomes_fixed_126"]["skillscout"].get("relevant", 0) == 0
    assert report["slot_outcomes_fixed_126"]["skillscout"]["unknown"] == 42


def test_uninvoked_arm_means_no_pair_level_lookup_and_unknown_relevance():
    clock = ScriptClock([0.0, 240.0, 301.0])
    calls = []

    def runner(arm):
        def call(payload, cap):
            calls.append(("invoke", arm))
            return InvocationReturn(
                PrimaryResponse(
                    candidates=(CandidateObservation("candidate", rank=1),),
                    decision=Decision.REVIEW if arm is ArmName.SKILLSCOUT else None,
                ),
                TerminationStatus.CONFIRMED,
                "done",
            )
        return call

    def lookup(request):
        calls.append(("lookup", request.kind))
        return LookupObservation(success=True)

    recorder = EvaluationRecorder(
        InvocationHandlers(runner(ArmName.SKILLSCOUT), runner(ArmName.ORDINARY_SEARCH)),
        lookup=lookup,
        clock=clock,
    )
    plan = build_schedule(TASKS)[0]
    pair = recorder.record_pair(plan, TASKS[0], "/tmp/opaque", "d" * 64, run_id="partial")
    assert pair.arms[plan.arm_order[1]].status is ArmStatus.NOT_RUN
    assert not any(kind == "lookup" for kind, _ in calls)
    assert all(slot.detail is None and slot.audit is None for arm in pair.arms.values() for slot in arm.slots)


def test_crosswalk_deduplicates_only_exact_revisions():
    crosswalk = CandidateCrosswalk(key_factory=iter(["opaque-1", "opaque-2", "opaque-3"]).__next__)
    first = crosswalk.register(pair_id="p1", task_id="t1", repetition=1, arm=ArmName.SKILLSCOUT, rank=1, stable_id="id", content_hash="h", source_url="https://user:password@example.invalid/path?token=secret#fragment")
    second = crosswalk.register(pair_id="p2", task_id="t1", repetition=2, arm=ArmName.ORDINARY_SEARCH, rank=1, stable_id="id", content_hash="h")
    third = crosswalk.register(pair_id="p3", task_id="t1", repetition=3, arm=ArmName.SKILLSCOUT, rank=1, stable_id="id")
    fourth = crosswalk.register(pair_id="p4", task_id="t1", repetition=3, arm=ArmName.SKILLSCOUT, rank=1, stable_id="id")
    assert first == second
    assert third != fourth
    assert len(crosswalk.entries) == 4
    assert crosswalk.entries[0].source_url == "https://example.invalid/path"


def test_primary_estimand_uses_equal_task_weights_and_fixed_126_slot_denominator(tmp_path: Path):
    _recorder, pairs = _full_offline_run(tmp_path, with_candidate=True)
    for pair in pairs:
        if pair.task_id != TASKS[0].task_id:
            continue
        scout = pair.arms[ArmName.SKILLSCOUT].slots[0]
        ordinary = pair.arms[ArmName.ORDINARY_SEARCH].slots[0]
        scout.relevance_label = RelevanceLabel.RELEVANT
        scout.review_resolution = ReviewResolution.RESOLVED
        ordinary.relevance_label = RelevanceLabel.IRRELEVANT
        ordinary.review_resolution = ReviewResolution.RESOLVED
    report = compute_metrics(pairs, TASKS)
    assert report["primary"]["fixed_denominator_per_arm"] == 126
    assert report["primary"]["delta"] == pytest.approx(1 / 42)
    assert report["slot_outcomes_fixed_126"]["skillscout"]["relevant"] == 3
    assert report["slot_outcomes_fixed_126"]["ordinary_search"]["irrelevant"] == 3
    assert report["evaluable_slot_precision"]["skillscout"]["denominator"] == 3
    assert report["primary"]["independent_interpretation_units"] == 14
    assert report["primary"]["repeated_pairs"] == 42


def test_blocked_failed_empty_and_unknown_outcomes_are_not_counted_as_irrelevant():
    clock = FakeClock()
    recorder = EvaluationRecorder(_handlers(clock), clock=clock)
    paths, hashes = _all_context_inputs(Path("/tmp"))
    pairs = recorder.run_offline(TASKS, paths, hashes, blocked_task_ids={TASKS[0].task_id})
    counts = compute_metrics(pairs, TASKS)["slot_outcomes_fixed_126"]["skillscout"]
    assert counts.get("irrelevant", 0) == 0
    assert counts["blocked"] == 9
    assert counts["empty"] > 0


def test_reviewer_packets_mask_identity_and_exclude_evaluator_and_protected_fields():
    stripe = next(task for task in TASKS if task.task_id == "review-stripe-webhook-audit")
    packet = build_candidate_relevance_packet(
        stripe,
        approved_context="Node.js webhook service; package metadata only.",
        evidence=[
            EvidenceExcerpt("Signature check uses raw request bytes and validates event type."),
            EvidenceExcerpt("hidden audit finding", evidence_type="audit"),
            EvidenceExcerpt("See https://catalog.invalid/skill; stable-id-1."),
        ],
        packet_key_factory=lambda: "review-opaque",
        identifiers_to_mask=("stable-id-1",),
    )
    rendered = str(packet.to_dict())
    assert "expected_decision" not in rendered
    assert "task_id" not in rendered
    assert "relevant_if_content_matches" not in rendered
    assert "stable-id-1" not in rendered
    assert "https://catalog.invalid" not in rendered
    assert "hidden audit finding" not in rendered
    assert PROTECTED_AUDIT_TEXT[0] not in rendered
    assert has_protected_audit_disclosure("Audit evidence is missing for the current revision.")
    with pytest.raises(ReviewError, match="protected audit-state"):
        build_candidate_relevance_packet(
            stripe,
            approved_context=PROTECTED_AUDIT_TEXT[0],
            evidence=(),
        )
    with pytest.raises(ReviewError, match="protected audit-state"):
        build_candidate_relevance_packet(
            stripe,
            approved_context="Audit evidence is missing for the current revision.",
            evidence=(),
        )


def test_reviewer_evidence_and_rationales_redact_credential_like_values():
    secret = "api_key=reviewer_secret_value"
    packet = build_candidate_relevance_packet(
        TASKS[0],
        approved_context="Safe context",
        evidence=[EvidenceExcerpt(f"Candidate README says {secret}.")],
        packet_key_factory=lambda: "credential-review",
    )
    assert secret not in str(packet.to_dict())
    ledger = ReviewLedger()
    ledger.lock_initial(
        packet,
        reviewer_pseudonym="reviewer-1",
        action="relevant",
        assessable=True,
        rationale=f"The candidate satisfies the criterion; observed {secret}.",
        evidence_basis="retrospective",
    )
    assert secret not in str(ledger.events[0].to_dict())
    assert "[credential redacted]" in ledger.events[0].rationale


def test_decision_packet_hides_original_action_and_can_exclude_protected_evidence():
    task = TASKS[0]
    packet = build_decision_conformity_packet(
        task,
        approved_context="Small repository with one README.",
        evidence=[EvidenceExcerpt("Candidate detail addresses only typo correction.")],
        packet_key_factory=lambda: "decision-opaque",
    )
    rendered = packet.to_dict()
    assert "original_decision" not in rendered
    assert "expected_decision" not in rendered
    assert "expected_decision" not in str(rendered)
    assert len(packet.decision_options) == 4


def test_reviewer_packet_key_cannot_disclose_evaluator_task_id():
    task = TASKS[0]
    with pytest.raises(ReviewError, match="must not disclose the task ID"):
        build_candidate_relevance_packet(
            task,
            approved_context="approved",
            evidence=(),
            packet_key_factory=lambda: f"packet-{task.task_id}",
        )


def test_review_ledger_preserves_initial_disagreement_and_requires_adjudication():
    task = TASKS[0]
    packet = build_candidate_relevance_packet(task, approved_context="approved", evidence=[], packet_key_factory=lambda: "c1")
    ledger = ReviewLedger(clock=lambda: "2026-10-21T00:00:00Z")
    first = ledger.lock_initial(packet, reviewer_pseudonym="r1", action="relevant", assessable=True, rationale="criterion met", evidence_basis="retrospective")
    ledger.lock_initial(packet, reviewer_pseudonym="r2", action="irrelevant", assessable=True, rationale="criterion not met", evidence_basis="retrospective")
    assert ledger.candidate_resolution(packet.packet_key) == (ReviewResolution.UNRESOLVED, None)
    with pytest.raises(ReviewError, match="adjudication requires"):
        ledger.append_adjudication(packet, reviewer_pseudonym="r3", action="relevant", assessable=True, rationale="resolve", evidence_basis="retrospective")
    ledger.lock_adjudicator_initial(packet, reviewer_pseudonym="r3", action="relevant", assessable=True, rationale="independent", evidence_basis="retrospective")
    ledger.append_adjudication(packet, reviewer_pseudonym="r3", action="relevant", assessable=True, rationale="criteria support relevance", evidence_basis="retrospective")
    resolution, label = ledger.candidate_resolution(packet.packet_key)
    assert resolution is ReviewResolution.RESOLVED
    assert label is RelevanceLabel.RELEVANT
    assert len(ledger.events) == 4
    assert ledger.events[0].event_id == first.event_id
    assert [event.phase for event in ledger.events].count(ReviewPhase.INITIAL) == 2
    reloaded = ReviewLedger.from_records([event.to_dict() for event in ledger.events])
    assert reloaded.candidate_resolution(packet.packet_key) == (
        ReviewResolution.RESOLVED,
        RelevanceLabel.RELEVANT,
    )


def test_adjudication_is_not_allowed_when_independent_reviewers_agree():
    packet = build_candidate_relevance_packet(
        TASKS[0], approved_context="approved", evidence=[], packet_key_factory=lambda: "agreement"
    )
    ledger = ReviewLedger()
    for reviewer in ("r1", "r2"):
        ledger.lock_initial(
            packet,
            reviewer_pseudonym=reviewer,
            action="relevant",
            assessable=True,
            rationale="criterion met",
            evidence_basis="retrospective",
        )
    with pytest.raises(ReviewError, match="disagreeing"):
        ledger.lock_adjudicator_initial(
            packet,
            reviewer_pseudonym="r3",
            action="irrelevant",
            assessable=True,
            rationale="not needed",
            evidence_basis="retrospective",
        )


def test_compromised_reviewer_is_preserved_and_requires_unexposed_replacement():
    packet = build_candidate_relevance_packet(TASKS[0], approved_context="safe", evidence=[], packet_key_factory=lambda: "replacement")
    ledger = ReviewLedger()
    compromised = ledger.lock_initial(
        packet,
        reviewer_pseudonym="r1",
        action="relevant",
        assessable=True,
        rationale="assessment preserved",
        evidence_basis="retrospective",
        blinding_compromised=True,
        unavoidable_exposure=("stable source name",),
    )
    ledger.lock_replacement(
        packet,
        replaces_event_id=compromised.event_id,
        reviewer_pseudonym="r2",
        action="irrelevant",
        assessable=True,
        rationale="unexposed independent replacement",
        evidence_basis="retrospective",
    )
    ledger.lock_initial(packet, reviewer_pseudonym="r3", action="irrelevant", assessable=True, rationale="independent", evidence_basis="retrospective")
    assert ledger.candidate_resolution(packet.packet_key) == (ReviewResolution.RESOLVED, RelevanceLabel.IRRELEVANT)
    assert len(ledger.events) == 3


def test_review_order_is_reproducible_and_decision_conformity_requires_approved_gates():
    packets = [
        build_candidate_relevance_packet(task, approved_context="safe", evidence=[], packet_key_factory=lambda i=i: str(i))
        for i, task in enumerate(TASKS[:5])
    ]
    assert [p.packet_key for p in shuffle_packets(packets, seed=91)] == [p.packet_key for p in shuffle_packets(packets, seed=91)]
    review_order = prepare_review_order(packets, seed=91)
    assert review_order.seed == 91
    assert review_order.packet_keys == tuple(p.packet_key for p in review_order.packets)
    assert decision_conformity_outcome(
        Decision.USE,
        Decision.USE,
        assessable=True,
        review_resolution=ReviewResolution.RESOLVED,
        task_gates_approved=False,
    ) is DecisionConformity.INDETERMINATE
    assert decision_conformity_outcome(
        Decision.USE,
        Decision.REVIEW,
        assessable=True,
        review_resolution=ReviewResolution.RESOLVED,
        task_gates_approved=True,
    ) is DecisionConformity.INCORRECT


def test_locked_review_labels_and_task_actions_join_only_through_opaque_keys(tmp_path: Path):
    _recorder, pairs = _full_offline_run(tmp_path, with_candidate=True)
    pair = pairs[0]
    slot = pair.arms[ArmName.SKILLSCOUT].slots[0]
    task = TASKS[pair.task_index - 1]
    relevance_packet = build_candidate_relevance_packet(
        task,
        approved_context="approved fixture facts",
        evidence=[EvidenceExcerpt("Task-specific detail excerpt.")],
        packet_key_factory=lambda: slot.opaque_candidate_key,
    )
    ledger = ReviewLedger()
    ledger.lock_initial(relevance_packet, reviewer_pseudonym="rel-1", action="relevant", assessable=True, rationale="criterion is met", evidence_basis="retrospective")
    ledger.lock_initial(relevance_packet, reviewer_pseudonym="rel-2", action="relevant", assessable=True, rationale="criterion is met", evidence_basis="retrospective")
    apply_candidate_review_labels([pair], ledger)
    assert slot.relevance_label is RelevanceLabel.RELEVANT
    assert slot.review_resolution is ReviewResolution.RESOLVED

    decision_packet = build_decision_conformity_packet(
        task,
        approved_context="approved fixture facts",
        evidence=[EvidenceExcerpt("Task requirements and linked evidence.")],
        packet_key_factory=lambda: "decision-packet-key",
    )
    ledger.lock_initial(decision_packet, reviewer_pseudonym="dec-1", action="REVIEW", assessable=True, rationale="review is justified", evidence_basis="mixed")
    ledger.lock_initial(decision_packet, reviewer_pseudonym="dec-2", action="REVIEW", assessable=True, rationale="review is justified", evidence_basis="mixed")
    outcomes = decision_outcomes_by_pair(
        [pair],
        ledger,
        {pair.pair_id: decision_packet.packet_key},
        task_gates_approved=True,
    )
    assert outcomes[pair.pair_id] is DecisionConformity.CORRECT


def test_validator_and_private_run_store_round_trip(tmp_path: Path):
    recorder, pairs = _full_offline_run(tmp_path, with_candidate=True)
    assert validate_records(pairs, TASKS) == []
    run_dir = tmp_path / "restricted-run"
    write_run(
        run_dir,
        RunManifest(run_id="test-run"),
        pairs,
        TASKS,
        crosswalk_entries=recorder.crosswalk.entries,
    )
    manifest, loaded = read_run(run_dir)
    assert manifest["catalog_mode"] == "offline"
    assert len(loaded) == 42
    assert validate_records(loaded, TASKS) == []
    assert run_dir.stat().st_mode & 0o777 == 0o700
    assert (run_dir / "pairs.jsonl").stat().st_mode & 0o777 == 0o600
    with pytest.raises(Exception, match="refusing to overwrite"):
        write_run(
            run_dir,
            RunManifest(run_id="test-run"),
            pairs,
            TASKS,
            crosswalk_entries=recorder.crosswalk.entries,
        )


def test_private_run_store_refuses_credential_like_candidate_record(tmp_path: Path):
    recorder, pairs = _full_offline_run(tmp_path, with_candidate=True)
    pair = pairs[0]
    arm_name = pair.arm_order[0]
    slot = pair.arms[arm_name].slots[0]
    secret = "api_key=stored_candidate_secret"
    slot.stable_id = secret
    crosswalk_rows = [entry.to_dict() for entry in recorder.crosswalk.entries]
    for entry in crosswalk_rows:
        if entry["pair_id"] == pair.pair_id and entry["arm"] == arm_name.value and entry["rank"] == 1:
            entry["stable_id"] = secret
            break
    run_dir = tmp_path / "unsafe-pair"
    with pytest.raises(Exception, match="credential-like text"):
        write_run(
            run_dir,
            RunManifest(run_id="unsafe-pair"),
            pairs,
            TASKS,
            crosswalk_entries=crosswalk_rows,
        )
    assert not run_dir.exists()
    with pytest.raises(Exception, match="credential or token"):
        write_run(
            tmp_path / "unsafe-manifest",
            {"run_id": "bad", "dataset_sha256": DATASET_SHA256, "catalog_mode": "offline", "note": "Bearer abcdefghijkl"},
            pairs,
            TASKS,
            crosswalk_entries=recorder.crosswalk.entries,
        )


def test_persisted_blinded_labels_are_joined_for_metrics_cli(tmp_path: Path, capsys):
    recorder, pairs = _full_offline_run(tmp_path, with_candidate=True)
    ledger = ReviewLedger()
    tasks_by_id = {task.task_id: task for task in TASKS}
    reviewed_keys: set[str] = set()
    for entry in recorder.crosswalk.entries:
        if entry.opaque_candidate_key in reviewed_keys:
            continue
        reviewed_keys.add(entry.opaque_candidate_key)
        packet = build_candidate_relevance_packet(
            tasks_by_id[entry.task_id],
            approved_context="approved fixture context",
            evidence=[EvidenceExcerpt("Linked detail sufficiently matches the task criterion.")],
            packet_key_factory=lambda key=entry.opaque_candidate_key: key,
        )
        for reviewer in ("relevance-a", "relevance-b"):
            ledger.lock_initial(
                packet,
                reviewer_pseudonym=reviewer,
                action="relevant",
                assessable=True,
                rationale="linked candidate evidence meets the criterion",
                evidence_basis="retrospective",
            )
    run_dir = tmp_path / "reviewed-run"
    write_run(
        run_dir,
        RunManifest(run_id="reviewed-run"),
        pairs,
        TASKS,
        review_events=ledger.events,
        crosswalk_entries=recorder.crosswalk.entries,
    )
    from skillscout.evaluation.__main__ import main

    assert main(["metrics", str(run_dir)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["slot_outcomes_fixed_126"]["skillscout"]["relevant"] == 42
    assert report["slot_outcomes_fixed_126"]["ordinary_search"]["relevant"] == 42
    assert report["primary"]["delta"] == pytest.approx(0.0)
    assert report["decision_conformity_skillscout_only"]["counts"]["INDETERMINATE"] == 42


def test_live_collection_and_invalid_payloads_are_refused(tmp_path: Path):
    clock = FakeClock()
    recorder = EvaluationRecorder(_handlers(clock), clock=clock)
    paths, hashes = _all_context_inputs(tmp_path)
    with pytest.raises(RunModeError):
        recorder.run_offline(TASKS, paths, hashes, catalog_mode="live")
    with pytest.raises(ValueError, match="dataset hash"):
        recorder.run_offline(TASKS, paths, hashes, dataset_sha256="0" * 64)


def test_validator_reports_malformed_shapes_without_raising():
    result = validate_records([{"arm_order": None}, *({} for _ in range(41))], TASKS)
    assert len(result) == 1
    assert result[0].startswith("malformed run record structure:")

