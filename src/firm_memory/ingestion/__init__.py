"""Candidate intake and the human approval gate.

Nothing reaches the provider without passing through here. Under the V1 posture
that gate is a person; the canonical model records confidence from the start so
the same gate can later admit high-confidence candidates automatically without
a migration.
"""

from __future__ import annotations

from .approval import ApprovalQueue, Candidate
from .store import CandidateStore, InMemoryCandidateStore, JsonFileCandidateStore

__all__ = [
    "ApprovalQueue",
    "Candidate",
    "CandidateStore",
    "InMemoryCandidateStore",
    "JsonFileCandidateStore",
]
