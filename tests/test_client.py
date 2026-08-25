"""The facade must make unscoped writes impossible and backend errors legible."""

import pytest

from firm_mem0.client import FirmMemory
from firm_mem0.config import Settings
from firm_mem0.errors import InvalidInputError, MemoryBackendError
from firm_mem0.namespace import Layer, Namespace

SETTINGS = Settings.from_env({"FIRM_MEM0_PG_DSN": "postgresql://mem0:pw@db.internal:5432/mem0"})
NS = Namespace(repo="acme-billing-svc")


class FakeMemory:
    """Records calls so we can assert on the scoping the facade injects."""

    def __init__(self, search_result=None, raises=None):
        self.add_calls = []
        self.search_calls = []
        self.delete_calls = []
        self._search_result = search_result if search_result is not None else {"results": []}
        self._raises = raises

    def add(self, messages, **kwargs):
        if self._raises:
            raise self._raises
        self.add_calls.append((messages, kwargs))
        return {"results": [{"id": "m1", "event": "ADD"}]}

    def search(self, query, **kwargs):
        if self._raises:
            raise self._raises
        self.search_calls.append((query, kwargs))
        return self._search_result

    def delete(self, memory_id):
        if self._raises:
            raise self._raises
        self.delete_calls.append(memory_id)
        return {"message": "deleted"}


def build(memory):
    return FirmMemory(memory=memory, namespace=NS, settings=SETTINGS)


def test_remember_injects_repo_scoping_by_default():
    fake = FakeMemory()
    build(fake).remember("We chose Kuzu over Neo4j to avoid a JVM in the sidecar")

    _messages, kwargs = fake.add_calls[0]
    assert kwargs["user_id"] == "repo:acme-billing-svc"
    assert kwargs["agent_id"] == "acme-billing-svc"


def test_remember_stamps_provenance_metadata():
    fake = FakeMemory()
    build(fake).remember("Ruff replaces black here", memory_type="coding_conventions", branch="main")

    _messages, kwargs = fake.add_calls[0]
    assert kwargs["metadata"]["type"] == "coding_conventions"
    assert kwargs["metadata"]["layer"] == "repo"
    assert kwargs["metadata"]["repo"] == "acme-billing-svc"
    assert kwargs["metadata"]["branch"] == "main"
    assert kwargs["metadata"]["source"] == "sdk"


def test_caller_can_override_source_but_not_scoping():
    fake = FakeMemory()
    build(fake).remember("issue thread decision", source="gitlab")

    _messages, kwargs = fake.add_calls[0]
    assert kwargs["metadata"]["source"] == "gitlab"
    assert kwargs["user_id"] == "repo:acme-billing-svc"


def test_remember_to_firm_layer_uses_the_firm_owner():
    fake = FakeMemory()
    build(fake).remember("Never use npm; this firm is pnpm-only", layer=Layer.FIRM)

    _messages, kwargs = fake.add_calls[0]
    assert kwargs["user_id"] == "firm"
    assert "agent_id" not in kwargs


def test_curated_facts_can_skip_inference():
    fake = FakeMemory()
    build(fake).remember("pnpm only", layer=Layer.FIRM, infer=False)
    assert fake.add_calls[0][1]["infer"] is False


def test_task_scoping_is_threaded_through():
    fake = FakeMemory()
    build(fake).remember("reviewer rejected the cache", task="gitlab-issue-4821")
    assert fake.add_calls[0][1]["run_id"] == "gitlab-issue-4821"


@pytest.mark.parametrize("bad", ["", "   ", None, []])
def test_remember_rejects_empty_content(bad):
    with pytest.raises(InvalidInputError):
        build(FakeMemory()).remember(bad)


def test_recall_unions_layers_in_one_search_call():
    fake = FakeMemory()
    build(fake).recall("how do we handle migrations")

    assert len(fake.search_calls) == 1
    _query, kwargs = fake.search_calls[0]
    assert "OR" in kwargs["filters"]
    assert kwargs["filters"]["OR"] == [
        {"user_id": "repo:acme-billing-svc", "agent_id": "acme-billing-svc"},
        {"user_id": "firm"},
    ]


def test_recall_applies_configured_defaults():
    fake = FakeMemory()
    build(fake).recall("migrations")

    _query, kwargs = fake.search_calls[0]
    assert kwargs["top_k"] == SETTINGS.default_top_k
    assert kwargs["threshold"] == SETTINGS.default_threshold
    assert kwargs["rerank"] is True


def test_recall_arguments_override_defaults():
    fake = FakeMemory()
    build(fake).recall("migrations", top_k=1, threshold=0.9, rerank=False)

    _query, kwargs = fake.search_calls[0]
    assert (kwargs["top_k"], kwargs["threshold"], kwargs["rerank"]) == (1, 0.9, False)


def test_recall_normalises_the_v1_1_envelope():
    fake = FakeMemory(search_result={"results": [{"id": "m1", "memory": "x"}]})
    assert build(fake).recall("q") == [{"id": "m1", "memory": "x"}]


def test_recall_accepts_a_bare_list_response():
    fake = FakeMemory(search_result=[{"id": "m1", "memory": "x"}])
    assert build(fake).recall("q") == [{"id": "m1", "memory": "x"}]


def test_recall_tolerates_an_unexpected_response_shape():
    assert build(FakeMemory(search_result=None)).recall("q") == []


@pytest.mark.parametrize("bad", ["", "   "])
def test_recall_rejects_empty_queries(bad):
    with pytest.raises(InvalidInputError):
        build(FakeMemory()).recall(bad)


def test_backend_failures_are_wrapped_with_context():
    fake = FakeMemory(raises=RuntimeError("connection refused"))
    with pytest.raises(MemoryBackendError, match="connection refused"):
        build(fake).recall("q")


def test_forget_delegates_to_the_backend():
    fake = FakeMemory()
    build(fake).forget("m1")
    assert fake.delete_calls == ["m1"]


def test_for_task_returns_a_scoped_facade_without_mutating_the_original():
    fake = FakeMemory()
    facade = build(fake)
    scoped = facade.for_task("gitlab-issue-7")

    assert scoped.namespace.task == "gitlab-issue-7"
    assert facade.namespace.task is None
    scoped.remember("something")
    assert fake.add_calls[0][1]["run_id"] == "gitlab-issue-7"
