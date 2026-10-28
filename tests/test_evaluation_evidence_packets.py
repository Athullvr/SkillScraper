from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from skillscout.evaluation.evidence_packets import (
    EVIDENCE_ARTIFACT_SCHEMA,
    EvidencePacketError,
    EvidencePacketPipeline,
    EvidenceStatus,
    apply_reviews_and_derive_metrics,
    load_evidence_source_artifact,
    load_review_ledger,
    load_restricted_packet_crosswalk,
    load_review_packet_artifacts,
    persist_review_ledger,
    write_evidence_source_artifact,
    write_review_packet_artifacts,
)
from skillscout.evaluation.fixtures import PROTECTED_AUDIT_TEXT, has_protected_audit_disclosure
from skillscout.evaluation.models import (
    CandidateObservation,
    Decision,
    InvocationReturn,
    LinkageStatus,
    PrimaryResponse,
    RelevanceLabel,
    ReviewResolution,
    TerminationStatus,
)
from skillscout.evaluation.protocol import DATASET_SHA256, ArmName, build_schedule, load_dataset
from skillscout.evaluation.recorder import EvaluationRecorder, InvocationHandlers, LookupObservation
from skillscout.evaluation.reviews import ReviewError, ReviewKind, ReviewLedger
from skillscout.evaluation.store import RunManifest, write_run


TASKS = load_dataset().eligible_tasks


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def utcnow(self) -> datetime:
        return datetime(2026, 10, 21, tzinfo=timezone.utc) + timedelta(seconds=self.value)

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _offline_records(tmp_path: Path):
    clock = FakeClock()
    digest = "a" * 64

    def invoker(arm: ArmName):
        def invoke(payload, timeout_s):
            clock.advance(0.01)
            candidate = CandidateObservation(
                stable_id="candidate-source-1",
                source_slug="source-skill",
                rank=1,
                arm_content_hash=digest,
                arm_revision="arm-revision-1",
                source_url="https://source.invalid/skill?token=must-not-escape",
                returned_metadata={"score": "9876", "popularity": "99999", "expected_decision": "AVOID"},
            )
            return InvocationReturn(
                PrimaryResponse(
                    candidates=(candidate,),
                    decision=Decision.REVIEW if arm is ArmName.SKILLSCOUT else None,
                    internal_attempt_count=1,
                ),
                TerminationStatus.CONFIRMED,
                "offline fake completed",
            )
        return invoke

    def lookup(request):
        clock.advance(0.005)
        return LookupObservation(
            success=True,
            returned_candidate_id=request.candidate_id,
            content_hash=request.arm_content_hash,
            revision=request.arm_revision,
            identity_linkage=LinkageStatus.MATCHED,
            revision_linkage=LinkageStatus.MATCHED,
            detail_sufficient_for_relevance=request.kind == "detail",
            termination_status=TerminationStatus.CONFIRMED,
            termination_evidence="fake terminal lookup",
        )

    recorder = EvaluationRecorder(
        InvocationHandlers(
            skillscout=invoker(ArmName.SKILLSCOUT),
            ordinary_search=invoker(ArmName.ORDINARY_SEARCH),
        ),
        lookup=lookup,
        clock=clock,
    )
    paths = {task.task_id: tmp_path / f"opaque-context-{task.eligible_index}" for task in TASKS}
    hashes = {task.task_id: f"{task.eligible_index:064x}" for task in TASKS}
    pairs = recorder.run_offline(TASKS, paths, hashes, run_id="packet-test")
    return recorder, pairs


def _attachments(pairs, *, text="Candidate detail provides actionable implementation guidance."):
    result = {}
    for pair in pairs:
        for arm in ArmName:
            arm_record = pair.arms[arm]
            for slot in arm_record.slots:
                if slot.status.value != "occupied":
                    continue
                result[(pair.pair_id, arm.value, slot.rank)] = {
                    "excerpts": [{"content": text, "evidence_type": "candidate_readme", "rank": 999}],
                    "provenance": "retrospective_evaluator_evidence",
                    "retrieved_at_utc": slot.detail.end_utc,
                    "observed_at_utc": slot.detail.start_utc,
                    # The following attacker-controlled fields are deliberately ignored.
                    "arm": "SkillScout",
                    "rank": 999,
                    "score": "score-leak-marker",
                    "popularity": "popularity-leak-marker",
                    "expected_decision": "expected-label-leak-marker",
                    "task_category": "category-leak-marker",
                    "audit_status": "protected-audit-leak-marker",
                    "error": "exception-leak-marker",
                    "metadata": {"source": "source-metadata-leak-marker"},
                }
    return result


