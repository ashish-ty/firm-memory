"""Choosing where candidates wait, from one configured location.

The platform layer must not name a storage technology — that is what keeps a
provider swap a configuration change, and there is a guard test on it. So the
queue's location is configured as a **URL**, and the scheme picks the store:

    postgresql://mem0:pw@db/mem0     shared across machines
    file:///srv/firm-memory/queue.json   one host
    memory://                        this process only

That is not decoration. It collapses "which store" and "where" into a single
fact, so a deployment cannot end up with a path configured and a DSN silently
winning, and it keeps the word "Postgres" out of :class:`~firm_memory.config.Settings`.

A bare path is accepted too, because ``FIRM_MEMORY_CANDIDATES_PATH`` predates
this and pointing it at a file should keep working.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..errors import ConfigurationError
from .postgres_store import PostgresCandidateStore
from .store import CandidateStore, InMemoryCandidateStore, JsonFileCandidateStore

#: Schemes psycopg understands, handed to it unchanged.
_POSTGRES_SCHEMES = frozenset({"postgresql", "postgres"})
_FILE_SCHEME = "file"
_MEMORY_SCHEME = "memory"


def candidate_store_from_url(url: str) -> CandidateStore:
    """Build the candidate store *url* names.

    Raises rather than falling back to an in-process queue on an unrecognised
    scheme. A misconfigured queue that silently becomes a dict would accept
    everything the bot proposes and lose all of it when the job ends, with no
    error anywhere — the failure this whole module exists to prevent.
    """
    location = (url or "").strip()
    if not location:
        raise ConfigurationError("A candidate queue location is required")

    scheme = urlsplit(location).scheme.lower()

    if scheme in _POSTGRES_SCHEMES:
        return PostgresCandidateStore(location)
    if scheme == _MEMORY_SCHEME:
        return InMemoryCandidateStore()
    if scheme == _FILE_SCHEME:
        return JsonFileCandidateStore(_file_path(location))
    if not scheme:
        # A bare filesystem path, which is what FIRM_MEMORY_CANDIDATES_PATH is.
        return JsonFileCandidateStore(location)

    raise ConfigurationError(
        f"Unsupported candidate queue location {location!r}: expected a "
        f"postgresql:// URL, a file:// URL or a path, or memory://"
    )


def _file_path(url: str) -> str:
    """The filesystem path inside a ``file://`` URL.

    ``file:///srv/queue.json`` has an empty host and an absolute path;
    ``file://queue.json`` is a relative path a reader almost certainly meant,
    which urlsplit reads as a host. Both resolve to something usable rather
    than to a confusing empty path.
    """
    parts = urlsplit(url)
    if parts.netloc and not parts.path:
        return parts.netloc
    return f"{parts.netloc}{parts.path}" if parts.netloc else parts.path
