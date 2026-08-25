"""Candidates wait outside the provider, and must survive the process."""

import json

import pytest

from firm_memory.ingestion import InMemoryCandidateStore, JsonFileCandidateStore
from tests.conftest import make_memory

STORES = ("memory", "file")


@pytest.fixture(params=STORES)
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryCandidateStore()
    return JsonFileCandidateStore(tmp_path / "candidates.json")


def test_a_candidate_round_trips_intact(store):
    memory = make_memory(confidence=0.7, metadata={"branch": "main"})
    store.add("cand-1", memory)

    loaded = store.get("cand-1")
    assert loaded.to_dict() == memory.to_dict()


def test_removing_returns_the_candidate_and_empties_the_queue(store):
    store.add("cand-1", make_memory())
    assert store.remove("cand-1") is not None
    assert list(store.items()) == []


def test_removing_an_unknown_candidate_is_not_an_error(store):
    assert store.remove("nope") is None


def test_re_adding_the_same_id_replaces_rather_than_duplicates(store):
    store.add("cand-1", make_memory(content="first version of the fact"))
    store.add("cand-1", make_memory(content="second version of the fact"))

    [(_, memory)] = list(store.items())
    assert memory.content == "second version of the fact"


def test_the_file_store_survives_a_new_process(tmp_path):
    path = tmp_path / "candidates.json"
    JsonFileCandidateStore(path).add("cand-1", make_memory())

    assert JsonFileCandidateStore(path).get("cand-1") is not None


def test_an_unreadable_queue_file_is_treated_as_empty_not_fatal(tmp_path):
    """A corrupt queue must not stop the bot from reviewing an MR."""
    path = tmp_path / "candidates.json"
    path.write_text("{ this is not json", encoding="utf-8")

    assert list(JsonFileCandidateStore(path).items()) == []


def test_the_queue_file_is_written_as_readable_json(tmp_path):
    """An engineer should be able to read the queue without a tool."""
    path = tmp_path / "candidates.json"
    JsonFileCandidateStore(path).add("cand-1", make_memory(content="MCX orders route through Risk Engine A."))

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["cand-1"]["content"] == "MCX orders route through Risk Engine A."


def test_a_missing_parent_directory_is_created(tmp_path):
    store = JsonFileCandidateStore(tmp_path / "nested" / "dir" / "candidates.json")
    store.add("cand-1", make_memory())
    assert store.get("cand-1") is not None