def _build(tmp_path: Path, *, attachments=None, **kwargs):
    recorder, pairs = _offline_records(tmp_path)
    bundle = EvidencePacketPipeline().build(
        pairs,
        TASKS,
        approved_context_by_task={task.task_id: "" for task in TASKS},
        attachments=_attachments(pairs) if attachments is None else attachments,
        candidate_crosswalk_entries=recorder.crosswalk.entries,
        **kwargs,
    )
    return recorder, pairs, bundle


def _all_packet_text(bundle) -> str:
    return json.dumps(bundle.public_payload(), ensure_ascii=False, sort_keys=True)


def test_versioned_allowlisted_packets_mask_identity_and_internal_runtime_fields(tmp_path):
    _, _, bundle = _build(tmp_path)
    public = _all_packet_text(bundle)
    assert bundle.candidate_artifacts
    artifact = bundle.candidate_artifacts[0].to_dict()
    assert artifact["schema_version"] == EVIDENCE_ARTIFACT_SCHEMA
    assert artifact["opaque_candidate_id"] == artifact["packet"]["packet_key"]
    assert artifact["observed_revision_ref"]
    assert artifact["retrieval_deadline_at_utc"]
    assert artifact["retrieval_status"] == EvidenceStatus.COMPLETE.value
    for forbidden in (
        "candidate-source-1",
        "source-skill",
        "source.invalid",
        "token=must-not-escape",
        "SkillScout",
        "score-leak-marker",
        "popularity-leak-marker",
        "expected-label-leak-marker",
        "category-leak-marker",
        "protected-audit-leak-marker",
        "exception-leak-marker",
        "source-metadata-leak-marker",
    ):
        assert forbidden not in public
    assert artifact["packet"]["schema_version"] == "skillscout-review-packet-v2"


def test_runtime_candidate_key_is_replaced_if_it_contains_source_identity(tmp_path):
    _, pairs = _offline_records(tmp_path)
    for pair in pairs:
        for arm in ArmName:
            for slot in pair.arms[arm].slots:
                if slot.status.value == "occupied":
                    slot.opaque_candidate_key = slot.stable_id
    bundle = EvidencePacketPipeline().build(
        pairs,
        TASKS,
        approved_context_by_task={task.task_id: "" for task in TASKS},
        attachments=_attachments(pairs),
    )
    public = _all_packet_text(bundle)
    assert "candidate-source-1" not in public
    assert all(item.opaque_candidate_id != "candidate-source-1" for item in bundle.candidate_artifacts)


def test_sensitive_and_protected_excerpt_text_is_withheld_and_never_serialized(tmp_path):
    recorder, pairs = _offline_records(tmp_path)
    protected = _attachments(pairs, text=PROTECTED_AUDIT_TEXT[0])
    secret_texts = (
        "Candidate content contains AKIA1234567890ABCDEF and must not escape.",
        "Returned by SkillScout after rank #1; score 0.97 and popularity 9000.",
        "Returned by SkillScraper after rank #1; score 0.97 and popularity 9000.",
        "Expected decision: AVOID for this task category.",
    )
    # Taint every occurrence for the same task/revision so another pair cannot supply a safe substitute.
    task_id = TASKS[0].task_id
    attachment_sets = [protected] + [_attachments(pairs, text=text) for text in secret_texts]
    for target in attachment_sets:
        for pair in pairs:
            if pair.task_id == task_id:
                for arm in ArmName:
                    slot = pair.arms[arm].slots[0]
                    if target is protected:
                        target[(pair.pair_id, arm.value, slot.rank)]["excerpts"][0]["content"] = PROTECTED_AUDIT_TEXT[0]
    for index, attachments in enumerate(attachment_sets):
        bundle = EvidencePacketPipeline().build(
            pairs,
            TASKS,
            approved_context_by_task={task.task_id: "" for task in TASKS},
            attachments=attachments,
            candidate_crosswalk_entries=recorder.crosswalk.entries,
        )
        artifact = next(item for item in bundle.candidate_artifacts if item.packet.task_text == TASKS[0].task_text)
        serialized = _all_packet_text(bundle)
        assert artifact.retrieval_status is EvidenceStatus.UNVERIFIED
        assert not artifact.packet.assessment_allowed
        assert "evidence_unavailable_for_review" in artifact.packet.evidence_reasons
        assert PROTECTED_AUDIT_TEXT[0] not in serialized
        assert "protected_text_withheld" not in serialized
        assert "protected_text_withheld" in json.dumps(bundle.restricted_payload(), ensure_ascii=False)
        for marker in ("AKIA1234567890ABCDEF", "SkillScout", "SkillScraper", "rank #1", "score 0.97", "popularity 9000", "Expected decision: AVOID", "task category"):
            assert marker not in serialized
        reviewer_path, _ = write_review_packet_artifacts(
            bundle,
            tmp_path / f"sensitive-reviewer-{index}" / "packets.json",
            tmp_path / f"sensitive-restricted-{index}" / "crosswalk.json",
        )
        serialized_file = reviewer_path.read_text(encoding="utf-8")
        assert PROTECTED_AUDIT_TEXT[0] not in serialized_file
        for marker in ("AKIA1234567890ABCDEF", "SkillScout", "SkillScraper", "rank #1", "score 0.97", "popularity 9000", "Expected decision: AVOID"):
            assert marker not in serialized_file


