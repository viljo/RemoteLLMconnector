"""WebSocket tunnel client for connecting to the broker."""

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from enum import Enum
from pathlib import Path
from typing import Any

import websockets
import yaml
from websockets.asyncio.client import ClientConnection

from remotellm.shared.logging import get_logger
from remotellm.shared.protocol import (
    ApprovedPayload,
    MessageType,
    PendingPayload,
    RevokedPayload,
    TunnelMessage,
    create_auth_message,
    create_ping_message,
    create_pong_message,
)

logger = get_logger(__name__)


class ConnectionState(str, Enum):
    """Tunnel connection states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    PENDING = "pending"  # Waiting for admin approval
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


# Type for request handler callback
RequestHandler = Callable[[TunnelMessage], Coroutine[Any, Any, None]]


class TunnelClient:
    """WebSocket client for tunnel connection to broker."""

    def __init__(
        self,
        broker_url: str,
        broker_token: str | None,
        request_handler: RequestHandler,
        models: list[str] | None = None,
        connector_name: str | None = None,
        credentials_file: Path | None = None,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 300.0,
        keepalive_interval: float = 60.0,
    ):
        """Initialize the tunnel client.

        Args:
            broker_url: WebSocket URL of the broker
            broker_token: Authentication token for the broker (optional, can be loaded from credentials)
            request_handler: Async callback for handling incoming requests
            models: List of model names served by this connector
            connector_name: Optional friendly name for this connector
            credentials_file: Path to store approved API key (for approval workflow)
            reconnect_base_delay: Base delay for exponential backoff (default 1s)
            reconnect_max_delay: Maximum delay between reconnection attempts (default 5min)
            keepalive_interval: Interval in seconds between keepalive pings (default 60s)
        """
        self.broker_url = broker_url
        self.broker_token = broker_token
        self.request_handler = request_handler
        self.models = models or []
        self.connector_name = connector_name
        self.credentials_file = credentials_file
        self.reconnect_base_delay = reconnect_base_delay
        self.reconnect_max_delay = reconnect_max_delay
        self.keepalive_interval = keepalive_interval

        self._state = ConnectionState.DISCONNECTED
        self._ws: ClientConnection | None = None
        self._session_id: str | None = None
        self._connector_id: str | None = None  # Assigned by broker
        self._reconnect_attempt = 0
        self._running = False
        self._send_lock = asyncio.Lock()
        self._keepalive_task: asyncio.Task | None = None

        # Try to load token from credentials file if not provided
        if self.broker_token is None and self.credentials_file:
            self.broker_token = self._load_credentials()

    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self._state

    @property
    def session_id(self) -> str | None:
        """Get current session ID if connected."""
        return self._session_id

    @property
    def connector_id(self) -> str | None:
        """Get connector ID assigned by broker."""
        return self._connector_id

    def _load_credentials(self) -> str | None:
        """Load broker token from credentials file.

        Returns:
            The broker token if found, None otherwise
        """
        if not self.credentials_file:
            return None

        creds_path = Path(self.credentials_file).expanduser()
        if not creds_path.exists():
            return None

        try:
            with open(creds_path) as f:
                data = yaml.safe_load(f)
                token = data.get("broker_token") if data else None
                if token:
                    logger.info("Loaded broker token from credentials file", path=str(creds_path))
                return token
        except Exception as e:
            logger.warning("Failed to load credentials", path=str(creds_path), error=str(e))
            return None

    def _save_credentials(self, api_key: str) -> None:
        """Save broker token to credentials file.

        Args:
            api_key: The API key to save
        """
        if not self.credentials_file:
            return

        creds_path = Path(self.credentials_file).expanduser()
        try:
            creds_path.parent.mkdir(parents=True, exist_ok=True)
            with open(creds_path, "w") as f:
                yaml.dump({"broker_token": api_key}, f)
            logger.info("Saved broker token to credentials file", path=str(creds_path))
        except Exception as e:
            logger.error("Failed to save credentials", path=str(creds_path), error=str(e))

    def _clear_credentials(self) -> None:
        """Clear saved broker token."""
        if not self.credentials_file:
            return

        creds_path = Path(self.credentials_file).expanduser()
        if creds_path.exists():
            try:
                creds_path.unlink()
                logger.info("Cleared credentials file", path=str(creds_path))
            except Exception as e:
                logger.warning("Failed to clear credentials", path=str(creds_path), error=str(e))

    async def connect(self) -> bool:
        """Connect and authenticate with the broker.

        Returns:
            True if connection successful (or entered pending state), False otherwise
        """
        self._state = ConnectionState.CONNECTING
        logger.info("Connecting to broker", url=self.broker_url)

        try:
            self._ws = await websockets.connect(self.broker_url)
            self._state = ConnectionState.AUTHENTICATING

            # Send auth message with models list and optional name
            auth_id = f"auth-{uuid.uuid4().hex[:8]}"
            auth_msg = create_auth_message(
                auth_id, self.broker_token, self.models, self.connector_name
            )
            await self._ws.send(auth_msg.model_dump_json())
            logger.debug(
                "Sent AUTH message",
                correlation_id=auth_id,
                models=self.models,
                name=self.connector_name,
                has_token=self.broker_token is not None,
            )

            # Wait for auth response
            response_raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
            response = TunnelMessage.model_validate_json(response_raw)

            if response.type == MessageType.AUTH_OK:
                self._session_id = response.payload.get("session_id")
                self._state = ConnectionState.CONNECTED
                self._reconnect_attempt = 0
                logger.info("Connected to broker", session_id=self._session_id)
                return True

            elif response.type == MessageType.PENDING:
                # Connector pending admin approval
                payload = PendingPayload.model_validate(response.payload)
                self._connector_id = payload.connector_id
                self._state = ConnectionState.PENDING
                logger.info(
                    "Connector pending admin approval",
                    connector_id=self._connector_id,
                    message=payload.message,
                )
                # Return True to indicate connection is active (just waiting for approval)
                return True

            elif response.type == MessageType.AUTH_FAIL:
                error = response.payload.get("error", "Unknown error")
                logger.error("Authentication failed", error=error)
                self._state = ConnectionState.DISCONNECTED
                await self._ws.close()
                return False

            else:
                logger.error("Unexpected auth response", type=response.type)
                self._state = ConnectionState.DISCONNECTED
                await self._ws.close()
                return False

        except TimeoutError:
            logger.error("Authentication timeout")
            self._state = ConnectionState.DISCONNECTED
            if self._ws:
                await self._ws.close()
            return False
        except Exception as e:
            logger.error("Connection failed", error=str(e))
            self._state = ConnectionState.DISCONNECTED
            if self._ws:
                await self._ws.close()
            return False

    async def send_message(self, message: TunnelMessage) -> None:
        """Send a message through the tunnel.

        Args:
            message: The message to send
        """
        if self._ws is None or self._state not in (ConnectionState.CONNECTED, ConnectionState.PENDING):
            raise RuntimeError("Not connected to broker")

        async with self._send_lock:
            await self._ws.send(message.model_dump_json())

    async def run(self) -> None:
        """Run the tunnel client, handling messages and reconnection."""
        self._running = True

        while self._running:
            if self._state == ConnectionState.DISCONNECTED:
                # Attempt to connect
                success = await self.connect()
                if not success:
                    await self._handle_reconnect()
                    continue
                # Start keepalive after successful connection
                self._start_keepalive()

            try:
                # Listen for messages
                await self._message_loop()
            except websockets.ConnectionClosed as e:
                logger.warning("Connection closed", code=e.code, reason=e.reason)
                self._stop_keepalive()
                self._ws = None  # Clear old connection
                self._state = ConnectionState.DISCONNECTED
                await self._handle_reconnect()
                self._state = ConnectionState.DISCONNECTED  # Reset after delay so loop reconnects
            except Exception as e:
                logger.error("Message loop error", error=str(e))
                self._stop_keepalive()
                self._ws = None  # Clear old connection
                self._state = ConnectionState.DISCONNECTED
                await self._handle_reconnect()
                self._state = ConnectionState.DISCONNECTED  # Reset after delay so loop reconnects

    async def _message_loop(self) -> None:
        """Process incoming messages."""
        if self._ws is None:
            return

        async for raw_message in self._ws:
            if not self._running:
                break

            try:
                message = TunnelMessage.model_validate_json(raw_message)
                await self._handle_message(message)
            except Exception as e:
                logger.error("Failed to process message", error=str(e))

    async def _handle_message(self, message: TunnelMessage) -> None:
        """Handle an incoming message.

        Args:
            message: The received message
        """
        if message.type == MessageType.REQUEST:
            # Forward to request handler
            asyncio.create_task(self.request_handler(message))
        elif message.type == MessageType.PING:
            # Respond with PONG
            pong = create_pong_message(message.id)
            await self.send_message(pong)
        elif message.type == MessageType.PONG:
            # Response to our keepalive ping
            logger.debug("Received keepalive pong", correlation_id=message.id)
        elif message.type == MessageType.CANCEL:
            # TODO: Implement request cancellation
            logger.info("Received cancel request", correlation_id=message.id)
        elif message.type == MessageType.APPROVED:
            # Connector has been approved by admin
            await self._handle_approved(message)
        elif message.type == MessageType.REVOKED:
            # Connector API key has been revoked
            await self._handle_revoked(message)
        else:
            logger.warning("Unexpected message type", type=message.type)

    async def _handle_approved(self, message: TunnelMessage) -> None:
        """Handle APPROVED message from broker.

        Args:
            message: The APPROVED message containing the API key
        """
        payload = ApprovedPayload.model_validate(message.payload)
        logger.info(
            "Connector approved by admin",
            connector_id=self._connector_id,
        )

        # Save the API key to credentials file
        self._save_credentials(payload.api_key)

        # Update our token for future connections
        self.broker_token = payload.api_key

        logger.info(
            "Reconnecting with new API key to complete registration",
            connector_id=self._connector_id,
            credentials_saved=self.credentials_file is not None,
        )

        # Trigger reconnection to properly register with models
        # The pending connection doesn't have models registered with the router,
        # so we need to close and reconnect with the new API key
        self._state = ConnectionState.DISCONNECTED
        self._reconnect_attempt = 0  # Reset so reconnect is immediate

        # Close the websocket to trigger reconnection in run() loop
        if self._ws:
            await self._ws.close()

    async def _handle_revoked(self, message: TunnelMessage) -> None:
        """Handle REVOKED message from broker.

        Args:
            message: The REVOKED message with reason
        """
        payload = RevokedPayload.model_validate(message.payload)
        logger.warning(
            "Connector API key revoked",
            connector_id=self._connector_id,
            reason=payload.reason,
        )

        # Clear saved credentials
        self._clear_credentials()
        self.broker_token = None

        # Transition to disconnected - will trigger reconnect
        self._state = ConnectionState.DISCONNECTED

        # Close the websocket
        if self._ws:
            await self._ws.close()

    async def _keepalive_loop(self) -> None:
        """Send periodic keepalive pings to maintain connection."""
        # Run keepalive in both CONNECTED and PENDING states
        active_states = (ConnectionState.CONNECTED, ConnectionState.PENDING)
        while self._running and self._state in active_states:
            try:
                await asyncio.sleep(self.keepalive_interval)
                if self._state in active_states:
                    ping_id = f"ping-{uuid.uuid4().hex[:8]}"
                    ping_msg = create_ping_message(ping_id)
                    await self.send_message(ping_msg)
                    logger.debug("Sent keepalive ping", correlation_id=ping_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Keepalive ping failed", error=str(e))
                break

    def _start_keepalive(self) -> None:
        """Start the keepalive task."""
        if self._keepalive_task is None or self._keepalive_task.done():
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    def _stop_keepalive(self) -> None:
        """Stop the keepalive task."""
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()

    async def _handle_reconnect(self) -> None:
        """Handle reconnection with exponential backoff.

        Will retry indefinitely until stopped or connection succeeds.
        Delay is capped at reconnect_max_delay (default 5 minutes).
        """
        if not self._running:
            return

        self._state = ConnectionState.RECONNECTING
        self._reconnect_attempt += 1

        # Calculate delay with exponential backoff, capped at max_delay
        import random

        delay = self.reconnect_base_delay * (2 ** min(self._reconnect_attempt - 1, 10))
        delay = min(delay, self.reconnect_max_delay)

        # Add jitter (up to 25% of delay) to prevent thundering herd
        jitter = delay * random.random() * 0.25
        delay += jitter

        logger.info(
            "Reconnecting (will retry indefinitely)",
            attempt=self._reconnect_attempt,
            delay=f"{delay:.1f}s",
            max_delay=f"{self.reconnect_max_delay:.0f}s",
        )
        await asyncio.sleep(delay)

    async def stop(self) -> None:
        """Stop the tunnel client."""
        self._running = False
        self._stop_keepalive()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass  # Connection may already be closed
        self._state = ConnectionState.DISCONNECTED
        logger.info("Tunnel client stopped")
