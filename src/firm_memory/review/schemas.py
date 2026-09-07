"""What the review UI is allowed to send.

Validation at the boundary, before anything reaches the gate. The models are
deliberately narrow: a reviewer may correct a candidate's content, its type, its
scope and its confidence, and nothing else. ``tier`` and ``task`` are absent
because they describe how a memory was written rather than what a reviewer
thinks of it, and letting a form change them would turn one kind of memory into
another by accident.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: Long enough for a real engineering fact, short enough that the box cannot be
#: used to paste a diff or a stack trace into the pool.
MAX_CONTENT = 4_000
MAX_REASON = 1_000


class ScopeInput(BaseModel):
    """A corrected scope. Validated properly by ``MemoryScope`` behind this."""

    model_config = ConfigDict(extra="forbid")

    firm: bool = False
    domains: list[str] = Field(default_factory=list, max_length=32)
    repos: list[str] = Field(default_factory=list, max_length=32)


class AmendmentInput(BaseModel):
    """The reviewer's edits. Every field optional: absent means unchanged."""

    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, max_length=MAX_CONTENT)
    type: str | None = Field(default=None, max_length=64)
    scope: ScopeInput | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reference: str | None = Field(default=None, max_length=256)
    evidence: str | None = Field(default=None, max_length=512)


class ApproveRequest(BaseModel):
    """Endorse a candidate, optionally correcting it on the way through.

    There is deliberately no ``approver`` field. Who is approving comes from the
    token on the request, not from the body — a name a client could put there is
    a claim, and approval is the one decision that has to be a fact.
    """

    model_config = ConfigDict(extra="forbid")

    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    amendment: AmendmentInput | None = None


class RejectRequest(BaseModel):
    """Turn a candidate down. The reviewer comes from the token, as above."""

    model_config = ConfigDict(extra="forbid")

    #: Why, so the same fact arriving again can be recognised as already refused.
    reason: str | None = Field(default=None, max_length=MAX_REASON)
