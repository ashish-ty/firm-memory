"""Configuration for the review service.

The token does two jobs: it grants access, and it says who is approving.

Binding those together is deliberate. Approval is the moment a proposal becomes
firm knowledge, and the firm may need to revisit that decision years later — so
it has to be attributable. A typed-in name is a claim; a token issued to one
person is a fact. Removing the name box and reading the reviewer from the token
makes attribution stronger, not weaker, and takes a step out of every review.

This is attribution, not scoping. A memory is owned by the repo and the firm,
never by a person: the reviewer's name lands in ``Provenance.author`` so a
memory can be traced and corrected, and never in a filter.

Two defaults that fail safe:

* **A token is required.** The queue holds unreviewed claims about the firm's
  trading systems and approving one is a write to firm knowledge, so the service
  refuses to start rather than run unauthenticated.
* **It binds to localhost.** Exposing the service is an explicit act. A default
  of ``0.0.0.0`` is how an internal tool ends up reachable from the office
  network without anyone deciding that it should be.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ..errors import ConfigurationError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
#: Requests per minute per client. Generous for a person clicking through a
#: queue, and low enough that a stolen endpoint cannot be hammered.
DEFAULT_RATE_LIMIT = 120
#: A review token authorises writes to firm knowledge. 32 hex characters is
#: 128 bits; the generator suggested everywhere issues 64.
MIN_TOKEN_LENGTH = 32

_TOKENS = "FIRM_MEMORY_REVIEW_TOKENS"
_TOKEN = "FIRM_MEMORY_REVIEW_TOKEN"
_APPROVER = "FIRM_MEMORY_REVIEW_APPROVER"

_GENERATE = "  openssl rand -hex 32"


@dataclass(frozen=True, slots=True)
class ReviewSettings:
    """Validated configuration for the review service."""

    #: Token -> the reviewer it was issued to. Every approval and rejection is
    #: attributed to the name the presented token maps to.
    reviewers: Mapping[str, str] = field(default_factory=dict)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT

    def __post_init__(self) -> None:
        object.__setattr__(self, "reviewers", MappingProxyType(dict(self.reviewers)))

    @classmethod
    def for_reviewer(cls, name: str, token: str, **overrides) -> ReviewSettings:
        """Settings for a single named reviewer. Mostly for tests and one-person setups."""
        return cls(reviewers={token: name}, **overrides)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ReviewSettings:
        """Build settings, failing fast on anything that would run insecurely."""
        env = env if env is not None else os.environ

        return cls(
            reviewers=_reviewers(env),
            host=(env.get("FIRM_MEMORY_REVIEW_HOST") or DEFAULT_HOST).strip(),
            port=_port(env),
            rate_limit_per_minute=_rate_limit(env),
        )

    def reviewer_for(self, token: str) -> str | None:
        """The reviewer *token* was issued to, or ``None`` if it was not issued."""
        return self.reviewers.get(token)


def _reviewers(env: Mapping[str, str]) -> Mapping[str, str]:
    """Parse the configured tokens into token -> reviewer.

    Two spellings, because a one-person setup should not have to learn the
    multi-reviewer one:

        FIRM_MEMORY_REVIEW_TOKENS="ashish:8f2c…,priya:1a9b…"
        FIRM_MEMORY_REVIEW_TOKEN=8f2c…   FIRM_MEMORY_REVIEW_APPROVER=ashish
    """
    listed = (env.get(_TOKENS) or "").strip()
    if listed:
        return _parse_pairs(listed)

    single = (env.get(_TOKEN) or "").strip()
    if not single:
        raise ConfigurationError(
            f"{_TOKENS} or {_TOKEN} is required: the review service approves writes to firm "
            "knowledge and will not start unauthenticated. Generate a token with:\n\n"
            f"{_GENERATE}\n\n"
            f"Then either\n"
            f"  {_TOKEN}=<token>   {_APPROVER}=<your name>\n"
            f"or, for a team, one token each:\n"
            f'  {_TOKENS}="ashish:<token>,priya:<token>"'
        )

    approver = (env.get(_APPROVER) or "").strip()
    if not approver:
        raise ConfigurationError(
            f"{_APPROVER} is required alongside {_TOKEN}: every approval is attributed to a "
            "person, and the token is what says which one. Set it to the reviewer's name, or "
            f'use {_TOKENS}="name:token,…" to issue one token per reviewer.'
        )

    _check_token(approver, single)
    return {single: approver}


def _parse_pairs(raw: str) -> Mapping[str, str]:
    """Parse ``name:token,name:token`` into token -> name."""
    reviewers: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, separator, token = entry.partition(":")
        if not separator:
            raise ConfigurationError(
                f"{_TOKENS} entry {entry!r} is not 'name:token'. "
                f'Expected {_TOKENS}="ashish:<token>,priya:<token>"'
            )
        name, token = name.strip(), token.strip()
        if not name:
            raise ConfigurationError(f"{_TOKENS} has a token with no reviewer name: {entry!r}")
        _check_token(name, token)
        if token in reviewers:
            # Two people behind one token means an approval cannot be traced to
            # either of them, which is the one thing the token is here to do.
            raise ConfigurationError(
                f"{_TOKENS} gives the same token to {reviewers[token]!r} and {name!r}. "
                "One token per reviewer, or approvals cannot be attributed."
            )
        reviewers[token] = name

    if not reviewers:
        raise ConfigurationError(f"{_TOKENS} is set but lists no reviewers.")
    return reviewers


def _check_token(name: str, token: str) -> None:
    """Refuse a token too short to be worth guarding."""
    if not token:
        raise ConfigurationError(f"The review token for {name!r} is empty. Generate one with:\n{_GENERATE}")
    if len(token) < MIN_TOKEN_LENGTH:
        raise ConfigurationError(
            f"The review token for {name!r} is {len(token)} characters; at least "
            f"{MIN_TOKEN_LENGTH} are required. Generate one with:\n{_GENERATE}"
        )


def _port(env: Mapping[str, str]) -> int:
    raw = (env.get("FIRM_MEMORY_REVIEW_PORT") or "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"FIRM_MEMORY_REVIEW_PORT must be an integer, got {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise ConfigurationError(f"FIRM_MEMORY_REVIEW_PORT must be a valid port, got {port}")
    return port


def _rate_limit(env: Mapping[str, str]) -> int:
    raw = (env.get("FIRM_MEMORY_REVIEW_RATE_LIMIT") or "").strip()
    if not raw:
        return DEFAULT_RATE_LIMIT
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"FIRM_MEMORY_REVIEW_RATE_LIMIT must be an integer, got {raw!r}") from exc
    if limit <= 0:
        raise ConfigurationError(f"FIRM_MEMORY_REVIEW_RATE_LIMIT must be greater than 0, got {limit}")
    return limit
