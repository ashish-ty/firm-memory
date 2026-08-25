"""Where a memory came from, and who stands behind it.

Provenance is what makes memory correctable rather than a black box: the bot
cites a memory id, an engineer follows the reference back to the MR, issue or
interview it came from, and either confirms or disputes it.

Provenance records a person only as **attribution**. It is never a scoping or
filtering axis — see :mod:`firm_memory.scope` for why the firm has no
per-engineer and no per-team memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from .errors import InvalidInputError


class Source:
    """Canonical values for :attr:`Provenance.source`.

    Not an enum: sources grow as ingestion paths are added, and an unknown
    source should not be a hard failure at the boundary.
    """

    SDK = "sdk"
    AGENT = "agent"
    INTERVIEW = "interview"
    DOCUMENT = "document"
    MERGE_REQUEST = "merge-request"
    ISSUE = "issue"
    REVERT = "revert"
    MIGRATION = "migration"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Immutable record of a memory's origin."""

    source: str = Source.SDK
    #: The MR iid, issue id, commit sha or interview session this came from.
    reference: str | None = None
    #: Who supplied or approved it. Attribution only — never used for scoping.
    author: str | None = None
    #: A path into the checkout the agent can open for full detail, so the
    #: memory can stay a compressed judgement instead of a copy of the document.
    evidence: str | None = None
    #: Content hash of a mutable source document, so a nightly sweep can tell
    #: whether the memory still reflects the file it was derived from.
    doc_sha: str | None = None
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        source = (self.source or "").strip()
        if not source:
            raise InvalidInputError("Provenance requires a source; memory without an origin cannot be corrected")
        object.__setattr__(self, "source", source)

    def with_author(self, author: str | None) -> Provenance:
        """Return a copy attributed to *author*."""
        return replace(self, author=author)

    def to_dict(self) -> dict:
        """Render the JSON form used on the MCP boundary and in exports."""
        return {
            "source": self.source,
            "reference": self.reference,
            "author": self.author,
            "evidence": self.evidence,
            "doc_sha": self.doc_sha,
            "recorded_at": self.recorded_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, raw: dict | None) -> Provenance:
        """Rebuild provenance from its JSON form."""
        raw = raw or {}
        recorded_at = raw.get("recorded_at")
        return cls(
            source=raw.get("source") or Source.SDK,
            reference=raw.get("reference"),
            author=raw.get("author"),
            evidence=raw.get("evidence"),
            doc_sha=raw.get("doc_sha"),
            recorded_at=_parse_timestamp(recorded_at),
        )


def _parse_timestamp(value: object) -> datetime:
    """Parse an ISO timestamp, falling back to now for absent or unusable input."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(UTC)
    return datetime.now(UTC)
