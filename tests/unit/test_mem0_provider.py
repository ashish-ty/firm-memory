"""The mem0 provider translates canonical memories to and from mem0."""

import pytest

from firm_memory.errors import ProviderError
from firm_memory.models import Memory, MemoryStatus, MemoryTier
from firm_memory.providers.mem0 import Mem0Provider, Mem0Settings
from firm_memory.scope import MemoryScope
from firm_memory.taxonomy import MemoryType
from tests.conftest import make_memory

SETTINGS = Mem0Settings(pg_dsn="postgresql://mem0:pw@db.internal:5432/mem0")


class FakeMem0:
    """Records calls so we can assert on what the provider sends mem0."""

    def __init__(self, search_result=None, record=None, raises=None):
        self.add_calls = []
        self.search_calls = []
        self.update_calls = []
        self._search_result = search_result if search_result is not None else {"results": []}
        self._record = record
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

    def get(self, memory_id):
        if self._raises:
            raise self._raises
        return self._record

    def update(self, memory_id, **kwargs):
        if self._raises:
            raise self._raises
        self.update_calls.append((memory_id, kwargs))
        return {"message": "updated"}


def build(backend):
    return Mem0Provider(backend, settings=SETTINGS)


# --- writes ------------------------------------------------------------------


def test_insert_returns_the_memory_with_its_assigned_id():
    stored = build(FakeMem0()).insert(make_memory())
    assert stored.id == "m1"


def test_insert_does_not_re_infer_an_already_distilled_fact():
    """Re-extracting a summary loses the provenance the gate just attached."""
    fake = FakeMem0()
    build(fake).insert(make_memory())
    assert fake.add_calls[0][1]["infer"] is False


def test_inference_is_available_for_reconciling_a_near_duplicate():
    fake = FakeMem0()
    build(fake).insert(make_memory(), infer=True)
    assert fake.add_calls[0][1]["infer"] is True


def test_writes_are_scoped_without_any_caller_effort():
    fake = FakeMem0()
    build(fake).insert(make_memory(scope=MemoryScope.for_repo("oms")))
    _messages, kwargs = fake.add_calls[0]
    assert kwargs["agent_id"] == "oms"
    assert kwargs["metadata"]["scope_repo_oms"] == "1"


def test_a_multi_repo_memory_is_written_once_with_every_atom_recorded():
    """One fact must not become N copies that drift apart."""
    fake = FakeMem0()
    build(fake).insert(make_memory(scope=MemoryScope(repos=("oms", "gateway"))))

    assert len(fake.add_calls) == 1
    metadata = fake.add_calls[0][1]["metadata"]
    assert metadata["scope_repo_oms"] == "1" and metadata["scope_repo_gateway"] == "1"


def test_backend_write_failure_is_wrapped_with_context():
    with pytest.raises(ProviderError, match="write rejected"):
        build(FakeMem0(raises=RuntimeError("write rejected"))).insert(make_memory())


# --- reads -------------------------------------------------------------------


def test_search_sends_the_top_level_entity_key_mem0_requires():
    """Without one, mem0's own search() raises before touching the store."""
    fake = FakeMem0()
    build(fake).search("migrations", MemoryScope.for_query("oms"))

    _query, kwargs = fake.search_calls[0]
    assert any(key in kwargs["filters"] for key in ("user_id", "agent_id", "run_id"))


def test_search_unions_every_scope_atom_in_one_backend_call():
    fake = FakeMem0()
    build(fake).search("migrations", MemoryScope.for_query("oms", domains=("execution",)))

    assert len(fake.search_calls) == 1
    filters = fake.search_calls[0][1]["filters"]
    assert [branch for branch in filters["OR"]] == [
        {"scope_firm": "1", "tier": ["durable", "index"], "status": ["active", "disputed"]},
        {"scope_domain_execution": "1", "tier": ["durable", "index"], "status": ["active", "disputed"]},
        {"scope_repo_oms": "1", "tier": ["durable", "index"], "status": ["active", "disputed"]},
    ]


def test_search_excludes_episodic_memory_by_default():
    fake = FakeMem0()
    build(fake).search("q", MemoryScope.for_repo("oms"))
    assert "episodic" not in fake.search_calls[0][1]["filters"]["tier"]


def test_results_come_back_as_canonical_memories():
    record = {
        "id": "m9",
        "memory": "MCX orders route through Risk Engine A.",
        "score": 0.82,
        "metadata": {
            "type": "business_rules",
            "tier": "durable",
            "status": "active",
            "scope_domain_execution": "1",
            "scope_repo_oms": "1",
            "confidence": "0.9",
            "source": "interview",
            "reference": "mr-4821",
            "branch": "main",
        },
    }
    [memory] = build(FakeMem0(search_result={"results": [record]})).search("mcx", MemoryScope.for_repo("oms"))

    assert isinstance(memory, Memory)
    assert memory.id == "m9"
    assert memory.type is MemoryType.BUSINESS_RULE
    assert memory.scope == MemoryScope(domains=("execution",), repos=("oms",))
    assert memory.status is MemoryStatus.ACTIVE
    assert memory.confidence == 0.9
    assert memory.provenance.source == "interview"
    assert memory.provenance.reference == "mr-4821"
    assert memory.metadata["branch"] == "main"
    assert memory.score == 0.82


