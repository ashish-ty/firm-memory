"""The completion client: deterministic, and never fatal to the caller."""

import pytest

from firm_memory.errors import ExtractionError
from firm_memory.ingestion.llm import LiteLLMClient

RESPONSE = '{"facts": [{"content": "Cash strategies stop at 15:20.", "type": "business_rules"}]}'


class FakeCompletion:
    """Stands in for litellm.completion, recording the params it was passed."""

    def __init__(self, content=RESPONSE, raises=None):
        self.params = None
        self._content = content
        self._raises = raises

    def __call__(self, **params):
        if self._raises:
            raise self._raises
        self.params = params
        message = type("Message", (), {"content": self._content})
        choice = type("Choice", (), {"message": message})
        return type("Response", (), {"choices": [choice]})


def build(**kwargs):
    completion = FakeCompletion(**kwargs)
    return LiteLLMClient("gpt-4o-mini", completion=completion), completion


def test_the_model_is_passed_through_verbatim():
    client, completion = build()
    client.complete_json("system", "user")
    assert completion.params["model"] == "gpt-4o-mini"


def test_extraction_is_deterministic():
    """The same MR re-reviewed must not yield different candidates each run."""
    client, completion = build()
    client.complete_json("system", "user")
    assert completion.params["temperature"] == 0.0


def test_json_mode_is_requested():
    client, completion = build()
    client.complete_json("system", "user")
    assert completion.params["response_format"] == {"type": "json_object"}


def test_credentials_are_omitted_unless_given():
    """LiteLLM resolves them from its own environment, as mem0's class does."""
    client, completion = build()
    client.complete_json("system", "user")
    assert "api_key" not in completion.params
    assert "api_base" not in completion.params


def test_an_explicit_gateway_is_passed_through():
    completion = FakeCompletion()
    client = LiteLLMClient(
        "litellm_proxy/gpt-4o-mini",
        api_key="sk-proxy",
        api_base="https://llm.internal/v1",
        completion=completion,
    )
    client.complete_json("system", "user")

    assert completion.params["api_key"] == "sk-proxy"
    assert completion.params["api_base"] == "https://llm.internal/v1"


def test_a_json_object_is_returned_parsed():
    client, _ = build()
    assert client.complete_json("s", "u")["facts"][0]["type"] == "business_rules"


def test_a_fenced_response_is_still_parsed():
    """Models wrap JSON in a code fence regardless of what they were asked."""
    client, _ = build(content=f"```json\n{RESPONSE}\n```")
    assert client.complete_json("s", "u")["facts"]


@pytest.mark.parametrize("bad", ["", "   ", "not json at all", "[1, 2, 3]"])
def test_an_unusable_response_is_a_typed_error(bad):
    client, _ = build(content=bad)
    with pytest.raises(ExtractionError):
        client.complete_json("s", "u")


def test_a_transport_failure_is_wrapped_with_context():
    client, _ = build(raises=RuntimeError("gateway timeout"))
    with pytest.raises(ExtractionError, match="gateway timeout"):
        client.complete_json("s", "u")


def test_building_a_client_does_not_require_litellm():
    """A deployment that never ingests must not be made to install it."""
    client = LiteLLMClient("openrouter/anthropic/claude-3.5-sonnet")
    assert client is not None
