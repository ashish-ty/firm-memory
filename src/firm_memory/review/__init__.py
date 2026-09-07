"""The human approval gate, as something a person can actually use.

The gate itself lives in :mod:`firm_memory.ingestion.approval` and is complete
without this package: a candidate is queued, a person endorses it, and only then
is it written. What was missing was a way for that person to *see* the queue —
until now it was reachable only from Python, which meant in practice that
nothing got reviewed.

Three layers, kept apart so the gate is testable without HTTP:

``service``   the review operations, framework free
``app``       the HTTP surface, and the mapping from failures to status codes
``static``    one self-contained page, served by the API

The page loads nothing from the internet — no CDN, no web font. Candidates are
unreviewed statements about the firm's trading systems, and a review tool that
reaches out to third parties while displaying them would undo the self-hosted,
no-egress deployment this platform is built for.
"""

from __future__ import annotations

from .service import ReviewService
from .settings import ReviewSettings

__all__ = ["ReviewService", "ReviewSettings"]
