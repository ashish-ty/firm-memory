"""The completion client used to distil raw material into candidate facts.

Extraction is a **platform** concern, not a provider one. The prompt is built
from the firm's taxonomy, so whoever runs it must speak the firm's vocabulary —
and the result has to land in the approval queue rather than in the pool. Asking
the memory provider to extract would do the opposite: mem0's own extraction runs
inside ``add()``, which writes. That is precisely the direct-ingestion path this
platform does not have.

So the client lives here, behind a two-method protocol, and LiteLLM is the
default implementation because it fronts every provider through one key.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

from ..errors import ConfigurationError, ExtractionError

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 2000


@runtime_checkable
class CompletionClient(Protocol):
    """Turns a system + user prompt into a JSON object."""

    def complete_json(self, system: str, user: str) -> dict:
        """Return the model's response parsed as a JSON object."""
        ...


class LiteLLMClient:
    """A completion client over ``litellm.completion``.

    LiteLLM is the default because one gateway key reaches every provider: the
    firm can change model without changing this package, and without a second
    credential appearing in the deployment.

    Credentials are **not** passed as arguments unless given. LiteLLM resolves
    them from its own environment variables, which is also how mem0's LiteLLM
    class behaves — so a single environment configures both.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        completion: Any = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Resolved on first use, not here: building a FirmMemory must not
        # require an LLM client that a deployment may never call.
        self._completion = completion

    def complete_json(self, system: str, user: str) -> dict:
        """Ask the model for a JSON object and return it parsed."""
        params: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Extraction must be reproducible: the same MR re-reviewed should
            # not yield a different set of candidates each run.
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self._api_key:
            params["api_key"] = self._api_key
        if self._api_base:
            params["api_base"] = self._api_base

        if self._completion is None:
            self._completion = _litellm_completion()

        try:
            response = self._completion(**params)
            content = response.choices[0].message.content
        except Exception as exc:
            raise ExtractionError(f"The extraction model call failed: {exc}") from exc

        return _parse_object(content)


def _parse_object(content: str | None) -> dict:
    """Parse a JSON object, tolerating the code fence models like to add."""
    text = (content or "").strip()
    if not text:
        raise ExtractionError("The extraction model returned an empty response")

    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"The extraction model returned unparseable JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ExtractionError(f"Expected a JSON object from the extraction model, got {type(parsed).__name__}")
    return parsed


def _litellm_completion() -> Any:
    """Import litellm lazily, so it is only required by consumers that ingest."""
    try:
        from litellm import completion
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise ConfigurationError(
            "Fact extraction requires the 'litellm' package. Install it with: uv sync"
        ) from exc
    return completion
