"""WebSocket tunnel server for accepting connector connections."""

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import aiohttp
from aiohttp import web

from remotellm.shared.logging import get_logger
from remotellm.shared.protocol import (
    AuthPayload,
    MessageType,
    TunnelMessage,
    create_auth_fail_message,
    create_auth_ok_message,
    create_ping_message,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


@dataclass
class ConnectorRegistration:
    """Registered connector connection."""

    connector_id: str
    websocket: web.WebSocketResponse
    connected_at: float
    models: list[str] = field(default_factory=list)
    llm_api_key: str | None = None
    pending_requests: dict[str, asyncio.Future[TunnelMessage]] = field(default_factory=dict)


class TunnelServer:
    """WebSocket server for connector tunnel connections.

    Can operate in two modes:
    1. Integrated mode: WebSocket handler at /ws path on main HTTP server
    2. Standalone mode: Separate WebSocket server on its own port (legacy)
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8443,
        connector_tokens: list[str] | None = None,
        connector_configs: dict[str, str | None] | None = None,
        auth_timeout: float = 10.0,
        ping_interval: float = 30.0,
        on_connector_registered: "Callable[[str, list[str], str | None], None] | None" = None,
        on_connector_disconnected: "Callable[[str], None] | None" = None,
    ):
        """Initialize the tunnel server.

        Args:
            host: Host to bind to (for standalone mode)
            port: Port to bind to (for standalone mode)
            connector_tokens: Valid authentication tokens for connectors
            connector_configs: Mapping of token → llm_api_key for connectors
            auth_timeout: Timeout for connector authentication
            ping_interval: Interval for ping/pong health checks
            on_connector_registered: Callback when connector registers (connector_id, models, llm_api_key)
            on_connector_disconnected: Callback when connector disconnects (connector_id)
        """
        self.host = host
        self.port = port
        self.connector_tokens = connector_tokens or []
        self.connector_configs = connector_configs or {}
        self.auth_timeout = auth_timeout
        self.ping_interval = ping_interval
        self.on_connector_registered = on_connector_registered
        self.on_connector_disconnected = on_connector_disconnected

        self._connectors: dict[str, ConnectorRegistration] = {}
        self._running = False

    @property
    def connector_count(self) -> int:
        """Get number of connected connectors."""
        return len(self._connectors)

    def get_connector(self) -> ConnectorRegistration | None:
        """Get an available connector.

        Returns:
            A connector registration if available, None otherwise
        """
        if not self._connectors:
            return None
        # Return first available connector (simple round-robin could be added later)
        return next(iter(self._connectors.values()))

    async def send_request(
        self,
        connector_id: str,
        message: TunnelMessage,
        timeout: float = 300.0,
    ) -> TunnelMessage:
        """Send a request to a connector and wait for response.

        Args:
            connector_id: ID of the connector to send to
            message: The request message
            timeout: Response timeout in seconds

        Returns:
            The response message

        Raises:
            KeyError: If connector not found
            asyncio.TimeoutError: If response times out
        """
        connector = self._connectors.get(connector_id)
        if not connector:
            raise KeyError(f"Connector {connector_id} not found")

        # Create a future for the response
        response_future: asyncio.Future[TunnelMessage] = asyncio.Future()
        connector.pending_requests[message.id] = response_future

        try:
            # Send the request
            await connector.websocket.send_str(message.model_dump_json())
            logger.debug(
                "Sent request to connector", connector_id=connector_id, correlation_id=message.id
            )

            # Wait for response
            return await asyncio.wait_for(response_future, timeout=timeout)
        finally:
            connector.pending_requests.pop(message.id, None)

    async def send_request_streaming(
        self,
        connector_id: str,
        message: TunnelMessage,
    ) -> asyncio.Queue[TunnelMessage]:
        """Send a request and return a queue for streaming responses.

        Args:
            connector_id: ID of the connector to send to
            message: The request message

        Returns:
            Queue that will receive response messages

        Raises:
            KeyError: If connector not found
        """
        connector = self._connectors.get(connector_id)
        if not connector:
            raise KeyError(f"Connector {connector_id} not found")

        # Create a queue for streaming responses
        response_queue: asyncio.Queue[TunnelMessage] = asyncio.Queue()
        connector.pending_requests[message.id] = response_queue  # type: ignore

        # Send the request
        await connector.websocket.send_str(message.model_dump_json())
        logger.debug(
            "Sent streaming request to connector",
            connector_id=connector_id,
            correlation_id=message.id,
        )

        return response_queue

    def setup_routes(self, app: web.Application) -> None:
        """Set up WebSocket route on an aiohttp application.

        This integrates the tunnel server with the main HTTP server.

        Args:
            app: The aiohttp application to add the /ws route to
        """
        app.router.add_get("/ws", self._handle_websocket_request)
        logger.info("WebSocket tunnel route registered at /ws")

    async def start(self) -> None:
        """Mark the tunnel server as running.

        For integrated mode, routes should be set up via setup_routes().
        """
        self._running = True
        logger.info("Tunnel server started (integrated mode)")

    async def stop(self) -> None:
        """Stop the tunnel server."""
        self._running = False
        # Close all connected websockets
        for connector_id, connector in list(self._connectors.items()):
            try:
                await connector.websocket.close()
            except Exception:
                pass
        logger.info("Tunnel server stopped")

    async def _handle_websocket_request(self, request: web.Request) -> web.WebSocketResponse:
        """Handle incoming WebSocket connection request (aiohttp handler).

        Args:
            request: The aiohttp request

        Returns:
            WebSocket response
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await self._handle_connection(ws)
        return ws

    async def _handle_connection(self, websocket: web.WebSocketResponse) -> None:
        """Handle a new WebSocket connection.

        Args:
            websocket: The WebSocket connection
        """
        connector_id: str | None = None

        try:
            # Authenticate the connector
            auth_result = await self._authenticate(websocket)
            if not auth_result:
                return

            connector_id, models, llm_api_key = auth_result

            # Register the connector
            import time

            registration = ConnectorRegistration(
                connector_id=connector_id,
                websocket=websocket,
                connected_at=time.time(),
                models=models,
                llm_api_key=llm_api_key,
            )
            self._connectors[connector_id] = registration
            logger.info("Connector registered", connector_id=connector_id, models=models)

            # Notify callback
            if self.on_connector_registered:
                self.on_connector_registered(connector_id, models, llm_api_key)

            # Start ping task
            ping_task = asyncio.create_task(self._ping_loop(connector_id))

            try:
                # Handle messages
                await self._message_loop(connector_id, websocket)
            finally:
                ping_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ping_task

        except Exception as e:
            logger.error("Connection error", connector_id=connector_id, error=str(e))
        finally:
            if connector_id and connector_id in self._connectors:
                # Cancel any pending requests
                connector = self._connectors[connector_id]
                for _request_id, pending in connector.pending_requests.items():
                    if isinstance(pending, asyncio.Future) and not pending.done():
                        pending.set_exception(ConnectionError("Connector disconnected"))
                    elif isinstance(pending, asyncio.Queue):
                        await pending.put(None)  # Signal end of stream
                del self._connectors[connector_id]
                logger.info("Connector unregistered", connector_id=connector_id)

                # Notify callback
                if self.on_connector_disconnected:
                    self.on_connector_disconnected(connector_id)

    async def _authenticate(
        self, websocket: web.WebSocketResponse
    ) -> tuple[str, list[str], str | None] | None:
        """Authenticate a connector connection.

        Args:
            websocket: The WebSocket connection

        Returns:
            Tuple of (connector_id, models, llm_api_key) if authenticated, None otherwise
        """
        try:
            # Wait for AUTH message
            msg = await asyncio.wait_for(websocket.receive(), timeout=self.auth_timeout)
            if msg.type == aiohttp.WSMsgType.TEXT:
                raw_message = msg.data
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                logger.warning("WebSocket closed during auth")
                return None
            else:
                logger.warning("Unexpected message type during auth", type=msg.type)
                return None

            message = TunnelMessage.model_validate_json(raw_message)

            if message.type != MessageType.AUTH:
                logger.warning("Expected AUTH message", received=message.type)
                fail_msg = create_auth_fail_message(message.id, "Expected AUTH message")
                await websocket.send_str(fail_msg.model_dump_json())
                await websocket.close()
                return None

            payload = AuthPayload.model_validate(message.payload)

            # Validate token
            if self.connector_tokens and payload.token not in self.connector_tokens:
                logger.warning("Invalid connector token")
                fail_msg = create_auth_fail_message(message.id, "Invalid token")
                await websocket.send_str(fail_msg.model_dump_json())
                await websocket.close()
                return None

            # Extract models from AUTH payload
            models = payload.models

            # Look up llm_api_key from config by token
            llm_api_key = self.connector_configs.get(payload.token)

            # Generate connector ID and send AUTH_OK
            connector_id = f"conn-{uuid.uuid4().hex[:8]}"
            ok_msg = create_auth_ok_message(message.id, connector_id)
            await websocket.send_str(ok_msg.model_dump_json())

            logger.info(
                "Connector authenticated",
                connector_id=connector_id,
                models=models,
                has_llm_api_key=llm_api_key is not None,
            )
            return (connector_id, models, llm_api_key)

        except TimeoutError:
            logger.warning("Authentication timeout")
            await websocket.close()
            return None
        except Exception as e:
            logger.error("Authentication error", error=str(e))
            await websocket.close()
            return None

    async def _message_loop(self, connector_id: str, websocket: web.WebSocketResponse) -> None:
        """Handle incoming messages from a connector.

        Args:
            connector_id: The connector ID
            websocket: The WebSocket connection
        """
        async for msg in websocket:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    message = TunnelMessage.model_validate_json(msg.data)
                    await self._handle_message(connector_id, message)
                except Exception as e:
                    logger.error("Failed to process message", connector_id=connector_id, error=str(e))
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.warning("WebSocket error", connector_id=connector_id, error=websocket.exception())
                break
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                logger.info("Connector disconnected", connector_id=connector_id)
                break

    async def _handle_message(self, connector_id: str, message: TunnelMessage) -> None:
        """Handle a message from a connector.

        Args:
            connector_id: The connector ID
            message: The received message
        """
        connector = self._connectors.get(connector_id)
        if not connector:
            return

        correlation_id = message.id

        if message.type == MessageType.PONG:
            # Pong received, connection is healthy
            logger.debug("Received PONG", connector_id=connector_id)
            return

        # Check for pending request
        pending = connector.pending_requests.get(correlation_id)

        if pending is None:
            logger.warning(
                "No pending request for response",
                connector_id=connector_id,
                correlation_id=correlation_id,
            )
            return

        if isinstance(pending, asyncio.Future):
            # Non-streaming response
            if message.type in (MessageType.RESPONSE, MessageType.ERROR) and not pending.done():
                pending.set_result(message)
        elif isinstance(pending, asyncio.Queue):
            # Streaming response
            await pending.put(message)
            if message.type in (MessageType.STREAM_END, MessageType.ERROR):
                # Signal end of stream
                await pending.put(None)
                connector.pending_requests.pop(correlation_id, None)

    async def _ping_loop(self, connector_id: str) -> None:
        """Send periodic pings to keep connection alive.

        Args:
            connector_id: The connector ID
        """
        while self._running:
            await asyncio.sleep(self.ping_interval)

            connector = self._connectors.get(connector_id)
            if not connector:
                break

            try:
                ping_msg = create_ping_message(f"ping-{uuid.uuid4().hex[:8]}")
                await connector.websocket.send_str(ping_msg.model_dump_json())
                logger.debug("Sent PING", connector_id=connector_id)
            except Exception as e:
                logger.warning("Failed to send ping", connector_id=connector_id, error=str(e))
                break
