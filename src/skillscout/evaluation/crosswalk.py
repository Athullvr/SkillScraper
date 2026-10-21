"""Restricted evaluator mapping from opaque review keys to candidate occurrences."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from skillscout.evaluation.protocol import ArmName


@dataclass(frozen=True, slots=True)
class CandidateCrosswalkEntry:
    opaque_candidate_key: str
    pair_id: str
    task_id: str
    repetition: int
    arm: ArmName
    rank: int
    stable_id: str
    source_url: str | None
    content_hash: str | None
    revision: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "arm": self.arm.value,
        }


@dataclass(frozen=True, slots=True)
class DecisionCrosswalkEntry:
    opaque_decision_packet_key: str
    pair_id: str
    task_id: str
    repetition: int
    arm: ArmName = ArmName.SKILLSCOUT

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "arm": self.arm.value,
        }


class CandidateCrosswalk:
    """Create reviewer keys and retain the evaluator-only identity crosswalk."""

    def __init__(self, *, key_factory: Callable[[], str] | None = None) -> None:
        self._key_factory = key_factory or (lambda: str(uuid.uuid4()))
        self._revision_keys: dict[tuple[str, str, str, str], str] = {}
        self._entries: list[CandidateCrosswalkEntry] = []

    @property
    def entries(self) -> tuple[CandidateCrosswalkEntry, ...]:
        return tuple(self._entries)

    def register(
        self,
        *,
        pair_id: str,
        task_id: str,
        repetition: int,
        arm: ArmName,
        rank: int,
        stable_id: str,
        source_url: str | None = None,
        content_hash: str | None = None,
        revision: str | None = None,
    ) -> str:
        if content_hash or revision:
            revision_key = (task_id, stable_id, content_hash or "", revision or "")
            opaque = self._revision_keys.get(revision_key)
            if opaque is None:
                opaque = self._key_factory()
                self._revision_keys[revision_key] = opaque
        else:
            # Identity alone is not sufficient to deduplicate reviewer work.
            opaque = self._key_factory()
        self._entries.append(
            CandidateCrosswalkEntry(
                opaque_candidate_key=opaque,
                pair_id=pair_id,
                task_id=task_id,
                repetition=repetition,
                arm=arm,
                rank=rank,
                stable_id=stable_id,
                source_url=_safe_source_url(source_url),
                content_hash=content_hash,
                revision=revision,
            )
        )
        return opaque


class DecisionCrosswalk:
    """Restricted mapping for one blinded task-action packet per pair."""

    def __init__(self, *, key_factory: Callable[[], str] | None = None) -> None:
        self._key_factory = key_factory or (lambda: str(uuid.uuid4()))
        self._entries: list[DecisionCrosswalkEntry] = []

    @property
    def entries(self) -> tuple[DecisionCrosswalkEntry, ...]:
        return tuple(self._entries)

    def register(self, *, pair_id: str, task_id: str, repetition: int) -> str:
        opaque = self._key_factory()
        self._entries.append(
            DecisionCrosswalkEntry(
                opaque_decision_packet_key=opaque,
                pair_id=pair_id,
                task_id=task_id,
                repetition=repetition,
            )
        )
        return opaque


def _safe_source_url(value: str | None) -> str | None:
    """Keep public source location while stripping userinfo, query, and fragment."""
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme.casefold(), host, parsed.path, "", ""))
    except ValueError:
        return None