def test_missing_late_stale_mismatched_and_truncated_evidence_remain_distinct(tmp_path):
    recorder, pairs = _offline_records(tmp_path)
    attachments = _attachments(pairs)
    task_id = TASKS[0].task_id
    for pair in pairs:
        if pair.task_id != task_id:
            continue
        for arm in ArmName:
            slot = pair.arms[arm].slots[0]
            key = (pair.pair_id, arm.value, slot.rank)
            if pair.repetition == 1:
                attachments.pop(key)
            elif pair.repetition == 2:
                attachments[key]["stale"] = True
            else:
                attachments[key]["excerpts"] = ["x" * 2500]
    bundle = EvidencePacketPipeline().build(
        pairs,
        TASKS,
        approved_context_by_task={task.task_id: "" for task in TASKS},
        attachments=attachments,
        candidate_crosswalk_entries=recorder.crosswalk.entries,
    )
    statuses = {item.retrieval_status for item in bundle.candidate_artifacts if item.packet.task_text == TASKS[0].task_text}
    # A candidate key represents one exact task/revision, so the artifact takes the strongest
    # verified occurrence; individual slot timing/linkage still gates every metric denominator.
    assert statuses == {EvidenceStatus.PARTIAL}
    combined = next(item for item in bundle.candidate_artifacts if item.packet.task_text == TASKS[0].task_text)
    assert {"missing", "stale", "partial"}.issubset(set(combined.status_flags))
    missing_source = dict(attachments)
    for pair in pairs:
        if pair.task_id == task_id:
            for arm in ArmName:
                slot = pair.arms[arm].slots[0]
                missing_source.pop((pair.pair_id, arm.value, slot.rank), None)
    missing_bundle = EvidencePacketPipeline().build(
        pairs,
        TASKS,
        approved_context_by_task={task.task_id: "" for task in TASKS},
        attachments=missing_source,
        candidate_crosswalk_entries=recorder.crosswalk.entries,
    )
    missing = next(item for item in missing_bundle.candidate_artifacts if item.packet.task_text == TASKS[0].task_text)
    assert missing.retrieval_status is EvidenceStatus.MISSING
    assert not missing.packet.assessment_allowed


def test_revision_mismatch_and_late_lookup_are_not_assessable(tmp_path):
    recorder, pairs = _offline_records(tmp_path)
    pair = pairs[0]
    slot = pair.arms[pair.arm_order[0]].slots[0]
    slot.detail.revision = "different-revision"
    slot.detail.revision_linkage = LinkageStatus.MISMATCH
    attachments = _attachments(pairs)
    another_pair = next(pair for pair in pairs if pair.task_id == TASKS[0].task_id and pair.pair_id != pairs[0].pair_id)
    late_slot = another_pair.arms[another_pair.arm_order[0]].slots[0]
    attachments[(another_pair.pair_id, another_pair.arm_order[0].value, late_slot.rank)]["late"] = True
    bundle = EvidencePacketPipeline().build(
        pairs,
        TASKS,
        approved_context_by_task={task.task_id: "" for task in TASKS},
        attachments=attachments,
        candidate_crosswalk_entries=recorder.crosswalk.entries,
    )
    # The same exact candidate may have a separately verified occurrence, but the
    # mismatched and late occurrence states remain recorded in the private crosswalk.
    assert bundle.candidate_artifacts
    artifact = next(item for item in bundle.candidate_artifacts if item.packet.task_text == TASKS[0].task_text)
    assert {"late", "mismatched"}.issubset(set(artifact.status_flags))
    assert all("different-revision" not in item.packet.to_dict().__repr__() for item in bundle.candidate_artifacts)


