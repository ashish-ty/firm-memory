"""Platform configuration.

Two levels of settings, kept apart on purpose:

* **Platform** (``FIRM_MEMORY_*``) — which provider to use, how long to wait for
  it, how much to return, and the approval posture. Provider-independent.
* **Provider** (e.g. ``FIRM_MEM0_*``) — connection strings, models, collections.
  Read by the provider itself, from the same environment.

That split is what keeps a provider swap a configuration change. Nothing in the
platform layer may name pgvector, an embedding model or a mem0 entity axis.

The legacy ``FIRM_MEM0_TOP_K`` / ``FIRM_MEM0_THRESHOLD`` names are still honoured
as fallbacks so an existing deployment does not silently change behaviour on
upgrade.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .errors import ConfigurationError
from .lifecycle import DEFAULT_AUTO_APPROVE_THRESHOLD, ApprovalPolicy

DEFAULT_PROVIDER = "mem0"
#: Reuse the provider's own extractor by default. It is the reason to run a
#: memory framework at all, and it deduplicates against what is already stored.
DEFAULT_EXTRACTOR = "provider"
DEFAULT_LIMIT = 5
DEFAULT_MIN_SCORE = 0.3
#: Memory is best effort and must never hold up a review (HLD §11). Two seconds
#: is long enough for an embed plus a vector query, and short enough that a
#: degraded provider is invisible to the agent.
DEFAULT_TIMEOUT_SECONDS = 2.0

_FALSEY = frozenset({"0", "off", "false", "no"})


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated platform configuration."""

    provider: str = DEFAULT_PROVIDER
    default_limit: int = DEFAULT_LIMIT
    #: Relevance floor. Note that a hybrid provider's scores are compressed:
    #: mem0 divides the combined score by the number of signals in play (2.0
    #: with keyword search, 2.5 with entity boosts too), so a memory with a
    #: strong semantic match of 0.8 and no keyword hit surfaces as 0.4. Tune
    #: this against observed scores, not against cosine similarity intuitions.
    min_score: float = DEFAULT_MIN_SCORE
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    auto_approve_enabled: bool = False
    auto_approve_threshold: float = DEFAULT_AUTO_APPROVE_THRESHOLD
    #: Domains this checkout belongs to, so a query can reach cross-repo
    #: knowledge (Gateway -> Risk -> OMS) that no single repo owns.
    default_domains: tuple[str, ...] = ()
    #: Where proposed memories wait for a human. A stateless bot needs this to
    #: be a path: the agent that proposes and the engineer who approves are
    #: different processes, hours apart.
    candidates_path: str | None = None
    #: Where the queue lives, as a URL whose scheme picks the store. Set this
    #: when the two processes are on *different machines*, which is the real
    #: deployment: the bot proposes from a CI job that ends minutes later, and
    #: the engineer approves elsewhere hours after. Takes precedence over
    #: ``candidates_path``, which it generalises.
    candidates_url: str | None = None
    #: Which extractor distils raw material into candidates. ``"provider"``
    #: reuses the memory provider's own extractor — for mem0 that means its
    #: dedup-aware extraction, minus its write. ``"llm"`` uses the platform's
    #: own prompt, built for engineering memory from the start. ``"none"``
    #: disables ingestion, which is a valid deployment: search and hand-curated
    #: memory work without it.
    extractor: str = DEFAULT_EXTRACTOR
    #: The model the ``"llm"`` extractor calls. Unused by ``"provider"``, which
    #: goes through the provider's configured LLM.
    extraction_model: str | None = None
    extraction_api_key: str | None = None
    extraction_api_base: str | None = None
    #: The environment the selected provider reads its own configuration from.
    env: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from the environment, failing fast on anything unusable.

        No caller identity is read. Memory is owned by the repo and the firm,
        never by the person or team that triggered the call.
        """
        env = dict(env if env is not None else os.environ)

        return cls(
            provider=(env.get("FIRM_MEMORY_PROVIDER") or DEFAULT_PROVIDER).strip(),
            default_limit=_positive_int(env, "FIRM_MEMORY_LIMIT", "FIRM_MEM0_TOP_K", default=DEFAULT_LIMIT),
            min_score=_probability(env, "FIRM_MEMORY_MIN_SCORE", "FIRM_MEM0_THRESHOLD", default=DEFAULT_MIN_SCORE),
            timeout_seconds=_positive_float(env, "FIRM_MEMORY_TIMEOUT_SECONDS", default=DEFAULT_TIMEOUT_SECONDS),
            auto_approve_enabled=_flag(env, "FIRM_MEMORY_AUTO_APPROVE", default=False),
            auto_approve_threshold=_probability(
                env, "FIRM_MEMORY_AUTO_APPROVE_THRESHOLD", default=DEFAULT_AUTO_APPROVE_THRESHOLD
            ),
            default_domains=_slug_list(env.get("FIRM_MEMORY_DOMAINS")),
            candidates_path=(env.get("FIRM_MEMORY_CANDIDATES_PATH") or "").strip() or None,
            candidates_url=(env.get("FIRM_MEMORY_CANDIDATES_URL") or "").strip() or None,
            extractor=(env.get("FIRM_MEMORY_EXTRACTOR") or DEFAULT_EXTRACTOR).strip().lower(),
            extraction_model=(env.get("FIRM_MEMORY_EXTRACTION_MODEL") or "").strip() or None,
            extraction_api_key=(env.get("FIRM_MEMORY_EXTRACTION_API_KEY") or "").strip() or None,
            extraction_api_base=(env.get("FIRM_MEMORY_EXTRACTION_API_BASE") or "").strip() or None,
            env=MappingProxyType(env),
        )

    @property
    def approval_policy(self) -> ApprovalPolicy:
        """The approval gate implied by these settings."""
        return ApprovalPolicy(
            auto_approve_enabled=self.auto_approve_enabled,
            auto_approve_threshold=self.auto_approve_threshold,
        )


def _slug_list(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated list, dropping blanks."""
    return tuple(part.strip().lower() for part in (raw or "").split(",") if part.strip())


def _flag(env: Mapping[str, str], key: str, *, default: bool) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw not in _FALSEY


def _first(env: Mapping[str, str], *keys: str) -> tuple[str, str] | None:
    """Return the first (key, value) present, so error messages name the real variable."""
    for key in keys:
        raw = (env.get(key) or "").strip()
        if raw:
            return key, raw
    return None


def _positive_int(env: Mapping[str, str], *keys: str, default: int) -> int:
    found = _first(env, *keys)
    if found is None:
        return default
    key, raw = found
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigurationError(f"{key} must be greater than 0, got {value}")
    return value


def _positive_float(env: Mapping[str, str], *keys: str, default: float) -> float:
    found = _first(env, *keys)
    if found is None:
        return default
    key, raw = found
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ConfigurationError(f"{key} must be greater than 0, got {value}")
    return value


def _probability(env: Mapping[str, str], *keys: str, default: float) -> float:
    found = _first(env, *keys)
    if found is None:
        return default
    key, raw = found
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be a number, got {raw!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise ConfigurationError(f"{key} must be between 0.0 and 1.0, got {value}")
    return value
