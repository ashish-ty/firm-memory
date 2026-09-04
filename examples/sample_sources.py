"""Realistic source material for trying the ingestion pipeline.

Deliberately mixed. Some of these contain durable knowledge, one contains
almost none, and one is full of exactly the things the taxonomy excludes —
stack traces, a personal preference, transient state. A good extractor keeps
the first, returns little from the second, and is caught by the taxonomy on the
third.
"""

from __future__ import annotations

from firm_memory import MemoryScope, Provenance, Source, SourceDocument

# --- a design discussion with real rationale in it ---------------------------

CUTOFF_DISCUSSION = """
Reviewer: Why does OrderRouter drop cash-strategy orders after 15:20 instead of
queueing them?

Author: MCX rejects anything sent after 15:20 for cash strategies, so queueing
just moves the rejection later. We actually tried queueing them for the next
session back in March and it caused duplicate fills — the queued order and the
next-day order both went out. We reverted it the same week.

Reviewer: Should the check live in the strategy instead?

Author: No, it has to be in OrderRouter. Every venue path goes through it, and
DropCopy reconciles against what OrderRouter emitted. If a strategy suppressed
the order itself we would have a gap between what we think we sent and what the
exchange saw.
"""

# --- an incident write-up ----------------------------------------------------

INCIDENT_REVIEW = """
Incident 4821 post-review.

Symptom: AnalyticsAPI returned 504s for about 40 minutes during the bulk
position import.

Root cause: cache stampede. CacheManager had no request coalescing, so every
one of the ~2000 concurrent requests that missed the cache went to Postgres for
the same position snapshot.

Fix: added single-flight coalescing in CacheManager so concurrent misses for the
same key share one database round trip.

Follow-up: we considered raising the connection pool size instead and decided
against it — it would have hidden the stampede rather than fixed it, and the
pool is shared with the Risk Engine which we do not want starved.
"""

# --- a thread with nothing durable in it -------------------------------------

ROUTINE_THREAD = """
Reviewer: nit, can you rename this variable to something clearer?
Author: done, pushed.
Reviewer: thanks, LGTM.
"""

# --- material that is mostly what the taxonomy excludes ----------------------

NOISY_THREAD = """
Author: getting this on main right now

Traceback (most recent call last):
  File "execution/router.py", line 214, in submit
    return self._gateway.send(order)
  File "execution/gateway.py", line 88, in send
    raise ConnectionError("no route to host")
ConnectionError: no route to host

Reviewer: that is just the staging gateway being down again, it is back now.
Also Ashish prefers we use tabs in this repo, not spaces.
"""

EXECUTION_SCOPE = MemoryScope(domains=("execution",), repos=("oms",))


def sample_documents() -> list[SourceDocument]:
    """The four sources above, ready to ingest."""
    return [
        SourceDocument(
            content=CUTOFF_DISCUSSION,
            scope=EXECUTION_SCOPE,
            provenance=Provenance(source=Source.MERGE_REQUEST, reference="mr-4821"),
            kind="merge request discussion",
        ),
        SourceDocument(
            content=INCIDENT_REVIEW,
            scope=MemoryScope(domains=("execution",), repos=("analytics-api", "oms")),
            provenance=Provenance(source=Source.ISSUE, reference="incident-4821"),
            kind="incident review",
        ),
        SourceDocument(
            content=ROUTINE_THREAD,
            scope=EXECUTION_SCOPE,
            provenance=Provenance(source=Source.MERGE_REQUEST, reference="mr-5177"),
            kind="merge request discussion",
        ),
        SourceDocument(
            content=NOISY_THREAD,
            scope=EXECUTION_SCOPE,
            provenance=Provenance(source=Source.MERGE_REQUEST, reference="mr-5201"),
            kind="merge request discussion",
        ),
    ]
