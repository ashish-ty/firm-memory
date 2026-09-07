"""Who is reaching the queue.

Access and attribution used to be two mechanisms: a shared token to get in, and
a name typed into a box to sign the decision. They are now one. The token is
issued to a person, and presenting it both admits you and says who you are.

That is a stronger guarantee than the name box gave. A typed name is a claim
anyone could make; a token is issued. It also removes a step from every review,
and a field a reviewer can leave wrong.

Tokens are compared in constant time, and against *every* configured token, so
that the time taken reveals neither which token matched nor how nearly a wrong
one did.

This is attribution, not authorisation: every reviewer can do the same things.
The name exists so a memory can be traced back and corrected, and it never
becomes a filter or a scoping axis — a memory is owned by the repo and the firm,
never by a person.
"""

from __future__ import annotations

from collections.abc import Mapping
from secrets import compare_digest

from fastapi import HTTPException, Request, status

#: Sent as ``Authorization: Bearer <token>``.
_BEARER = "bearer"

_REFUSED = "A valid review token is required."


def reviewer_guard(reviewers: Mapping[str, str]):
    """Build the dependency that gates every API route and names the caller.

    Returns the reviewer's name, so a route can attribute a decision without
    trusting anything in the request body.
    """

    def require_reviewer(request: Request) -> str:
        name = _resolve(_bearer(request.headers.get("authorization")), reviewers)
        if name is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=_REFUSED,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return name

    return require_reviewer


def _resolve(supplied: str | None, reviewers: Mapping[str, str]) -> str | None:
    """The reviewer *supplied* was issued to, in constant time."""
    if supplied is None:
        return None

    # Every token is compared even after a match, so the number of comparisons
    # does not depend on which reviewer presented a token or whether any did.
    matched: str | None = None
    for token, name in reviewers.items():
        if compare_digest(supplied, token):
            matched = name
    return matched


def _bearer(header: str | None) -> str | None:
    """Extract the token from an Authorization header, or ``None``."""
    if not header:
        return None
    scheme, _, credentials = header.partition(" ")
    if scheme.strip().lower() != _BEARER:
        return None
    return credentials.strip() or None