def test_attachment_timestamp_after_shared_deadline_is_late_and_missing_time_is_unverified(tmp_path):
    recorder, pairs = _offline_records(tmp_path)
    attachments = _attachments(pairs)
    task_id = TASKS[0].task_id
    for pair in pairs:
        if pair.task_id != task_id:
            continue
        for arm in ArmName:
            slot = pair.arms[arm].slots[0]
            key = (pair.pair_id, arm.value, slot.rank)
            attachments[key]["retrieved_at_utc"] = "2026-10-21T00:20:00+00:00"
    late_bundle = EvidencePacketPipeline().build(
        pairs,
        TASKS,
        approved_context_by_task={task.task_id: "" for task in TASKS},
        attachments=attachments,
        candidate_crosswalk_entries=recorder.crosswalk.entries,
    )
    late_artifact = next(item for item in late_bundle.candidate_artifacts if item.packet.task_text == TASKS[0].task_text)
    assert late_artifact.retrieval_status is EvidenceStatus.LATE
    assert not late_artifact.packet.assessment_allowed

    missing_clock = _attachments(pairs)
    for pair in pairs:
        if pair.task_id == task_id:
            for arm in ArmName:
                slot = pair.arms[arm].slots[0]
                missing_clock[(pair.pair_id, arm.value, slot.rank)]["retrieved_at_utc"] = None
    unverified_bundle = EvidencePacketPipeline().build(
        pairs,
        TASKS,
        approved_context_by_task={task.task_id: "" for task in TASKS},
        attachments=missing_clock,
        candidate_crosswalk_entries=recorder.crosswalk.entries,
    )
    unverified = next(item for item in unverified_bundle.candidate_artifacts if item.packet.task_text == TASKS[0].task_text)
    assert unverified.retrieval_status is EvidenceStatus.UNVERIFIED
    assert "evidence_timing_unverified" in unverified.status_flags
    assert not unverified.packet.assessment_allowed


def test_missing_candidate_identity_fails_closed(tmp_path):
    recorder, pairs = _offline_records(tmp_path)
    slot = pairs[0].arms[pairs[0].arm_order[0]].slots[0]
    slot.stable_id = None
    with pytest.raises(EvidencePacketError):
        EvidencePacketPipeline().build(
            pairs,
            TASKS,
            approved_context_by_task={task.task_id: "" for task in TASKS},
            attachments=_attachments(pairs),
            candidate_crosswalk_entries=recorder.crosswalk.entries,
        )


def test_protected_audit_dependency_blocks_decision_packet_but_not_relevance(tmp_path):
    _, _, bundle = _build(
        tmp_path,
        decision_criteria_by_task={task.task_id: ("Apply the approved task-level rubric.",) for task in TASKS},
    )
    stripe_pairs = [gate for gate in bundle.decision_gates if gate["status"] == "indeterminate_protected_evidence_withheld"]
    assert len(stripe_pairs) == 3
    stripe_task = next(task for task in TASKS if has_protected_audit_disclosure(task.project_context))
    assert any(item.packet.task_text == stripe_task.task_text and item.packet.assessment_allowed for item in bundle.candidate_artifacts)
    assert not any(
        packet.kind is ReviewKind.DECISION_CONFORMITY and packet.task_text == stripe_task.task_text
        for packet in bundle.decision_packets
    )
    assert stripe_task.task_id not in json.dumps(bundle.public_payload())
    assert PROTECTED_AUDIT_TEXT[0] not in json.dumps(bundle.public_payload())


def test_protected_audit_evidence_is_preserved_only_in_the_restricted_artifact(tmp_path):
    recorder, pairs = _offline_records(tmp_path)
    attachments = _attachments(pairs)
    pair = pairs[0]
    arm = pair.arm_order[0]
    slot = pair.arms[arm].slots[0]
    key = (pair.pair_id, arm.value, slot.rank)
    attachments[key]["excerpts"].append({
        "content": PROTECTED_AUDIT_TEXT[0],
        "evidence_type": "audit_finding",
        "expected_decision": "AVOID",
    })
    bundle = EvidencePacketPipeline().build(
        pairs,
        TASKS,
        approved_context_by_task={task.task_id: "" for task in TASKS},
        attachments=attachments,
        candidate_crosswalk_entries=recorder.crosswalk.entries,
        decision_criteria_by_task={task.task_id: ("Apply the approved task-level rubric.",) for task in TASKS},
        decision_assessable_pairs={item.pair_id for item in pairs},
    )
    assert PROTECTED_AUDIT_TEXT[0] not in _all_packet_text(bundle)
    assert any(
        item["content"] == PROTECTED_AUDIT_TEXT[0]
        for item in bundle.restricted_payload()["protected_evidence"]
    )
    assert any(
        item["pair_id"] == pair.pair_id
        and item["status"] == "indeterminate_protected_evidence_withheld"
        for item in bundle.decision_gates
    )
    candidate = next(item for item in bundle.candidate_artifacts if item.packet.task_text == TASKS[0].task_text)
    assert candidate.packet.assessment_allowed
    reviewer_path, restricted_path = write_review_packet_artifacts(
        bundle,
        tmp_path / "protected-reviewer" / "packets.json",
        tmp_path / "protected-restricted" / "crosswalk.json",
    )
    assert PROTECTED_AUDIT_TEXT[0] not in reviewer_path.read_text(encoding="utf-8")
    assert PROTECTED_AUDIT_TEXT[0] in restricted_path.read_text(encoding="utf-8")
    restricted = load_restricted_packet_crosswalk(restricted_path, authorization_reference="approved offline review")
    assert restricted["protected_evidence"][0]["content"] == PROTECTED_AUDIT_TEXT[0]


