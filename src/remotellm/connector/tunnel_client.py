"""WebSocket tunnel client for connecting to the broker."""

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from remotellm.shared.logging import get_logger
from remotellm.shared.protocol import (
    MessageType,
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
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


# Type for request handler callback
RequestHandler = Callable[[TunnelMessage], Coroutine[Any, Any, None]]


class TunnelClient:
    """WebSocket client for tunnel connection to broker."""

    def __init__(
        self,
        broker_url: str,
        broker_token: str,
        request_handler: RequestHandler,
        models: list[str] | None = None,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 300.0,
        keepalive_interval: float = 60.0,
    ):
        """Initialize the tunnel client.

        Args:
            broker_url: WebSocket URL of the broker
            broker_token: Authentication token for the broker
            request_handler: Async callback for handling incoming requests
            models: List of model names served by this connector
            reconnect_base_delay: Base delay for exponential backoff (default 1s)
            reconnect_max_delay: Maximum delay between reconnection attempts (default 5min)
            keepalive_interval: Interval in seconds between keepalive pings (default 60s)
        """
        self.broker_url = broker_url
        self.broker_token = broker_token
        self.request_handler = request_handler
        self.models = models or []
        self.reconnect_base_delay = reconnect_base_delay
        self.reconnect_max_delay = reconnect_max_delay
        self.keepalive_interval = keepalive_interval

        self._state = ConnectionState.DISCONNECTED
        self._ws: ClientConnection | None = None
        self._session_id: str | None = None
        self._reconnect_attempt = 0
        self._running = False
        self._send_lock = asyncio.Lock()
        self._keepalive_task: asyncio.Task | None = None

    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self._state

    @property
    def session_id(self) -> str | None:
        """Get current session ID if connected."""
        return self._session_id

    async def connect(self) -> bool:
        """Connect and authenticate with the broker.

        Returns:
            True if connection successful, False otherwise
        """
        self._state = ConnectionState.CONNECTING
        logger.info("Connecting to broker", url=self.broker_url)

        try:
            self._ws = await websockets.connect(self.broker_url)
            self._state = ConnectionState.AUTHENTICATING

            # Send auth message with models list
            auth_id = f"auth-{uuid.uuid4().hex[:8]}"
            auth_msg = create_auth_message(auth_id, self.broker_token, self.models)
            await self._ws.send(auth_msg.model_dump_json())
            logger.debug("Sent AUTH message", correlation_id=auth_id, models=self.models)

            # Wait for auth response
            response_raw = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
            response = TunnelMessage.model_validate_json(response_raw)

            if response.type == MessageType.AUTH_OK:
                self._session_id = response.payload.get("session_id")
                self._state = ConnectionState.CONNECTED
                self._reconnect_attempt = 0
                logger.info("Connected to broker", session_id=self._session_id)
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
        if self._ws is None or self._state != ConnectionState.CONNECTED:
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
                self._state = ConnectionState.DISCONNECTED
                await self._handle_reconnect()
            except Exception as e:
                logger.error("Message loop error", error=str(e))
                self._stop_keepalive()
                self._state = ConnectionState.DISCONNECTED
                await self._handle_reconnect()

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
        else:
            logger.warning("Unexpected message type", type=message.type)

    async def _keepalive_loop(self) -> None:
        """Send periodic keepalive pings to maintain connection."""
        while self._running and self._state == ConnectionState.CONNECTED:
            try:
                await asyncio.sleep(self.keepalive_interval)
                if self._state == ConnectionState.CONNECTED:
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
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._state = ConnectionState.DISCONNECTED
        logger.info("Tunnel client stopped")
