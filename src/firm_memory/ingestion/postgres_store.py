"""Candidates held in Postgres, so the queue spans machines.

The other two stores assume one machine. That assumption does not survive the
deployment this platform exists for: the bot proposes from a GitLab CI job that
ends minutes later, and an engineer approves hours after that, somewhere else
entirely. A dict dies with the process and a JSON file dies with the runner, so
neither can carry a candidate from the one to the other.

This store uses the Postgres the platform already runs for the vector pool. It
is a **separate table**, not a corner of the memory pool: an unreviewed
candidate is not firm knowledge, and putting it in the retrievable pool would
put every recall one status-filter bug away from returning things nobody
approved.

Two properties matter more than performance here:

* ``remove`` is a single ``DELETE ... RETURNING``. Claiming a candidate is
  therefore atomic, so two reviewers opening the same queue cannot both approve
  the same candidate — exactly one gets the row, the other is told it is gone.
* Failures raise. An unreachable queue and an empty queue look identical to a
  reviewer, and showing "nothing to review" for a queue that is actually full is
  a worse failure than an error message.

Connections are opened per operation rather than pooled. Both callers are
short-lived or low-rate — a CI job writing a handful of candidates, a reviewer
loading a page — and a pool would add lifecycle to own for no measurable gain.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from typing import Any

from ..errors import CandidateStoreError, ConfigurationError
from ..models import Memory

DEFAULT_TABLE = "firm_memory_candidates"

#: Table names are interpolated into DDL, which no parameter binding can cover.
#: Restricting them to plain lowercase identifiers is what keeps that safe.
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    candidate_id TEXT PRIMARY KEY,
    memory       JSONB       NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

#: Oldest first, so a reviewer works through the backlog rather than the newest
#: thing the bot happened to say.
_QUEUE_ORDER = "ORDER BY created_at, candidate_id"


class PostgresCandidateStore:
    """Candidates persisted in a Postgres table, shared across processes."""

    def __init__(
        self,
        dsn: str,
        *,
        table: str = DEFAULT_TABLE,
        connect: Callable[[str], Any] | None = None,
    ) -> None:
        if not (dsn or "").strip():
            raise ConfigurationError("A Postgres candidate store requires a connection string")
        if not _IDENTIFIER.match(table or ""):
            raise ConfigurationError(
                f"Invalid candidate table name {table!r}: must match {_IDENTIFIER.pattern}"
            )
        self._dsn = dsn.strip()
        self._table = table
        self._connect = connect or _psycopg_connect
        self._schema_ready = False

    # --- CandidateStore ------------------------------------------------------

    def add(self, candidate_id: str, memory: Memory) -> None:
        """Record *memory* as awaiting review, replacing any earlier version.

        Upsert rather than insert because a stateless bot re-proposes the same
        fact on every re-run of the same MR, and the candidate id is derived
        from the fact itself.
        """
        self._run(
            f"INSERT INTO {self._table} (candidate_id, memory) VALUES (%s, %s) "
            f"ON CONFLICT (candidate_id) DO UPDATE SET memory = EXCLUDED.memory, updated_at = now()",
            (candidate_id, json.dumps(memory.to_dict())),
        )

    def get(self, candidate_id: str) -> Memory | None:
        """Return the candidate, or ``None``."""
        rows = self._run(
            f"SELECT memory FROM {self._table} WHERE candidate_id = %s",
            (candidate_id,),
            fetch=True,
        )
        return _to_memory(rows[0][0]) if rows else None

    def remove(self, candidate_id: str) -> Memory | None:
        """Claim and return the candidate, or ``None`` if someone else has it.

        The delete and the read are one statement on purpose: this is what makes
        approval a race exactly one reviewer can win.
        """
        rows = self._run(
            f"DELETE FROM {self._table} WHERE candidate_id = %s RETURNING memory",
            (candidate_id,),
            fetch=True,
        )
        return _to_memory(rows[0][0]) if rows else None

    def items(self) -> Iterator[tuple[str, Memory]]:
        """Yield every pending candidate, oldest first."""
        rows = self._run(f"SELECT candidate_id, memory FROM {self._table} {_QUEUE_ORDER}", (), fetch=True)
        for candidate_id, raw in rows:
            yield candidate_id, _to_memory(raw)

    # --- plumbing ------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the table if it is absent.

        Called lazily on first use so that deploying the platform stays a
        matter of setting a connection string, with no migration step to forget.
        """
        if self._schema_ready:
            return
        self._run(_SCHEMA.format(table=self._table), (), schema=True)
        self._schema_ready = True

    def _run(self, sql: str, params: tuple, *, fetch: bool = False, schema: bool = False) -> list:
        """Execute one statement, translating driver failures into our own."""
        if not schema:
            self.ensure_schema()
        try:
            with self._connect(self._dsn) as connection, connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall() if fetch else []
        except Exception as exc:  # any driver failure is a queue failure
            raise CandidateStoreError(
                f"The candidate queue in Postgres is unreachable: {exc}"
            ) from exc


def _to_memory(raw: object) -> Memory:
    """Rebuild a memory from a JSONB column, whichever form the driver returns."""
    if isinstance(raw, str | bytes):
        raw = json.loads(raw)
    return Memory.from_dict(raw)


def _psycopg_connect(dsn: str) -> Any:
    """Open a connection, failing with an actionable message if the driver is absent."""
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - psycopg is a hard dependency
        raise ConfigurationError(
            "The Postgres candidate store requires the 'psycopg' package. Run: uv sync"
        ) from exc
    return psycopg.connect(dsn)