def test_packet_storage_round_trip_integrity_determinism_and_crosswalk_boundary(tmp_path):
    _, _, bundle = _build(tmp_path)
    reviewer_dir_a = tmp_path / "reviewer-a"
    restricted_dir_a = tmp_path / "restricted-a"
    reviewer_dir_b = tmp_path / "reviewer-b"
    restricted_dir_b = tmp_path / "restricted-b"
    a, crosswalk_a = write_review_packet_artifacts(bundle, reviewer_dir_a / "packets.json", restricted_dir_a / "crosswalk.json")
    b, crosswalk_b = write_review_packet_artifacts(bundle, reviewer_dir_b / "packets.json", restricted_dir_b / "crosswalk.json")
    assert a.read_bytes() == b.read_bytes()
    assert crosswalk_a.read_bytes() == crosswalk_b.read_bytes()
    assert stat.S_IMODE(a.stat().st_mode) == 0o600
    assert stat.S_IMODE(crosswalk_a.stat().st_mode) == 0o600
    assert stat.S_IMODE(crosswalk_a.parent.stat().st_mode) == 0o700
    assert crosswalk_a.parent != a.parent
    assert "candidate-source-1" not in a.read_text(encoding="utf-8")
    loaded = load_review_packet_artifacts(a)
    assert loaded.dataset_sha256 == DATASET_SHA256
    assert len(loaded.candidate_packets) == len(bundle.candidate_artifacts)
    with pytest.raises(EvidencePacketError, match="authorization reference"):
        load_restricted_packet_crosswalk(crosswalk_a, authorization_reference="")
    restricted = load_restricted_packet_crosswalk(crosswalk_a, authorization_reference="offline QA review")
    assert restricted["candidate_entries"]
    assert restricted["candidate_entries"][0]["stable_id"] == "candidate-source-1"
    os.chmod(crosswalk_a.parent, 0o755)
    with pytest.raises(EvidencePacketError, match="access boundary"):
        load_restricted_packet_crosswalk(crosswalk_a, authorization_reference="offline QA review")


def test_persisted_run_to_packets_to_reviews_to_metrics_offline(tmp_path, capsys):
    recorder, pairs = _offline_records(tmp_path)
    run_path = write_run(
        tmp_path / "restricted-run",
        RunManifest(run_id="offline-packet-integration"),
        pairs,
        TASKS,
        crosswalk_entries=recorder.crosswalk.entries,
    )
    source_path = write_evidence_source_artifact(
        _attachments(pairs),
        tmp_path / "restricted-evidence" / "source.json",
    )
    assert len(load_evidence_source_artifact(source_path).attachments) == 84
    from skillscout.evaluation.__main__ import main

    reviewer_path = tmp_path / "reviewer-artifacts" / "packets.json"
    crosswalk_path = tmp_path / "restricted-packet-crosswalk" / "crosswalk.json"
    assert main([
        "packets", "build", str(run_path),
        "--evidence-source", str(source_path),
        "--reviewer-output", str(reviewer_path),
        "--restricted-output", str(crosswalk_path),
    ]) == 0
    build_summary = json.loads(capsys.readouterr().out)
    assert build_summary["candidate_packets"] > 0
    reviewer_path_2 = tmp_path / "reviewer-artifacts-repeat" / "packets.json"
    crosswalk_path_2 = tmp_path / "restricted-packet-crosswalk-repeat" / "crosswalk.json"
    assert main([
        "packets", "build", str(run_path),
        "--evidence-source", str(source_path),
        "--reviewer-output", str(reviewer_path_2),
        "--restricted-output", str(crosswalk_path_2),
    ]) == 0
    capsys.readouterr()
    assert reviewer_path.read_bytes() == reviewer_path_2.read_bytes()
    assert crosswalk_path.read_bytes() == crosswalk_path_2.read_bytes()
    loaded = load_review_packet_artifacts(reviewer_path)
    restricted = load_restricted_packet_crosswalk(crosswalk_path, authorization_reference="offline integration test")
    incomplete_context_task_texts = {
        task.task_text for task in TASKS
        if task.task_id in {
            "routine-unit-test", "routine-readme-command", "helpful-postgres-migration", "helpful-terraform-iam"
        }
    }
    for artifact in loaded.candidate_packets:
        if artifact.packet.task_text in incomplete_context_task_texts:
            assert artifact.packet.context_status == "incomplete_by_protocol"
            assert artifact.packet.context_reasons
            assert "context_incomplete" in artifact.packet.evidence_reasons
            assert not artifact.packet.assessment_allowed
        else:
            assert artifact.packet.context_status == "ready"
    assert sum(item["status"] == "indeterminate_context_incomplete" for item in restricted["decision_gates"]) == 12
    ledger = ReviewLedger()
    for artifact in loaded.candidate_packets:
        for reviewer in ("reviewer-a", "reviewer-b"):
            assessable = artifact.packet.assessment_allowed
            ledger.lock_initial(
                artifact.packet,
                reviewer_pseudonym=f"{reviewer}-{artifact.opaque_candidate_id}",
                action="relevant" if assessable else "unknown",
                assessable=assessable,
                rationale=(
                    "The linked excerpt supports the task criterion."
                    if assessable else "The pinned task context omits a decision-relevant fact."
                ),
                evidence_basis="retrospective" if assessable else "insufficient",
            )
    ledger_path = tmp_path / "restricted-review-events" / "events.jsonl"
    persist_review_ledger(loaded, ledger, ledger_path)
    reloaded = load_review_ledger(loaded, ledger_path)
    assert len(reloaded.events) == len(ledger.events)
    assert main([
        "packets", "verify", str(reviewer_path),
    ]) == 0
    verify_summary = json.loads(capsys.readouterr().out)
    assert verify_summary["assessable_candidate_packets"] == 10
    assert main([
        "packets", "metrics", str(run_path),
        "--reviewer-artifact", str(reviewer_path),
        "--restricted-crosswalk", str(crosswalk_path),
        "--review-ledger", str(ledger_path),
        "--authorization-reference", "offline integration test",
    ]) == 0
    metrics = json.loads(capsys.readouterr().out)
    assert metrics["primary"]["fixed_denominator_per_arm"] == 126
    assert metrics["primary"]["delta"] == 0
    assert metrics["slot_outcomes_fixed_126"]["skillscout"]["relevant"] == 30
    assert metrics["slot_outcomes_fixed_126"]["skillscout"]["unknown"] == 12


