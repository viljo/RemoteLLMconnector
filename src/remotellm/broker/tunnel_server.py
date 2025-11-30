"""WebSocket tunnel server for accepting connector connections."""

from __future__ import annotations

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
    ApprovedPayload,
    AuthPayload,
    MessageType,
    TunnelMessage,
    create_approved_message,
    create_auth_fail_message,
    create_auth_ok_message,
    create_pending_message,
    create_ping_message,
    create_revoked_message,
)

if TYPE_CHECKING:
    from remotellm.broker.connectors import Connector, ConnectorStore

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


@dataclass
class PendingConnection:
    """Connection awaiting admin approval."""

    connector_id: str
    websocket: web.WebSocketResponse
    models: list[str]
    name: str | None
    connected_at: float
    auth_correlation_id: str  # For sending APPROVED message


class AuthStatus:
    """Result of authentication attempt."""

    APPROVED = "approved"
    PENDING = "pending"
    FAILED = "failed"


@dataclass
class AuthResult:
    """Result of connector authentication."""

    status: str
    connector_id: str | None = None
    models: list[str] = field(default_factory=list)
    llm_api_key: str | None = None
    name: str | None = None
    correlation_id: str | None = None


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
        connector_store: "ConnectorStore | None" = None,
        auth_timeout: float = 10.0,
        ping_interval: float = 30.0,
        on_connector_registered: "Callable[[str, list[str], str | None], None] | None" = None,
        on_connector_disconnected: "Callable[[str], None] | None" = None,
    ):
        """Initialize the tunnel server.

        Args:
            host: Host to bind to (for standalone mode)
            port: Port to bind to (for standalone mode)
            connector_tokens: Valid authentication tokens for connectors (legacy mode)
            connector_configs: Mapping of token → llm_api_key for connectors (legacy mode)
            connector_store: Persistent connector storage for approval workflow
            auth_timeout: Timeout for connector authentication
            ping_interval: Interval for ping/pong health checks
            on_connector_registered: Callback when connector registers (connector_id, models, llm_api_key)
            on_connector_disconnected: Callback when connector disconnects (connector_id)
        """
        self.host = host
        self.port = port
        self.connector_tokens = connector_tokens or []
        self.connector_configs = connector_configs or {}
        self.connector_store = connector_store
        self.auth_timeout = auth_timeout
        self.ping_interval = ping_interval
        self.on_connector_registered = on_connector_registered
        self.on_connector_disconnected = on_connector_disconnected

        self._connectors: dict[str, ConnectorRegistration] = {}
        self._pending_connections: dict[str, PendingConnection] = {}  # connector_id → pending connection
        self._running = False

    @property
    def connector_count(self) -> int:
        """Get number of connected connectors."""
        return len(self._connectors)

    @property
    def pending_count(self) -> int:
        """Get number of pending (unapproved) connections."""
        return len(self._pending_connections)

    def get_pending_connections(self) -> list[PendingConnection]:
        """Get all pending connections awaiting approval."""
        return list(self._pending_connections.values())

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
        import time

        connector_id: str | None = None
        is_pending = False

        try:
            # Authenticate the connector
            auth_result = await self._authenticate(websocket)

            if auth_result.status == AuthStatus.FAILED:
                return

            connector_id = auth_result.connector_id

            if auth_result.status == AuthStatus.PENDING:
                # Connector is pending approval - keep connection but don't register
                is_pending = True
                pending_conn = PendingConnection(
                    connector_id=connector_id,
                    websocket=websocket,
                    models=auth_result.models,
                    name=auth_result.name,
                    connected_at=time.time(),
                    auth_correlation_id=auth_result.correlation_id or "",
                )
                self._pending_connections[connector_id] = pending_conn
                logger.info(
                    "Connector pending approval",
                    connector_id=connector_id,
                    name=auth_result.name,
                    models=auth_result.models,
                )

                # Keep connection alive but don't process requests
                # Wait for admin approval via notify_approval() or disconnect
                async for msg in websocket:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        # Pending connectors can only receive PING/PONG
                        message = TunnelMessage.model_validate_json(msg.data)
                        if message.type == MessageType.PONG:
                            logger.debug("Received PONG from pending connector", connector_id=connector_id)
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        break
                return

            # Approved connector - register and process requests
            registration = ConnectorRegistration(
                connector_id=connector_id,
                websocket=websocket,
                connected_at=time.time(),
                models=auth_result.models,
                llm_api_key=auth_result.llm_api_key,
            )
            self._connectors[connector_id] = registration
            logger.info("Connector registered", connector_id=connector_id, models=auth_result.models)

            # Notify callback
            if self.on_connector_registered:
                self.on_connector_registered(connector_id, auth_result.models, auth_result.llm_api_key)

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
            # Clean up pending connection
            if connector_id and connector_id in self._pending_connections:
                del self._pending_connections[connector_id]
                logger.info("Pending connector disconnected", connector_id=connector_id)

            # Clean up registered connector
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

    async def _authenticate(self, websocket: web.WebSocketResponse) -> AuthResult:
        """Authenticate a connector connection.

        Args:
            websocket: The WebSocket connection

        Returns:
            AuthResult with status (APPROVED, PENDING, or FAILED)
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
                return AuthResult(status=AuthStatus.FAILED)

            payload = AuthPayload.model_validate(message.payload)
            models = payload.models
            name = payload.name

            # Check if using approval workflow (connector_store) or legacy mode (connector_tokens)
            if self.connector_store is not None:
                return await self._authenticate_with_store(websocket, message, payload)

            # Legacy mode: validate against connector_tokens list
            if self.connector_tokens and payload.token not in self.connector_tokens:
                logger.warning("Invalid connector token")
                fail_msg = create_auth_fail_message(message.id, "Invalid token")
                await websocket.send_str(fail_msg.model_dump_json())
                await websocket.close()
                return AuthResult(status=AuthStatus.FAILED)

            # Look up llm_api_key from config by token
            llm_api_key = self.connector_configs.get(payload.token) if payload.token else None

            # Generate connector ID and send AUTH_OK
            connector_id = f"conn-{uuid.uuid4().hex[:8]}"
            ok_msg = create_auth_ok_message(message.id, connector_id)
            await websocket.send_str(ok_msg.model_dump_json())

            logger.info(
                "Connector authenticated (legacy mode)",
                connector_id=connector_id,
                models=models,
                has_llm_api_key=llm_api_key is not None,
            )
            return AuthResult(
                status=AuthStatus.APPROVED,
                connector_id=connector_id,
                models=models,
                llm_api_key=llm_api_key,
                name=name,
                correlation_id=message.id,
            )

        except TimeoutError:
            logger.warning("Authentication timeout")
            await websocket.close()
            return AuthResult(status=AuthStatus.FAILED)
        except Exception as e:
            logger.error("Authentication error", error=str(e))
            await websocket.close()
            return AuthResult(status=AuthStatus.FAILED)

    async def _authenticate_with_store(
        self,
        websocket: web.WebSocketResponse,
        message: TunnelMessage,
        payload: AuthPayload,
    ) -> AuthResult:
        """Authenticate using ConnectorStore for approval workflow.

        Args:
            websocket: The WebSocket connection
            message: The AUTH message
            payload: The parsed AuthPayload

        Returns:
            AuthResult with status (APPROVED, PENDING, or FAILED)
        """
        from remotellm.broker.connectors import ConnectorStatus

        models = payload.models
        name = payload.name

        # If token provided, check against connector store
        if payload.token:
            connector = self.connector_store.get_by_api_key(payload.token)

            if connector is not None:
                if connector.status == ConnectorStatus.APPROVED:
                    # Valid approved connector - authenticate
                    ok_msg = create_auth_ok_message(message.id, connector.connector_id)
                    await websocket.send_str(ok_msg.model_dump_json())

                    # Update models if different
                    if models != connector.models:
                        self.connector_store.update_models(connector.connector_id, models)

                    # Update last connected time
                    self.connector_store.update_last_connected(connector)

                    logger.info(
                        "Connector authenticated (approved)",
                        connector_id=connector.connector_id,
                        name=connector.name,
                        models=models,
                    )
                    return AuthResult(
                        status=AuthStatus.APPROVED,
                        connector_id=connector.connector_id,
                        models=models,
                        llm_api_key=None,  # TODO: Add llm_api_key to Connector model if needed
                        name=connector.name,
                        correlation_id=message.id,
                    )

                elif connector.status == ConnectorStatus.REVOKED:
                    # Revoked connector - reject
                    logger.warning("Revoked connector attempted to connect", connector_id=connector.connector_id)
                    fail_msg = create_auth_fail_message(message.id, "API key has been revoked")
                    await websocket.send_str(fail_msg.model_dump_json())
                    await websocket.close()
                    return AuthResult(status=AuthStatus.FAILED)

                # Pending connector trying to use token - this shouldn't happen normally
                # Fall through to create new pending entry

        # No token or invalid token - create pending connector
        pending_connector = self.connector_store.create_pending(models=models, name=name)

        # Send PENDING message
        pending_msg = create_pending_message(message.id, pending_connector.connector_id)
        await websocket.send_str(pending_msg.model_dump_json())

        logger.info(
            "Connector pending approval",
            connector_id=pending_connector.connector_id,
            name=name,
            models=models,
        )
        return AuthResult(
            status=AuthStatus.PENDING,
            connector_id=pending_connector.connector_id,
            models=models,
            name=name,
            correlation_id=message.id,
        )

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

    async def notify_approval(self, connector_id: str, api_key: str) -> bool:
        """Notify a pending connector that it has been approved.

        Sends APPROVED message with the generated API key. The connector
        will save this key and transition to authenticated state.

        Args:
            connector_id: The connector ID to approve
            api_key: The generated API key for this connector

        Returns:
            True if notification was sent, False if connector not found
        """
        pending = self._pending_connections.get(connector_id)
        if pending is None:
            logger.warning("No pending connection for approval", connector_id=connector_id)
            return False

        try:
            # Send APPROVED message
            approved_msg = create_approved_message(pending.auth_correlation_id, api_key)
            await pending.websocket.send_str(approved_msg.model_dump_json())

            logger.info("Sent approval to connector", connector_id=connector_id)

            # The connector will disconnect and reconnect with the new API key
            # Or we could transition it directly to registered state here
            # For simplicity, we'll close the connection and let it reconnect
            await pending.websocket.close()

            return True
        except Exception as e:
            logger.error("Failed to send approval", connector_id=connector_id, error=str(e))
            return False

    async def notify_revoke(self, connector_id: str, reason: str | None = None) -> bool:
        """Notify a connector that its API key has been revoked.

        Sends REVOKED message and closes the connection.

        Args:
            connector_id: The connector ID to revoke
            reason: Optional reason for revocation

        Returns:
            True if notification was sent, False if connector not found
        """
        # Check registered connectors
        connector = self._connectors.get(connector_id)
        if connector is not None:
            try:
                # Send REVOKED message
                revoked_msg = create_revoked_message(f"revoke-{uuid.uuid4().hex[:8]}", reason)
                await connector.websocket.send_str(revoked_msg.model_dump_json())
                await connector.websocket.close()

                logger.info("Revoked connector", connector_id=connector_id, reason=reason)
                return True
            except Exception as e:
                logger.error("Failed to send revoke", connector_id=connector_id, error=str(e))
                return False

        # Check pending connections
        pending = self._pending_connections.get(connector_id)
        if pending is not None:
            try:
                await pending.websocket.close()
                logger.info("Closed pending connection", connector_id=connector_id)
                return True
            except Exception as e:
                logger.error("Failed to close pending connection", connector_id=connector_id, error=str(e))
                return False

        logger.warning("No connection found for revoke", connector_id=connector_id)
        return False

    def is_connector_connected(self, connector_id: str) -> bool:
        """Check if a connector is currently connected.

        Args:
            connector_id: The connector ID to check

        Returns:
            True if connector is connected (either pending or registered)
        """
        return connector_id in self._connectors or connector_id in self._pending_connections
