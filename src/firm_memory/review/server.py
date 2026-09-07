"""Running the review service.

The process an engineer opens in a browser. It is long lived, unlike the bot
that fills the queue — which is the whole reason the queue has to live somewhere
both can reach.
"""

from __future__ import annotations

import logging

from ..errors import ConfigurationError
from .app import build_app
from .settings import ReviewSettings

logger = logging.getLogger(__name__)


def main() -> None:  # pragma: no cover - process entry point
    """Serve the review UI and its API."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = ReviewSettings.from_env()
    app = build_app(settings=settings)

    try:
        import uvicorn
    except ImportError as exc:
        raise ConfigurationError(
            "The review service requires the 'uvicorn' package. Run: uv sync"
        ) from exc

    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        # Not refused — exposing it is a legitimate choice, and the token is
        # there for exactly that. But it should never happen unnoticed.
        logger.warning(
            "The review service is bound to %s, which is reachable beyond this machine. "
            "The shared token is the only thing gating it.",
            settings.host,
        )

    logger.info("Review UI on http://%s:%s", settings.host, settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
