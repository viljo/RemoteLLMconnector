"""Health endpoint for the connector component."""

import time
from typing import TYPE_CHECKING

from aiohttp import web

from remotellm.shared.logging import get_logger

if TYPE_CHECKING:
    from remotellm.connector.llm_client import LLMClient
    from remotellm.connector.relay_client import RelayClient

logger = get_logger(__name__)


class HealthServer:
    """HTTP health server for the connector."""

    def __init__(
        self,
        port: int,
        relay_client: "RelayClient",
        llm_client: "LLMClient",
    ):
        """Initialize the health server.

        Args:
            port: Port to bind to
            relay_client: Relay client for status
            llm_client: LLM client for health checks
        """
        self.port = port
        self.relay_client = relay_client
        self.llm_client = llm_client
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

        Returns health status of the connector with registered models (T044).
        """
        from remotellm.connector.relay_client import ConnectionState

        relay_connected = self.relay_client.state == ConnectionState.CONNECTED
        llm_available = await self.llm_client.check_health()
        uptime = time.time() - self._start_time

        # Get registered models
        models = self.relay_client.models

        # Overall status is healthy if relay is connected
        # LLM availability is informational
        status = "healthy" if relay_connected else "unhealthy"
        http_status = 200 if relay_connected else 503

        return web.json_response(
            {
                "status": status,
                "relay_connected": relay_connected,
                "relay_state": self.relay_client.state.value,
                "relay_session_id": self.relay_client.session_id,
                "llm_available": llm_available,
                "models": models,
                "uptime_seconds": round(uptime, 1),
            },
            status=http_status,
        )

    async def _handle_ready(self, request: web.Request) -> web.Response:  # noqa: ARG002
        """Handle GET /ready endpoint.

        Returns readiness status - ready when both relay and LLM are available.
        """
        from remotellm.connector.relay_client import ConnectionState

        relay_connected = self.relay_client.state == ConnectionState.CONNECTED
        llm_available = await self.llm_client.check_health()
        ready = relay_connected and llm_available

        return web.json_response(
            {
                "ready": ready,
                "relay_connected": relay_connected,
                "llm_available": llm_available,
            },
            status=200 if ready else 503,
        )
