"""Candidates wait outside the provider, and must survive the process.

All three stores are held to one suite. That is the point of the parametrisation:
the Postgres store exists to carry a candidate from a CI job to an engineer's
review hours later, and it can only be swapped in for the others if it behaves
identically. The Postgres cases are skipped unless ``FIRM_MEMORY_TEST_PG_DSN``
names a reachable database — see the README's development section.
"""

import json
import os
import uuid

import pytest

from firm_memory.ingestion import InMemoryCandidateStore, JsonFileCandidateStore, PostgresCandidateStore
from tests.conftest import make_memory

STORES = ("memory", "file", "postgres")

TEST_DSN = os.environ.get("FIRM_MEMORY_TEST_PG_DSN")


@pytest.fixture(params=STORES)
def store(request, tmp_path):
    if request.param == "memory":
        yield InMemoryCandidateStore()
        return
    if request.param == "file":
        yield JsonFileCandidateStore(tmp_path / "candidates.json")
        return

    if not TEST_DSN:
        pytest.skip("FIRM_MEMORY_TEST_PG_DSN is not set; the Postgres queue is not exercised")
    # A table per test, so a failure cannot leak into the next one and the
    # suite never touches a real deployment's queue.
    table = f"test_candidates_{uuid.uuid4().hex}"
    try:
        yield PostgresCandidateStore(TEST_DSN, table=table)
    finally:
        _drop(TEST_DSN, table)


def _drop(dsn: str, table: str) -> None:
    import psycopg

    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {table}")


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


# --- the property only a shared queue has ------------------------------------


@pytest.mark.skipif(not TEST_DSN, reason="FIRM_MEMORY_TEST_PG_DSN is not set")
def test_the_postgres_queue_is_visible_to_a_second_process():
    """The whole reason it exists: CI proposes, an engineer approves elsewhere."""
    table = f"test_candidates_{uuid.uuid4().hex}"
    try:
        PostgresCandidateStore(TEST_DSN, table=table).add("cand-1", make_memory())

        # A separate instance, as a separate process would build.
        assert PostgresCandidateStore(TEST_DSN, table=table).get("cand-1") is not None
    finally:
        _drop(TEST_DSN, table)


@pytest.mark.skipif(not TEST_DSN, reason="FIRM_MEMORY_TEST_PG_DSN is not set")
def test_only_one_of_two_reviewers_can_claim_the_same_candidate():
    """Approval is a race exactly one reviewer wins; the other is told it is gone."""
    table = f"test_candidates_{uuid.uuid4().hex}"
    try:
        PostgresCandidateStore(TEST_DSN, table=table).add("cand-1", make_memory())

        first = PostgresCandidateStore(TEST_DSN, table=table).remove("cand-1")
        second = PostgresCandidateStore(TEST_DSN, table=table).remove("cand-1")

        assert first is not None
        assert second is None
    finally:
        _drop(TEST_DSN, table)
