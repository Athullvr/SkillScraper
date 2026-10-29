"""Console entry point for the authenticated hosted MCP service."""

from __future__ import annotations

import logging
import os


def main() -> None:
    """Validate startup configuration and serve the remote MCP endpoint."""
    try:
        import uvicorn

        from skillscout.config import load_config
        from skillscout.remote_server import create_app, load_remote_settings

        settings = load_remote_settings()
        load_config()  # Validate catalog and optional LLM settings without making requests.
        port = _port_from_environment()
        app = create_app(settings)
    except (ImportError, ValueError) as exc:
        # Configuration messages name settings and constraints, never credential values.
        logging.getLogger("skillscout.remote").error("startup_configuration_invalid: %s", exc)
        raise SystemExit(2) from None

    logging.getLogger("skillscout.remote").info(
        "remote_mcp_starting",
        extra={"host": "0.0.0.0", "port": port},
    )
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=False,
        timeout_keep_alive=10,
        timeout_graceful_shutdown=20,
        limit_concurrency=settings.max_concurrent_requests + 8,
        limit_max_requests=10_000,
        backlog=128,
    )
    uvicorn.Server(config).run()


def _port_from_environment() -> int:
    raw_port = os.environ.get("PORT", "10000")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("PORT must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65_535:
        raise ValueError("PORT must be an integer between 1 and 65535")
    return port


if __name__ == "__main__":
    main()
