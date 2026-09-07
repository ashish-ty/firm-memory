"""One configured location decides where candidates wait."""

import pytest

from firm_memory.errors import ConfigurationError
from firm_memory.ingestion import InMemoryCandidateStore, JsonFileCandidateStore, PostgresCandidateStore
from firm_memory.ingestion.selection import candidate_store_from_url


def test_a_postgres_url_gives_the_queue_that_spans_machines():
    store = candidate_store_from_url("postgresql://mem0:pw@localhost:5432/mem0")
    assert isinstance(store, PostgresCandidateStore)


def test_the_postgres_alias_scheme_is_accepted_too():
    assert isinstance(candidate_store_from_url("postgres://mem0:pw@db/mem0"), PostgresCandidateStore)


def test_a_file_url_gives_the_single_host_queue(tmp_path):
    store = candidate_store_from_url(f"file://{tmp_path}/candidates.json")
    assert isinstance(store, JsonFileCandidateStore)


def test_a_bare_path_still_works_because_it_predates_the_url(tmp_path):
    assert isinstance(candidate_store_from_url(str(tmp_path / "candidates.json")), JsonFileCandidateStore)


def test_memory_is_selectable_for_a_single_process():
    assert isinstance(candidate_store_from_url("memory://"), InMemoryCandidateStore)


def test_an_unrecognised_scheme_is_refused_rather_than_quietly_downgraded():
    """A queue that silently becomes a dict accepts everything and loses it all."""
    with pytest.raises(ConfigurationError, match="Unsupported candidate queue location"):
        candidate_store_from_url("redis://localhost:6379")


def test_an_empty_location_is_refused():
    with pytest.raises(ConfigurationError, match="location is required"):
        candidate_store_from_url("   ")


def test_a_file_url_round_trips_a_candidate_to_the_named_path(tmp_path):
    from tests.conftest import make_memory

    path = tmp_path / "queue.json"
    candidate_store_from_url(f"file://{path}").add("cand-1", make_memory())

    assert path.exists()
