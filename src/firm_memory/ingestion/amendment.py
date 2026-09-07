"""What a reviewer changes before endorsing a candidate.

An extracted fact is frequently 90% right: the rule is real but the wording is
the model's, the type is one category off, or the scope is narrower than the
fact actually is. Forcing such a candidate to be rejected and retyped by hand
loses the knowledge, which is the opposite of what the gate is for.

So the gate accepts an amendment. It is deliberately **not** a general edit
facility:

* Only the fields a reviewer can judge from the candidate itself are
  amendable — content, type, scope, confidence, and the two provenance pointers
  a person can verify.
* ``tier`` and ``task`` are not amendable. They are write-path facts decided by
  whatever produced the candidate, not review opinions, and changing them turns
  one kind of memory into another.
* Nothing is amended silently. The amended memory records who changed it and
  which fields moved, so a memory that reads oddly six months from now can be
  traced to the edit rather than blamed on the extractor.

Validation is not repeated here. Rebuilding the memory re-runs the canonical
model's own checks, so an amendment that puts a type outside the taxonomy or a
scope nobody can query fails exactly as a fresh proposal would.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..models import Memory
from ..scope import MemoryScope
from ..taxonomy import MemoryType

#: Metadata key naming the reviewer who amended a candidate.
AMENDED_BY = "amended_by"
#: Metadata key listing the amended fields, comma separated.
AMENDED_FIELDS = "amended_fields"


@dataclass(frozen=True, slots=True)
class Amendment:
    """A reviewer's edits to a candidate. ``None`` means "leave this alone"."""

    content: str | None = None
    type: str | MemoryType | None = None
    scope: MemoryScope | None = None
    confidence: float | None = None
    reference: str | None = None
    evidence: str | None = None

    @property
    def is_empty(self) -> bool:
        """Whether this amendment would change nothing."""
        return not self.changes()

    def changes(self) -> dict[str, object]:
        """The fields this amendment sets, keyed by name."""
        named = (
            ("content", self.content),
            ("type", self.type),
            ("scope", self.scope),
            ("confidence", self.confidence),
            ("reference", self.reference),
            ("evidence", self.evidence),
        )
        return {name: value for name, value in named if value is not None}

    def apply(self, memory: Memory, *, editor: str) -> Memory:
        """Return an amended copy of *memory*, recording the edit.

        The original is never mutated: the amended memory is a new value, and
        the candidate the extractor produced is still what was in the queue.
        """
        changes = self.changes()
        if not changes:
            return memory

        provenance = memory.provenance
        if "reference" in changes:
            provenance = replace(provenance, reference=str(changes.pop("reference")))
        if "evidence" in changes:
            provenance = replace(provenance, evidence=str(changes.pop("evidence")))

        metadata = dict(memory.metadata)
        metadata[AMENDED_BY] = editor
        metadata[AMENDED_FIELDS] = ",".join(sorted(self.changes()))

        # `replace` re-runs the canonical model's validation, so an amendment
        # cannot produce a memory a fresh proposal would have been refused.
        return replace(memory, provenance=provenance, metadata=metadata, **changes)

    @classmethod
    def from_dict(cls, raw: dict | None) -> Amendment:
        """Build an amendment from the JSON a review UI submits.

        Absent keys and explicit nulls both mean "unchanged". A blank string is
        treated the same way rather than as an instruction to clear a field: a
        reviewer emptying a text box is far more likely to have meant nothing by
        it than to have meant "erase this memory's content".
        """
        raw = raw or {}
        scope = raw.get("scope")
        return cls(
            content=_text(raw.get("content")),
            type=_text(raw.get("type")),
            scope=MemoryScope.from_dict(scope) if scope else None,
            confidence=None if raw.get("confidence") is None else float(raw["confidence"]),
            reference=_text(raw.get("reference")),
            evidence=_text(raw.get("evidence")),
        )


def _text(value: object) -> str | None:
    """Normalise a submitted string, treating blank as unchanged."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None
