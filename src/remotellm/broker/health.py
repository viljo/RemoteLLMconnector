"""Health endpoint for the broker component."""

import time
from typing import TYPE_CHECKING

from aiohttp import web

from remotellm.shared.logging import get_logger

if TYPE_CHECKING:
    from remotellm.broker.router import ModelRouter
    from remotellm.broker.tunnel_server import TunnelServer

logger = get_logger(__name__)


class HealthServer:
    """HTTP health server for the broker."""

    def __init__(
        self,
        port: int,
        tunnel_server: "TunnelServer",
        router: "ModelRouter | None" = None,
    ):
        """Initialize the health server.

        Args:
            port: Port to bind to
            tunnel_server: Tunnel server for status
            router: Model router for routing info
        """
        self.port = port
        self.tunnel_server = tunnel_server
        self.router = router
        self._start_time = time.time()
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Set up health routes."""
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/ready", self._handle_ready)

    async def start(self) -> None:
        """Start the health server."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        logger.info("Health server started", port=self.port)

    async def stop(self) -> None:
        """Stop the health server."""
        if self._runner:
            await self._runner.cleanup()
        logger.info("Health server stopped")

    async def _handle_health(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Handle GET /health endpoint.

        Returns health status of the broker with connector count and models (T042, T043).
        """
        connector_count = self.tunnel_server.connector_count
        uptime = time.time() - self._start_time

        # Get models from router if available
        models: list[str] = []
        if self.router:
            models = self.router.available_models

        # Broker is always healthy if running
        # Connector availability is informational
        status = "healthy"

        return web.json_response(
            {
                "status": status,
                "connectors_connected": connector_count,
                "models": models,
                "model_count": len(models),
                "uptime_seconds": round(uptime, 1),
            },
            status=200,
        )

    async def _handle_ready(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Handle GET /ready endpoint.

        Returns readiness status - ready when at least one connector is available.
        """
        connector_count = self.tunnel_server.connector_count
        ready = connector_count > 0

        return web.json_response(
            {
                "ready": ready,
                "connectors_connected": connector_count,
            },
            status=200 if ready else 503,
        )
