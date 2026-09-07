"""Candidate intake and the human approval gate.

Nothing reaches the provider without passing through here. Under the V1 posture
that gate is a person; the canonical model records confidence from the start so
the same gate can later admit high-confidence candidates automatically without
a migration.
"""

from __future__ import annotations

from .amendment import Amendment
from .approval import ApprovalQueue, Candidate
from .extraction import FactExtractor, LLMFactExtractor, SourceDocument, extract_all
from .llm import CompletionClient, LiteLLMClient
from .postgres_store import PostgresCandidateStore
from .store import CandidateStore, InMemoryCandidateStore, JsonFileCandidateStore

__all__ = [
    "Amendment",
    "ApprovalQueue",
    "Candidate",
    "CandidateStore",
    "CompletionClient",
    "FactExtractor",
    "InMemoryCandidateStore",
    "JsonFileCandidateStore",
    "LLMFactExtractor",
    "LiteLLMClient",
    "PostgresCandidateStore",
    "SourceDocument",
    "extract_all",
]
