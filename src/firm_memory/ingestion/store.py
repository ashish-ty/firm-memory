"""Where candidate memories wait for a human.

Candidates are held **outside** the provider on purpose. A proposal is not firm
knowledge yet, and mixing unreviewed candidates into the retrievable pool would
mean every recall is one status-filter bug away from returning things nobody
approved.

Two implementations. The in-memory one is for tests and for a single long-lived
process. The JSON-file one is what a stateless bot needs: an agent proposing
during an MR review and an engineer approving hours later are different
processes, so the queue has to outlive both.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol, runtime_checkable

from ..models import Memory


@runtime_checkable
class CandidateStore(Protocol):
    """Holds proposed memories until they are approved or rejected."""

    def add(self, candidate_id: str, memory: Memory) -> None:
        """Record *memory* as awaiting review under *candidate_id*."""
        ...

    def get(self, candidate_id: str) -> Memory | None:
        """Return the candidate with *candidate_id*, or ``None``."""
        ...

    def remove(self, candidate_id: str) -> Memory | None:
        """Remove and return the candidate with *candidate_id*, or ``None``."""
        ...

    def items(self) -> Iterator[tuple[str, Memory]]:
        """Yield every pending candidate as ``(candidate_id, memory)``."""
        ...


class InMemoryCandidateStore:
    """Candidates held in a dict. Lost when the process exits."""

    def __init__(self, initial: Iterable[tuple[str, Memory]] = ()) -> None:
        self._pending: dict[str, Memory] = dict(initial)

    def add(self, candidate_id: str, memory: Memory) -> None:
        """Record *memory* as awaiting review."""
        self._pending[candidate_id] = memory

    def get(self, candidate_id: str) -> Memory | None:
        """Return the candidate, or ``None``."""
        return self._pending.get(candidate_id)

    def remove(self, candidate_id: str) -> Memory | None:
        """Remove and return the candidate, or ``None``."""
        return self._pending.pop(candidate_id, None)

    def items(self) -> Iterator[tuple[str, Memory]]:
        """Yield every pending candidate."""
        yield from tuple(self._pending.items())


class JsonFileCandidateStore:
    """Candidates persisted as one JSON object, keyed by candidate id.

    Written atomically (temp file plus rename) so a crash mid-write cannot leave
    an unreadable queue, which would silently drop everything proposed so far.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    def add(self, candidate_id: str, memory: Memory) -> None:
        """Record *memory* as awaiting review."""
        pending = self._read()
        pending[candidate_id] = memory.to_dict()
        self._write(pending)

    def get(self, candidate_id: str) -> Memory | None:
        """Return the candidate, or ``None``."""
        raw = self._read().get(candidate_id)
        return Memory.from_dict(raw) if raw else None

    def remove(self, candidate_id: str) -> Memory | None:
        """Remove and return the candidate, or ``None``."""
        pending = self._read()
        raw = pending.pop(candidate_id, None)
        if raw is None:
            return None
        self._write(pending)
        return Memory.from_dict(raw)

    def items(self) -> Iterator[tuple[str, Memory]]:
        """Yield every pending candidate."""
        for candidate_id, raw in self._read().items():
            yield candidate_id, Memory.from_dict(raw)

    # --- file handling -------------------------------------------------------

    def _read(self) -> dict:
        """Load the queue, treating an unreadable file as empty rather than fatal."""
        if not self._path.exists():
            return {}
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _write(self, pending: dict) -> None:
        """Replace the queue file atomically."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=self._path.parent, prefix=self._path.name, suffix=".tmp", delete=False
        ) as handle:
            json.dump(pending, handle, indent=2, sort_keys=True)
            temp_path = Path(handle.name)
        temp_path.replace(self._path)