def test_a_bare_list_response_is_accepted():
    result = build(FakeMem0(search_result=[{"id": "m1", "memory": "x", "metadata": {"scope_firm": "1"}}]))
    assert len(result.search("q", MemoryScope.firm_wide())) == 1


def test_an_unexpected_response_shape_yields_nothing_rather_than_raising():
    assert build(FakeMem0(search_result=None)).search("q", MemoryScope.firm_wide()) == []


def test_one_malformed_row_does_not_lose_the_whole_recall():
    fake = FakeMem0(
        search_result={"results": [{"id": "bad"}, {"id": "m2", "memory": "ok", "metadata": {"scope_firm": "1"}}]}
    )
    assert [memory.id for memory in build(fake).search("q", MemoryScope.firm_wide())] == ["m2"]


def test_memory_written_before_the_platform_is_still_readable():
    """An existing pool must be queryable without a migration first."""
    record = {"id": "old", "memory": "We pin psycopg to 3.x", "user_id": "repo:acme-billing-svc"}
    memory = build(FakeMem0(record=record)).get("old")
    assert memory.scope == MemoryScope(repos=("acme-billing-svc",))


def test_a_stray_run_id_on_durable_memory_is_not_treated_as_a_task():
    record = {"id": "m1", "memory": "x", "run_id": "mr-1", "metadata": {"tier": "durable", "scope_firm": "1"}}
    assert build(FakeMem0(record=record)).get("m1").task is None


def test_an_episodic_record_keeps_its_task():
    record = {"id": "m1", "memory": "x", "run_id": "mr-1", "metadata": {"tier": "episodic", "scope_firm": "1"}}
    memory = build(FakeMem0(record=record)).get("m1")
    assert memory.tier is MemoryTier.EPISODIC and memory.task == "mr-1"


def test_get_returns_none_for_a_missing_memory():
    assert build(FakeMem0(record=None)).get("nope") is None


def test_backend_read_failure_is_wrapped_with_context():
    with pytest.raises(ProviderError, match="connection refused"):
        build(FakeMem0(raises=RuntimeError("connection refused"))).search("q", MemoryScope.firm_wide())


# --- updates -----------------------------------------------------------------


def test_update_sends_the_new_status_so_a_correction_is_visible_to_search():
    fake = FakeMem0()
    memory = make_memory(status=MemoryStatus.DISPUTED, id="m1")
    build(fake).update(memory)

    memory_id, kwargs = fake.update_calls[0]
    assert memory_id == "m1"
    assert kwargs["metadata"]["status"] == "disputed"


def test_updating_a_memory_that_was_never_inserted_is_refused():
    with pytest.raises(ProviderError, match="never been inserted"):
        build(FakeMem0()).update(make_memory())


# --- bootstrap ---------------------------------------------------------------


def test_from_settings_builds_the_backend_from_the_environment():
    from firm_memory.config import Settings

    captured = {}

    def factory(config):
        captured["config"] = config
        return FakeMem0()

    settings = Settings.from_env({"FIRM_MEM0_PG_DSN": "postgresql://mem0:pw@db.internal:5432/mem0"})
    provider = Mem0Provider.from_settings(settings, memory_factory=factory)

    assert provider.name == "mem0"
    assert captured["config"]["vector_store"]["provider"] == "pgvector"


def test_a_backend_that_will_not_start_fails_as_a_typed_error():
    from firm_memory.config import Settings

    def factory(_config):
        raise OSError("could not connect to pgvector")

    settings = Settings.from_env({"FIRM_MEM0_PG_DSN": "postgresql://mem0:pw@db.internal:5432/mem0"})
    with pytest.raises(ProviderError, match="could not connect"):
        Mem0Provider.from_settings(settings, memory_factory=factory)


def test_missing_backend_configuration_is_reported_before_anything_starts():
    from firm_memory.config import Settings
    from firm_memory.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="FIRM_MEM0_PG_DSN"):
        Mem0Provider.from_settings(Settings.from_env({}), memory_factory=lambda _c: FakeMem0())


def test_an_add_response_without_results_still_yields_a_usable_memory():
    class NoResults(FakeMem0):
        def add(self, messages, **kwargs):
            self.add_calls.append((messages, kwargs))
            return {"message": "ok"}

    stored = build(NoResults()).insert(make_memory())
    assert stored.id is None
