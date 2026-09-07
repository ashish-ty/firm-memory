"""Serve the review UI against the same pool and queue the example writes to.

    python examples/run_review_ui.py

The human half of the round trip. ``examples/ingest_via_mcp.py`` fills the queue
from a short-lived MCP process; this is the long-lived one an engineer opens,
and the two only meet because both resolve the same candidate queue URL from
``examples/_environment.py``.

The token comes from ``.env`` and stays the same across restarts, so the URL can
be bookmarked and the token pasted once. It is also *who you are*: every fact you
approve is recorded against the name the token was issued to, which is why there
is no name box in the UI.

If no token is configured this prints one to add to ``.env`` and stops, rather
than inventing a different one on every run.
"""

from __future__ import annotations

import getpass
import os
import secrets
import sys

from _environment import configure

from firm_memory.errors import ConfigurationError
from firm_memory.review.app import build_app
from firm_memory.review.settings import ReviewSettings


def main() -> None:
    """Start the review service against the configured token."""
    configure()

    try:
        settings = ReviewSettings.from_env()
    except ConfigurationError as exc:
        _explain_missing_token(exc)
        raise SystemExit(1) from None

    print("\nFirm Memory review")
    print(f"  URL       http://{settings.host}:{settings.port}")
    print(f"  Reviewer  {', '.join(sorted(settings.reviewers.values()))}")
    print(f"  Queue     {_redacted(os.environ.get('FIRM_MEMORY_CANDIDATES_URL'))}")
    print("\n  Paste your token on the page. Approvals are recorded against the")
    print("  name it was issued to, so there is nothing else to fill in.\n")

    import uvicorn

    uvicorn.run(build_app(settings=settings), host=settings.host, port=settings.port, log_level="warning")


def _explain_missing_token(exc: ConfigurationError) -> None:
    """Print the configuration error, and a token ready to paste into .env.

    Suggesting a value rather than adopting one is deliberate: a token invented
    per run cannot be bookmarked, and a reviewer who has to re-copy it every
    time is a reviewer who stops using the queue.
    """
    print(f"\n{exc}\n", file=sys.stderr)
    print("Add these two lines to .env, then run this again:\n", file=sys.stderr)
    print(f"  FIRM_MEMORY_REVIEW_TOKEN={secrets.token_hex(32)}", file=sys.stderr)
    print(f"  FIRM_MEMORY_REVIEW_APPROVER={getpass.getuser()}\n", file=sys.stderr)


def _redacted(url: str | None) -> str:
    """A connection string with its password removed, safe to print."""
    if not url:
        return "in-process (nothing else can see it)"
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    credentials, _, host = rest.partition("@")
    user = credentials.partition(":")[0]
    return f"{scheme}://{user}:***@{host}"


if __name__ == "__main__":
    main()
