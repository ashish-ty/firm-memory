"""The HTTP surface of the review gate.

A translation layer and nothing more: it authenticates, validates, calls
:class:`~firm_memory.review.service.ReviewService`, and turns failures into
status codes a person can act on. No approval logic lives here.

The status mapping is the part worth reading, because each code is a different
instruction to the reviewer:

===========================  =====  ====================================
Failure                      Code   What the reviewer should do
===========================  =====  ====================================
``CandidateStoreError``      503    Wait; the queue itself is unreachable
``LifecycleError``           409    Reload; someone else took this one
``ProviderError``            502    Retry; the candidate was put back
``TaxonomyError`` etc.       400    Fix the amendment
===========================  =====  ====================================

The 409 and the 502 are the two that matter. Approval claims the candidate
atomically, so two reviewers acting on one candidate is a race exactly one wins
— and the loser must be told to reload rather than shown a generic error. A
failed write, meanwhile, restores the candidate to the queue, so the honest
message is "try again", not "that is gone".

**A 4xx says what went wrong; a 5xx does not.** The 4xx messages describe the
reviewer's own input — a type outside the taxonomy, a scope nobody can query —
and showing them is the entire point. A 5xx carries a failure from inside the
deployment, and those messages wrap driver output that can name hosts, ports and
accounts. So server-side failures return a fixed sentence saying what to *do*,
and the cause goes to the log where an operator can read it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse

from ..errors import (
    CandidateStoreError,
    ExtractionError,
    FirmMemoryError,
    InvalidInputError,
    LifecycleError,
    ProviderError,
    ScopeError,
    TaxonomyError,
)
from ..ingestion.amendment import Amendment
from ..memory import FirmMemory
from ..scope import MemoryScope
from .auth import reviewer_guard
from .schemas import AmendmentInput, ApproveRequest, RejectRequest
from .service import ReviewService
from .settings import ReviewSettings
from .throttle import RateLimiter

logger = logging.getLogger(__name__)

TITLE = "Firm Memory review"
STATIC_DIR = Path(__file__).parent / "static"
INDEX = STATIC_DIR / "index.html"

#: Which failure means what to the person holding the mouse: the status code,
#: and — for server-side failures only — the sentence shown in place of the
#: exception's own message, which may name internal hosts and accounts.
_STATUS_BY_ERROR: tuple[tuple[type[FirmMemoryError], int, str | None], ...] = (
    (
        CandidateStoreError,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "The candidate queue is unreachable, so this is not an empty queue — nothing has been "
        "lost. Try again shortly; the cause is in the server log.",
    ),
    (LifecycleError, status.HTTP_409_CONFLICT, None),
    (
        ProviderError,
        status.HTTP_502_BAD_GATEWAY,
        "The memory store would not accept the write. The candidate has been put back in the "
        "queue, so nothing is lost — try again; the cause is in the server log.",
    ),
    (
        ExtractionError,
        status.HTTP_502_BAD_GATEWAY,
        "Extraction failed. The cause is in the server log.",
    ),
    (TaxonomyError, status.HTTP_400_BAD_REQUEST, None),
    (ScopeError, status.HTTP_400_BAD_REQUEST, None),
    (InvalidInputError, status.HTTP_400_BAD_REQUEST, None),
)

_UNEXPECTED = "Something failed inside the review service. The cause is in the server log."


def build_app(
    memory: FirmMemory | None = None,
    *,
    settings: ReviewSettings | None = None,
) -> FastAPI:
    """Create the review application.

    Both dependencies are injectable so the whole surface can be tested against
    the in-memory provider and a known token, with no database and no secrets.
    """
    config = settings or ReviewSettings.from_env()
    service = ReviewService(memory if memory is not None else FirmMemory.from_env())
    limiter = RateLimiter(config.rate_limit_per_minute)
    # The dependency both gates the route and names the caller, so a decision is
    # attributed to the token that was presented rather than to anything the
    # client put in the body.
    reviewer = Depends(reviewer_guard(config.reviewers))
    guard = reviewer

    app = FastAPI(title=TITLE, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def throttle(request: Request, call_next):
        """Bound how fast any one client may hit the API."""
        if request.url.path.startswith("/api/") and not limiter.allow(_client(request)):
            return JSONResponse(
                {"detail": "Too many requests. Slow down."},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        return await call_next(request)

    @app.exception_handler(FirmMemoryError)
    async def firm_memory_error(request: Request, exc: FirmMemoryError) -> JSONResponse:
        """Turn a domain failure into the status code that tells the reviewer what to do."""
        code, safe = _rendering_for(exc)
        # The log always gets everything. The response gets the exception's own
        # message only when that message is about the caller's input.
        log = logger.exception if code >= 500 else logger.warning
        log("%s on %s: %s", type(exc).__name__, request.url.path, exc)
        return JSONResponse(
            {"detail": safe if safe is not None else str(exc), "error_type": type(exc).__name__},
            status_code=code,
        )

    # --- reading -------------------------------------------------------------

    @app.get("/api/queue", dependencies=[guard])
    def queue() -> dict:
        """Everything awaiting a human."""
        return service.queue()

    @app.get("/api/taxonomy", dependencies=[guard])
    def taxonomy() -> dict:
        """The types a candidate may be retyped to."""
        return service.taxonomy()

    @app.get("/api/scope", dependencies=[guard])
    def scope() -> dict:
        """The scope this service reads and writes under."""
        return service.scope()

    @app.get("/api/whoami")
    def whoami(name: str = reviewer) -> dict:
        """The reviewer this token was issued to.

        The page shows it so a reviewer can see, before approving anything,
        which name their decisions will carry.
        """
        return {"reviewer": name}

    # --- deciding ------------------------------------------------------------

    @app.post("/api/candidates/{candidate_id}/approve")
    def approve(candidate_id: str, body: ApproveRequest, name: str = reviewer) -> dict:
        """Endorse a candidate, optionally amended, and write it to the pool."""
        return service.approve(
            candidate_id,
            approver=name,
            confidence=body.confidence,
            amendment=_amendment(body.amendment),
        )

    @app.post("/api/candidates/{candidate_id}/reject")
    def reject(candidate_id: str, body: RejectRequest, name: str = reviewer) -> dict:
        """Turn a candidate down."""
        return service.reject(candidate_id, reviewer=name, reason=body.reason)

    # --- the page ------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict:
        """Liveness only. Unauthenticated on purpose, and it reveals nothing."""
        return {"status": "ok"}

    @app.get("/")
    def index() -> FileResponse:
        """The review page.

        Served without the token because it holds no data — it is the form into
        which the reviewer types the token. Every byte of queue content behind
        it is gated.
        """
        if not INDEX.exists():  # pragma: no cover - packaging failure
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The review page is missing from this installation.",
            )
        return FileResponse(INDEX, media_type="text/html")

    return app


def _amendment(submitted: AmendmentInput | None) -> Amendment | None:
    """Convert a validated request body into the domain's amendment."""
    if submitted is None:
        return None
    amendment = Amendment(
        content=_text(submitted.content),
        type=_text(submitted.type),
        scope=MemoryScope(
            firm=submitted.scope.firm,
            domains=tuple(submitted.scope.domains),
            repos=tuple(submitted.scope.repos),
        )
        if submitted.scope is not None
        else None,
        confidence=submitted.confidence,
        reference=_text(submitted.reference),
        evidence=_text(submitted.evidence),
    )
    return None if amendment.is_empty else amendment


def _text(value: str | None) -> str | None:
    """Blank means unchanged, not erase."""
    if value is None:
        return None
    return value.strip() or None


def _rendering_for(exc: FirmMemoryError) -> tuple[int, str | None]:
    """The status code for a domain failure, and what may be said about it.

    ``None`` for the message means the exception's own text is safe to show:
    it describes something the caller sent. Anything unrecognised is treated as
    a server fault and told nothing, which is the safe direction to be wrong in.
    """
    for error_type, code, safe in _STATUS_BY_ERROR:
        if isinstance(exc, error_type):
            return code, safe
    return status.HTTP_500_INTERNAL_SERVER_ERROR, _UNEXPECTED


def _client(request: Request) -> str:
    """Identify the caller for rate limiting.

    The socket address, never a forwarded header: a client can set those
    freely, so trusting one would let anyone reset their own limit. Behind a
    proxy this rate limits the proxy, which is the correct conservative
    reading when nothing has told us the proxy is trustworthy.
    """
    return request.client.host if request.client else "unknown"