def test_integrity_malformed_schema_and_duplicate_keys_are_rejected(tmp_path):
    _, _, bundle = _build(tmp_path)
    path, _ = write_review_packet_artifacts(bundle, tmp_path / "r" / "packets.json", tmp_path / "x" / "crosswalk.json")
    mutated = json.loads(path.read_text(encoding="utf-8"))
    mutated["candidate_artifacts"][0]["packet"]["task_text"] = "tampered"
    path.write_text(json.dumps(mutated), encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(EvidencePacketError, match="integrity"):
        load_review_packet_artifacts(path)

    schema_path = tmp_path / "malformed-schema" / "packets.json"
    schema_path.parent.mkdir(mode=0o700)
    bad_schema_payload = bundle.public_payload()
    bad_schema_payload["schema_version"] = "unsupported-review-bundle-v9"
    canonical = json.dumps(bad_schema_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    bad_schema_payload["integrity_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    schema_path.write_text(json.dumps(bad_schema_payload, ensure_ascii=False), encoding="utf-8")
    os.chmod(schema_path, 0o600)
    with pytest.raises(EvidencePacketError, match="schema"):
        load_review_packet_artifacts(schema_path)

    incomplete_path = tmp_path / "incomplete" / "packets.json"
    incomplete_path.parent.mkdir(mode=0o700)
    incomplete_payload = bundle.public_payload()
    incomplete_payload["candidate_artifacts"][0].pop("retrieval_completed_at_utc")
    canonical = json.dumps(incomplete_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    incomplete_payload["integrity_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    incomplete_path.write_text(json.dumps(incomplete_payload, ensure_ascii=False), encoding="utf-8")
    os.chmod(incomplete_path, 0o600)
    with pytest.raises(EvidencePacketError, match="fields"):
        load_review_packet_artifacts(incomplete_path)

    forged_gate_path = tmp_path / "forged-gate" / "packets.json"
    forged_gate_path.parent.mkdir(mode=0o700)
    missing_bundle = _build(tmp_path, attachments={})[2]
    forged_gate_payload = missing_bundle.public_payload()
    target = next(item for item in forged_gate_payload["candidate_artifacts"] if item["retrieval_status"] == "missing")
    target["packet"]["evidence_status"] = "complete"
    target["packet"]["assessment_allowed"] = True
    canonical = json.dumps(forged_gate_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    forged_gate_payload["integrity_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    forged_gate_path.write_text(json.dumps(forged_gate_payload, ensure_ascii=False), encoding="utf-8")
    os.chmod(forged_gate_path, 0o600)
    with pytest.raises(EvidencePacketError, match="assessable reviewer packet"):
        load_review_packet_artifacts(forged_gate_path)
    valid_path, _ = write_review_packet_artifacts(
        bundle,
        tmp_path / "task-boundary-review" / "packets.json",
        tmp_path / "task-boundary-crosswalk" / "crosswalk.json",
    )
    with pytest.raises(EvidencePacketError, match="unchanged eligible task dataset"):
        load_review_packet_artifacts(valid_path, tasks=TASKS[:1])

    malformed = tmp_path / "malformed"
    malformed.mkdir(mode=0o700)
    malformed_path = malformed / "packets.json"
    malformed_path.write_text('{"a":1,"a":2}', encoding="utf-8")
    os.chmod(malformed_path, 0o600)
    with pytest.raises(EvidencePacketError, match="duplicate"):
        load_review_packet_artifacts(malformed_path)


def test_packet_and_crosswalk_paths_must_have_separate_access_boundaries(tmp_path):
    _, _, bundle = _build(tmp_path)
    same_dir = tmp_path / "same"
    with pytest.raises(EvidencePacketError, match="separate directories"):
        write_review_packet_artifacts(bundle, same_dir / "packets.json", same_dir / "crosswalk.json")


def test_ledger_gate_preserves_independent_unknown_assessments_and_primary_denominator(tmp_path):
    _, pairs, bundle = _build(tmp_path, attachments={})
    gated = next(item.packet for item in bundle.candidate_artifacts if item.retrieval_status is EvidenceStatus.MISSING)
    assert not gated.assessment_allowed
    ledger = ReviewLedger()
    for reviewer in ("reviewer-1", "reviewer-2"):
        with pytest.raises(ReviewError, match="evidence gate"):
            ledger.lock_initial(
                gated,
                reviewer_pseudonym=reviewer,
                action="relevant",
                assessable=True,
                rationale="insufficient detail remains unknown",
                evidence_basis="insufficient",
            )
        ledger.lock_initial(
            gated,
            reviewer_pseudonym=reviewer,
            action="unknown",
            assessable=False,
            rationale="insufficient detail remains unknown",
            evidence_basis="insufficient",
        )
    preserved = tuple(ledger.events)
    result = apply_reviews_and_derive_metrics(bundle, pairs, TASKS, ledger)
    assert tuple(ledger.events) == preserved
    assert ledger.candidate_resolution(gated.packet_key) == (ReviewResolution.RESOLVED, RelevanceLabel.UNKNOWN)
    assert result["metrics"]["primary"]["fixed_denominator_per_arm"] == 126
    assert result["metrics"]["primary"]["delta"] is not None
    precision = result["metrics"]["evaluable_slot_precision"]["skillscout"]
    assert precision["unknown"] + precision["unresolved_review"] > 0
    reloaded = ReviewLedger.from_records([event.to_dict() for event in ledger.events])
    assert len(reloaded.events) == 2


def test_review_integration_keeps_primary_estimand_and_protected_decisions_indeterminate(tmp_path):
    _, pairs, bundle = _build(
        tmp_path,
        decision_criteria_by_task={task.task_id: ("Use the approved task-level decision rubric.",) for task in TASKS},
        decision_assessable_pairs={
            f"p{plan.pair_number:03d}-t{plan.task_index:02d}-r{plan.repetition}"
            for plan in build_schedule(TASKS)
        },
    )
    ledger = ReviewLedger()
    for artifact in bundle.candidate_artifacts:
        packet = artifact.packet
        assessable = packet.assessment_allowed
        for reviewer in ("relevance-a", "relevance-b"):
            ledger.lock_initial(
                packet,
                reviewer_pseudonym=f"{reviewer}-{packet.packet_key}",
                action="relevant" if assessable else "unknown",
                assessable=assessable,
                rationale=("the linked detail supports the criterion" if assessable else "the fixture omits a required project fact"),
                evidence_basis="retrospective" if assessable else "insufficient",
            )
    for packet in bundle.decision_packets:
        for reviewer in ("decision-a", "decision-b"):
            ledger.lock_initial(
                packet,
                reviewer_pseudonym=f"{reviewer}-{packet.packet_key}",
                action="REVIEW" if packet.assessment_allowed else "unassessable",
                assessable=packet.assessment_allowed,
                rationale="the decision packet is reviewed under the approved test rubric",
                evidence_basis="mixed" if packet.assessment_allowed else "insufficient",
            )
    before = len(ledger.events)
    result = apply_reviews_and_derive_metrics(
        bundle,
        pairs,
        TASKS,
        ledger,
        task_gates_approved={task.task_id: True for task in TASKS},
    )
    assert len(ledger.events) == before
    metrics = result["metrics"]
    assert metrics["primary"]["fixed_denominator_per_arm"] == 126
    assert metrics["primary"]["planned_slots_per_repetition"] == 3
    assert metrics["primary"]["delta"] == 0
    assert metrics["primary"]["independent_interpretation_units"] == 14
    assert metrics["primary"]["repeated_pairs"] == 42
    protected_pair_ids = {item["pair_id"] for item in bundle.decision_gates if item["status"] == "indeterminate_protected_evidence_withheld"}
    assert protected_pair_ids
    assert all(result["decision_outcomes"][pair_id].value == "INDETERMINATE" for pair_id in protected_pair_ids)


def test_primary_metric_rejects_identity_mismatched_evidence_even_if_reviewed_relevant(tmp_path):
    _, pairs = _offline_records(tmp_path)
    first = pairs[0]
    slot = first.arms[ArmName.SKILLSCOUT].slots[0]
    slot.detail.returned_candidate_id = "different-candidate"
    bundle = EvidencePacketPipeline().build(
        pairs,
        TASKS,
        approved_context_by_task={task.task_id: "" for task in TASKS},
        attachments=_attachments(pairs),
    )
    ledger = ReviewLedger()
    for artifact in bundle.candidate_artifacts:
        for reviewer in ("mismatch-a", "mismatch-b"):
            assessable = artifact.packet.assessment_allowed
            ledger.lock_initial(
                artifact.packet,
                reviewer_pseudonym=f"{reviewer}-{artifact.opaque_candidate_id}",
                action="relevant" if assessable else "unknown",
                assessable=assessable,
                rationale=("the clean evidence supports the criterion" if assessable else "the fixture omits a required project fact"),
                evidence_basis="retrospective" if assessable else "insufficient",
            )
    result = apply_reviews_and_derive_metrics(bundle, pairs, TASKS, ledger)
    assert result["metrics"]["primary"]["fixed_denominator_per_arm"] == 126
    assert result["metrics"]["primary"]["delta"] == pytest.approx(-1 / 126)


def test_untrusted_error_metadata_and_unknown_fields_never_enter_serialized_artifacts(tmp_path):
    _, pairs = _offline_records(tmp_path)
    attachments = _attachments(pairs)
    first = next(iter(attachments))
    attachments[first].update({
        "exception": PROTECTED_AUDIT_TEXT[0],
        "error_message": "expected_decision: AVOID; task_category: confidential",
        "raw_body": "protected finding",
        "rank_order": 3,
        "scores": [0.99],
    })
    bundle = EvidencePacketPipeline().build(
        pairs,
        TASKS,
        approved_context_by_task={task.task_id: "" for task in TASKS},
        attachments=attachments,
    )
    rendered = json.dumps(bundle.public_payload(), ensure_ascii=False)
    for marker in (PROTECTED_AUDIT_TEXT[0], "expected_decision: AVOID", "confidential", "protected finding"):
        assert marker not in rendered
    reviewer_path, restricted_path = write_review_packet_artifacts(
        bundle,
        tmp_path / "error-reviewer" / "packets.json",
        tmp_path / "error-restricted" / "crosswalk.json",
    )
    serialized = reviewer_path.read_text(encoding="utf-8") + restricted_path.read_text(encoding="utf-8")
    for marker in (PROTECTED_AUDIT_TEXT[0], "expected_decision: AVOID", "confidential", "protected finding", "exception-leak-marker"):
        assert marker not in serialized


def test_credentials_in_restricted_evidence_are_never_serialized(tmp_path):
    _, pairs = _offline_records(tmp_path)
    attachments = _attachments(pairs)
    pair = pairs[0]
    arm = pair.arm_order[0]
    slot = pair.arms[arm].slots[0]
    attachments[(pair.pair_id, arm.value, slot.rank)]["excerpts"].append({
        "content": "audit finding includes api_key=private_credential_value",
        "evidence_type": "audit_finding",
    })
    bundle = EvidencePacketPipeline().build(
        pairs,
        TASKS,
        approved_context_by_task={task.task_id: "" for task in TASKS},
        attachments=attachments,
    )
    reviewer_path, restricted_path = write_review_packet_artifacts(
        bundle,
        tmp_path / "credential-reviewer" / "packets.json",
        tmp_path / "credential-restricted" / "crosswalk.json",
    )
    serialized = reviewer_path.read_text(encoding="utf-8") + restricted_path.read_text(encoding="utf-8")
    assert "private_credential_value" not in serialized


def test_legacy_packet_builder_also_fails_closed_on_arm_and_ranking_disclosures():
    from skillscout.evaluation.reviews import EvidenceExcerpt, build_candidate_relevance_packet

    for text in (
        "This candidate was returned by SkillScout.",
        "This candidate was returned by SkillScraper.",
        "Result rank #1; score 0.8; popularity 500.",
    ):
        with pytest.raises(ReviewError):
            build_candidate_relevance_packet(
                TASKS[0],
                approved_context="safe context",
                evidence=[EvidenceExcerpt(text)],
                packet_key_factory=lambda: "opaque-review-key",
            )

