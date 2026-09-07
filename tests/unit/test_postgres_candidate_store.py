"""The queue that has to span two machines.

The behavioural contract is asserted against a real database in
``test_candidate_store.py``, which parametrises all three stores over the same
suite. What is tested here is the SQL shape the design depends on — the atomic
claim, the upsert, the ordering — plus the failure behaviour, none of which a
round-trip test can distinguish from a slower implementation that gets them
wrong.
"""

import json

import pytest

from firm_memory.errors import CandidateStoreError, ConfigurationError
from firm_memory.ingestion import PostgresCandidateStore
from tests.conftest import make_memory

DSN = "postgresql://mem0:pw@localhost:5432/mem0"


class FakeCursor:
    def __init__(self, log: list, rows: list):
        self._log = log
        self._rows = rows

    def execute(self, sql, params=()):
        self._log.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    """Enough of psycopg's shape to record what the store asks for."""

    def __init__(self, log: list, rows: list):
        self._log = log
        self._rows = rows

    def cursor(self):
        return FakeCursor(self._log, self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def log() -> list:
    return []


def build(log, rows=()) -> PostgresCandidateStore:
    return PostgresCandidateStore(DSN, connect=lambda dsn: FakeConnection(log, list(rows)))


def statements(log) -> list[str]:
    return [sql for sql, _ in log]


# --- configuration -----------------------------------------------------------


def test_a_connection_string_is_required():
    with pytest.raises(ConfigurationError, match="connection string"):
        PostgresCandidateStore("   ")


def test_a_table_name_that_could_carry_sql_is_refused():
    """The table is interpolated into DDL, which no parameter binding covers."""
    with pytest.raises(ConfigurationError, match="Invalid candidate table name"):
        PostgresCandidateStore(DSN, table="candidates; DROP TABLE memories")


def test_the_queue_is_a_separate_table_from_the_memory_pool(log):
    """An unreviewed candidate must not sit where a recall could reach it."""
    build(log).add("cand-1", make_memory())
    assert "firm_memory_candidates" in statements(log)[0]


# --- the SQL the design depends on -------------------------------------------


def test_the_table_is_created_on_first_use_so_there_is_no_migration_step(log):
    build(log).add("cand-1", make_memory())
    assert statements(log)[0].startswith("CREATE TABLE IF NOT EXISTS")


def test_the_schema_is_created_once_not_on_every_call(log):
    store = build(log)
    store.add("cand-1", make_memory())
    store.add("cand-2", make_memory())

    assert sum(sql.startswith("CREATE TABLE") for sql in statements(log)) == 1


def test_re_proposing_the_same_fact_updates_rather_than_duplicates(log):
    """A stateless bot re-runs the same MR; the reviewer must not see it twice."""
    build(log).add("cand-1", make_memory())

    insert = statements(log)[1]
    assert "ON CONFLICT (candidate_id) DO UPDATE" in insert


def test_claiming_a_candidate_is_a_single_atomic_statement(log):
    """Two reviewers on one candidate: exactly one may win, and this is why."""
    build(log, rows=[]).remove("cand-1")

    delete = statements(log)[1]
    assert delete.startswith("DELETE FROM")
    assert "RETURNING memory" in delete


def test_a_candidate_already_claimed_by_someone_else_reads_as_absent(log):
    assert build(log, rows=[]).remove("cand-1") is None


def test_the_queue_is_ordered_oldest_first_so_the_backlog_gets_worked(log):
    list(build(log, rows=[]).items())
    assert "ORDER BY created_at, candidate_id" in statements(log)[1]


# --- round trip --------------------------------------------------------------


def test_a_candidate_is_written_as_its_canonical_json_form(log):
    """The column holds the firm's model, not a provider's projection of it."""
    memory = make_memory(confidence=0.7, metadata={"branch": "main"})
    build(log).add("cand-1", memory)

    _, (candidate_id, payload) = log[1]
    assert candidate_id == "cand-1"
    assert json.loads(payload) == memory.to_dict()


def test_a_driver_that_hands_back_raw_json_text_is_handled(log):
    """Whether JSONB arrives parsed or as text depends on the driver's adapters."""
    raw = json.dumps(make_memory().to_dict())
    store = PostgresCandidateStore(DSN, connect=lambda dsn: FakeConnection([], [(raw,)]))

    assert store.get("cand-1").content


# --- failure -----------------------------------------------------------------


def test_an_unreachable_queue_raises_rather_than_reading_as_empty():
    """Showing "nothing to review" for a full queue is the worst failure here."""

    def refuse(dsn):
        raise OSError("connection refused")

    store = PostgresCandidateStore(DSN, connect=refuse)

    with pytest.raises(CandidateStoreError, match="unreachable"):
        list(store.items())


def test_the_underlying_failure_is_kept_for_diagnosis():
    def refuse(dsn):
        raise OSError("connection refused")

    store = PostgresCandidateStore(DSN, connect=refuse)

    with pytest.raises(CandidateStoreError) as caught:
        store.get("cand-1")
    assert isinstance(caught.value.__cause__, OSError)
