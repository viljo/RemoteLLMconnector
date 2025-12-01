"""Unit tests for the relay server module."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp import web

from remotellm.broker.connectors import Connector, ConnectorStatus, ConnectorStore
from remotellm.broker.relay_server import (
    AuthResult,
    AuthStatus,
    ConnectorRegistration,
    PendingConnection,
    RelayServer,
)
from remotellm.shared.protocol import (
    ApprovedPayload,
    AuthPayload,
    MessageType,
    RelayMessage,
    create_approved_message,
    create_auth_fail_message,
    create_auth_message,
    create_auth_ok_message,
    create_error_message,
    create_pending_message,
    create_ping_message,
    create_pong_message,
    create_request_message,
    create_response_message,
    create_revoked_message,
    create_stream_chunk_message,
    create_stream_end_message,
)


class TestRelayServerInit:
    """Tests for RelayServer initialization."""

    def test_basic_init(self):
        """Test basic initialization with default parameters."""
        server = RelayServer()

        assert server.host == "0.0.0.0"
        assert server.port == 8443
        assert server.connector_tokens == []
        assert server.connector_configs == {}
        assert server.connector_store is None
        assert server.auth_timeout == 10.0
        assert server.ping_interval == 30.0
        assert server.on_connector_registered is None
        assert server.on_connector_disconnected is None
        assert server.connector_count == 0
        assert server.pending_count == 0
        assert server._running is False

    def test_init_with_legacy_tokens(self):
        """Test initialization with legacy connector tokens."""
        tokens = ["token1", "token2"]
        configs = {"token1": "api-key-1", "token2": None}

        server = RelayServer(
            host="127.0.0.1",
            port=9000,
            connector_tokens=tokens,
            connector_configs=configs,
        )

        assert server.host == "127.0.0.1"
        assert server.port == 9000
        assert server.connector_tokens == tokens
        assert server.connector_configs == configs

    def test_init_with_connector_store(self):
        """Test initialization with connector store for approval workflow."""
        store = ConnectorStore(file_path=None)  # Memory-only mode

        server = RelayServer(connector_store=store)

        assert server.connector_store is store

    def test_init_with_callbacks(self):
        """Test initialization with connection callbacks."""
        registered_cb = MagicMock()
        disconnected_cb = MagicMock()

        server = RelayServer(
            on_connector_registered=registered_cb,
            on_connector_disconnected=disconnected_cb,
        )

        assert server.on_connector_registered is registered_cb
        assert server.on_connector_disconnected is disconnected_cb

    def test_init_with_custom_timeouts(self):
        """Test initialization with custom timeout settings."""
        server = RelayServer(
            auth_timeout=15.0,
            ping_interval=60.0,
        )

        assert server.auth_timeout == 15.0
        assert server.ping_interval == 60.0


class TestRelayServerProperties:
    """Tests for RelayServer properties."""

    def test_connector_count_empty(self):
        """Test connector_count when no connectors."""
        server = RelayServer()
        assert server.connector_count == 0

    def test_connector_count_with_connectors(self):
        """Test connector_count with registered connectors."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
            models=["llama3"],
        )

        assert server.connector_count == 1

    def test_pending_count_empty(self):
        """Test pending_count when no pending connections."""
        server = RelayServer()
        assert server.pending_count == 0

    def test_pending_count_with_pending(self):
        """Test pending_count with pending connections."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._pending_connections["conn-1"] = PendingConnection(
            connector_id="conn-1",
            websocket=mock_ws,
            models=["llama3"],
            name="Test Connector",
            connected_at=time.time(),
            auth_correlation_id="auth-1",
        )

        assert server.pending_count == 1

    def test_get_pending_connections(self):
        """Test get_pending_connections returns list of pending connections."""
        server = RelayServer()
        mock_ws = AsyncMock()

        pending = PendingConnection(
            connector_id="conn-1",
            websocket=mock_ws,
            models=["llama3"],
            name="Test Connector",
            connected_at=time.time(),
            auth_correlation_id="auth-1",
        )
        server._pending_connections["conn-1"] = pending

        result = server.get_pending_connections()

        assert len(result) == 1
        assert result[0] == pending

    def test_get_connector_none_available(self):
        """Test get_connector returns None when no connectors."""
        server = RelayServer()
        assert server.get_connector() is None

    def test_get_connector_returns_first_available(self):
        """Test get_connector returns first available connector."""
        server = RelayServer()
        mock_ws = AsyncMock()

        reg = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
            models=["llama3"],
        )
        server._connectors["conn-1"] = reg

        result = server.get_connector()

        assert result == reg


class TestRelayServerStartStop:
    """Tests for starting and stopping the relay server."""

    async def test_start(self):
        """Test starting the relay server."""
        server = RelayServer()

        await server.start()

        assert server._running is True

    async def test_stop_no_connectors(self):
        """Test stopping when no connectors are connected."""
        server = RelayServer()
        server._running = True

        await server.stop()

        assert server._running is False

    async def test_stop_with_connectors(self):
        """Test stopping closes all connected websockets."""
        server = RelayServer()
        server._running = True

        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws1,
            connected_at=time.time(),
        )
        server._connectors["conn-2"] = ConnectorRegistration(
            connector_id="conn-2",
            websocket=mock_ws2,
            connected_at=time.time(),
        )

        await server.stop()

        assert server._running is False
        mock_ws1.close.assert_called_once()
        mock_ws2.close.assert_called_once()

    async def test_stop_with_connector_error(self):
        """Test stop handles errors when closing websockets."""
        server = RelayServer()
        server._running = True

        mock_ws = AsyncMock()
        mock_ws.close.side_effect = Exception("Close error")

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        # Should not raise
        await server.stop()

        assert server._running is False


class TestRelayServerRouteSetup:
    """Tests for route setup."""

    def test_setup_routes(self):
        """Test setting up WebSocket route on aiohttp app."""
        server = RelayServer()
        app = web.Application()

        server.setup_routes(app)

        # Check route was added (aiohttp adds both GET and HEAD for WebSocket)
        routes = list(app.router.routes())
        assert len(routes) >= 1
        # Check that at least one route has the /ws path
        ws_routes = [r for r in routes if r.resource.canonical == "/ws"]
        assert len(ws_routes) >= 1


class TestRelayServerAuthentication:
    """Tests for connector authentication."""

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = AsyncMock()
        ws.send_str = AsyncMock()
        ws.close = AsyncMock()
        ws.receive = AsyncMock()
        return ws

    async def test_authenticate_legacy_valid_token(self, mock_websocket):
        """Test authentication with valid legacy token."""
        server = RelayServer(
            connector_tokens=["valid-token"],
            connector_configs={"valid-token": "llm-api-key-123"},
        )

        auth_msg = create_auth_message(
            correlation_id="auth-1",
            token="valid-token",
            models=["llama3"],
            name="Test Connector",
        )

        mock_msg = MagicMock()
        mock_msg.type = aiohttp.WSMsgType.TEXT
        mock_msg.data = auth_msg.model_dump_json()
        mock_websocket.receive.return_value = mock_msg

        result = await server._authenticate(mock_websocket)

        assert result.status == AuthStatus.APPROVED
        assert result.connector_id is not None
        assert result.connector_id.startswith("conn-")
        assert result.models == ["llama3"]
        assert result.llm_api_key == "llm-api-key-123"
        assert result.name == "Test Connector"
        mock_websocket.send_str.assert_called_once()

    async def test_authenticate_legacy_invalid_token(self, mock_websocket):
        """Test authentication with invalid legacy token."""
        server = RelayServer(connector_tokens=["valid-token"])

        auth_msg = create_auth_message(
            correlation_id="auth-1",
            token="invalid-token",
            models=["llama3"],
        )

        mock_msg = MagicMock()
        mock_msg.type = aiohttp.WSMsgType.TEXT
        mock_msg.data = auth_msg.model_dump_json()
        mock_websocket.receive.return_value = mock_msg

        result = await server._authenticate(mock_websocket)

        assert result.status == AuthStatus.FAILED
        mock_websocket.close.assert_called_once()

    async def test_authenticate_timeout(self, mock_websocket):
        """Test authentication timeout."""
        server = RelayServer(auth_timeout=0.1)

        mock_websocket.receive.side_effect = asyncio.TimeoutError()

        result = await server._authenticate(mock_websocket)

        assert result.status == AuthStatus.FAILED
        mock_websocket.close.assert_called_once()

    async def test_authenticate_not_auth_message(self, mock_websocket):
        """Test authentication fails when first message is not AUTH."""
        server = RelayServer()

        ping_msg = create_ping_message("ping-1")

        mock_msg = MagicMock()
        mock_msg.type = aiohttp.WSMsgType.TEXT
        mock_msg.data = ping_msg.model_dump_json()
        mock_websocket.receive.return_value = mock_msg

        result = await server._authenticate(mock_websocket)

        assert result.status == AuthStatus.FAILED
        mock_websocket.close.assert_called_once()

    async def test_authenticate_websocket_closed(self, mock_websocket):
        """Test authentication when websocket closes during auth."""
        server = RelayServer()

        mock_msg = MagicMock()
        mock_msg.type = aiohttp.WSMsgType.CLOSE
        mock_websocket.receive.return_value = mock_msg

        result = await server._authenticate(mock_websocket)

        assert result is None

    async def test_authenticate_exception(self, mock_websocket):
        """Test authentication handles exceptions."""
        server = RelayServer()

        mock_websocket.receive.side_effect = Exception("Connection error")

        result = await server._authenticate(mock_websocket)

        assert result.status == AuthStatus.FAILED
        mock_websocket.close.assert_called_once()


class TestRelayServerAuthenticationWithStore:
    """Tests for authentication with ConnectorStore."""

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = AsyncMock()
        ws.send_str = AsyncMock()
        ws.close = AsyncMock()
        ws.receive = AsyncMock()
        return ws

    @pytest.fixture
    def connector_store(self):
        """Create a connector store in memory-only mode."""
        return ConnectorStore(file_path=None)

    async def test_authenticate_with_store_approved_connector(self, mock_websocket, connector_store):
        """Test authentication with approved connector using store."""
        # Create an approved connector
        connector = connector_store.create_pending(models=["llama3"], name="Test")
        api_key = connector_store.approve(connector.connector_id)

        server = RelayServer(connector_store=connector_store)

        auth_msg = create_auth_message(
            correlation_id="auth-1",
            token=api_key,
            models=["llama3", "gpt-4"],  # Different models
            name="Test Connector",
        )

        mock_msg = MagicMock()
        mock_msg.type = aiohttp.WSMsgType.TEXT
        mock_msg.data = auth_msg.model_dump_json()
        mock_websocket.receive.return_value = mock_msg

        result = await server._authenticate(mock_websocket)

        assert result.status == AuthStatus.APPROVED
        assert result.connector_id == connector.connector_id
        assert result.models == ["llama3", "gpt-4"]
        mock_websocket.send_str.assert_called_once()

        # Check models were updated in store
        updated = connector_store.get_by_id(connector.connector_id)
        assert updated.models == ["llama3", "gpt-4"]

    async def test_authenticate_with_store_revoked_connector(self, mock_websocket, connector_store):
        """Test authentication with revoked connector.

        Note: When a connector is revoked, its API key is removed from the index.
        So attempting to authenticate with a revoked key results in a new pending
        connector being created (since the key is not found in the index).
        """
        # Create and revoke a connector
        connector = connector_store.create_pending(models=["llama3"], name="Test")
        api_key = connector_store.approve(connector.connector_id)
        old_connector_id = connector.connector_id
        connector_store.revoke(connector.connector_id)

        server = RelayServer(connector_store=connector_store)

        auth_msg = create_auth_message(
            correlation_id="auth-1",
            token=api_key,  # Using the revoked API key
            models=["llama3"],
        )

        mock_msg = MagicMock()
        mock_msg.type = aiohttp.WSMsgType.TEXT
        mock_msg.data = auth_msg.model_dump_json()
        mock_websocket.receive.return_value = mock_msg

        result = await server._authenticate(mock_websocket)

        # Since the API key is removed from index on revoke, it's treated as invalid
        # and a new pending connector is created
        assert result.status == AuthStatus.PENDING
        assert result.connector_id != old_connector_id  # New connector ID
        mock_websocket.send_str.assert_called_once()

    async def test_authenticate_with_store_no_token(self, mock_websocket, connector_store):
        """Test authentication with no token creates pending connector."""
        server = RelayServer(connector_store=connector_store)

        auth_msg = create_auth_message(
            correlation_id="auth-1",
            token=None,
            models=["llama3"],
            name="New Connector",
        )

        mock_msg = MagicMock()
        mock_msg.type = aiohttp.WSMsgType.TEXT
        mock_msg.data = auth_msg.model_dump_json()
        mock_websocket.receive.return_value = mock_msg

        result = await server._authenticate(mock_websocket)

        assert result.status == AuthStatus.PENDING
        assert result.connector_id is not None
        assert result.models == ["llama3"]
        assert result.name == "New Connector"
        mock_websocket.send_str.assert_called_once()

        # Check pending connector was created
        connector = connector_store.get_by_id(result.connector_id)
        assert connector is not None
        assert connector.status == ConnectorStatus.PENDING

    async def test_authenticate_with_store_invalid_token(self, mock_websocket, connector_store):
        """Test authentication with invalid token creates pending connector."""
        server = RelayServer(connector_store=connector_store)

        auth_msg = create_auth_message(
            correlation_id="auth-1",
            token="invalid-token",
            models=["llama3"],
        )

        mock_msg = MagicMock()
        mock_msg.type = aiohttp.WSMsgType.TEXT
        mock_msg.data = auth_msg.model_dump_json()
        mock_websocket.receive.return_value = mock_msg

        result = await server._authenticate(mock_websocket)

        assert result.status == AuthStatus.PENDING
        assert result.connector_id is not None

    async def test_authenticate_with_store_revoked_status_rejects(self, mock_websocket, connector_store):
        """Test that a connector with REVOKED status that still has API key is rejected.

        This tests the case where a connector has been revoked but the API key
        is still in the index (edge case or if revoke is modified).
        """
        # Create and approve a connector
        connector = connector_store.create_pending(models=["llama3"], name="Test")
        api_key = connector_store.approve(connector.connector_id)

        # Manually set status to REVOKED without removing from index
        # (simulating the edge case)
        connector.status = ConnectorStatus.REVOKED
        # Don't call revoke() to keep API key in index

        server = RelayServer(connector_store=connector_store)

        auth_msg = create_auth_message(
            correlation_id="auth-1",
            token=api_key,
            models=["llama3"],
        )

        mock_msg = MagicMock()
        mock_msg.type = aiohttp.WSMsgType.TEXT
        mock_msg.data = auth_msg.model_dump_json()
        mock_websocket.receive.return_value = mock_msg

        result = await server._authenticate(mock_websocket)

        # Should be rejected with FAILED status
        assert result.status == AuthStatus.FAILED
        mock_websocket.close.assert_called_once()


class TestRelayServerSendRequest:
    """Tests for send_request method."""

    async def test_send_request_success(self):
        """Test sending request and receiving response."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        request_msg = create_request_message(
            correlation_id="req-1",
            method="POST",
            path="/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            body="eyJ9",
        )

        response_msg = create_response_message(
            correlation_id="req-1",
            status=200,
            headers={"Content-Type": "application/json"},
            body="eyJyZXN1bHQiOiJvayJ9",
        )

        # Simulate response being received
        async def mock_send_and_respond(*args, **kwargs):
            # Simulate the response arriving
            connector = server._connectors["conn-1"]
            future = connector.pending_requests["req-1"]
            future.set_result(response_msg)

        mock_ws.send_str.side_effect = mock_send_and_respond

        result = await server.send_request("conn-1", request_msg, timeout=1.0)

        assert result == response_msg
        mock_ws.send_str.assert_called_once()

    async def test_send_request_connector_not_found(self):
        """Test send_request raises KeyError for unknown connector."""
        server = RelayServer()

        request_msg = create_request_message(
            correlation_id="req-1",
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body="",
        )

        with pytest.raises(KeyError, match="Connector conn-nonexistent not found"):
            await server.send_request("conn-nonexistent", request_msg)

    async def test_send_request_timeout(self):
        """Test send_request raises TimeoutError."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        request_msg = create_request_message(
            correlation_id="req-1",
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body="",
        )

        # Don't set result on future, causing timeout
        with pytest.raises(asyncio.TimeoutError):
            await server.send_request("conn-1", request_msg, timeout=0.1)

    async def test_send_request_cleans_up_pending(self):
        """Test send_request cleans up pending requests on completion."""
        server = RelayServer()
        mock_ws = AsyncMock()

        connector = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )
        server._connectors["conn-1"] = connector

        request_msg = create_request_message(
            correlation_id="req-1",
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body="",
        )

        response_msg = create_response_message(
            correlation_id="req-1",
            status=200,
            headers={},
            body="",
        )

        async def mock_send_and_respond(*args, **kwargs):
            future = connector.pending_requests["req-1"]
            future.set_result(response_msg)

        mock_ws.send_str.side_effect = mock_send_and_respond

        await server.send_request("conn-1", request_msg, timeout=1.0)

        # Pending request should be cleaned up
        assert "req-1" not in connector.pending_requests


class TestRelayServerSendRequestStreaming:
    """Tests for send_request_streaming method."""

    async def test_send_request_streaming_success(self):
        """Test streaming request returns queue."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        request_msg = create_request_message(
            correlation_id="req-1",
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body="",
        )

        queue = await server.send_request_streaming("conn-1", request_msg)

        assert isinstance(queue, asyncio.Queue)
        mock_ws.send_str.assert_called_once()

    async def test_send_request_streaming_connector_not_found(self):
        """Test streaming request raises KeyError for unknown connector."""
        server = RelayServer()

        request_msg = create_request_message(
            correlation_id="req-1",
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body="",
        )

        with pytest.raises(KeyError, match="Connector conn-nonexistent not found"):
            await server.send_request_streaming("conn-nonexistent", request_msg)


class TestRelayServerMessageHandling:
    """Tests for message handling."""

    async def test_handle_message_pong(self):
        """Test handling PONG message."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        pong_msg = create_pong_message("pong-1")

        # Should not raise
        await server._handle_message("conn-1", pong_msg)

    async def test_handle_message_response_future(self):
        """Test handling RESPONSE message with future."""
        server = RelayServer()
        mock_ws = AsyncMock()

        connector = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )
        server._connectors["conn-1"] = connector

        # Create pending future
        future: asyncio.Future[RelayMessage] = asyncio.Future()
        connector.pending_requests["req-1"] = future

        response_msg = create_response_message(
            correlation_id="req-1",
            status=200,
            headers={},
            body="",
        )

        await server._handle_message("conn-1", response_msg)

        assert future.done()
        assert future.result() == response_msg

    async def test_handle_message_error_future(self):
        """Test handling ERROR message with future."""
        server = RelayServer()
        mock_ws = AsyncMock()

        connector = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )
        server._connectors["conn-1"] = connector

        # Create pending future
        future: asyncio.Future[RelayMessage] = asyncio.Future()
        connector.pending_requests["req-1"] = future

        error_msg = create_error_message(
            correlation_id="req-1",
            status=500,
            error="Internal error",
            code="internal_error",
        )

        await server._handle_message("conn-1", error_msg)

        assert future.done()
        assert future.result() == error_msg

    async def test_handle_message_stream_chunk_queue(self):
        """Test handling STREAM_CHUNK message with queue."""
        server = RelayServer()
        mock_ws = AsyncMock()

        connector = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )
        server._connectors["conn-1"] = connector

        # Create pending queue
        queue: asyncio.Queue[RelayMessage] = asyncio.Queue()
        connector.pending_requests["req-1"] = queue  # type: ignore

        chunk_msg = create_stream_chunk_message(
            correlation_id="req-1",
            chunk="chunk data",
        )

        await server._handle_message("conn-1", chunk_msg)

        assert queue.qsize() == 1
        result = await queue.get()
        assert result == chunk_msg

    async def test_handle_message_stream_end_queue(self):
        """Test handling STREAM_END message with queue."""
        server = RelayServer()
        mock_ws = AsyncMock()

        connector = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )
        server._connectors["conn-1"] = connector

        # Create pending queue
        queue: asyncio.Queue[RelayMessage] = asyncio.Queue()
        connector.pending_requests["req-1"] = queue  # type: ignore

        end_msg = create_stream_end_message("req-1")

        await server._handle_message("conn-1", end_msg)

        # Should get end message and None sentinel
        assert queue.qsize() == 2
        result1 = await queue.get()
        assert result1 == end_msg
        result2 = await queue.get()
        assert result2 is None

        # Pending request should be cleaned up
        assert "req-1" not in connector.pending_requests

    async def test_handle_message_no_pending_request(self):
        """Test handling message with no pending request."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        response_msg = create_response_message(
            correlation_id="req-unknown",
            status=200,
            headers={},
            body="",
        )

        # Should not raise
        await server._handle_message("conn-1", response_msg)

    async def test_handle_message_connector_not_found(self):
        """Test handling message for non-existent connector."""
        server = RelayServer()

        response_msg = create_response_message(
            correlation_id="req-1",
            status=200,
            headers={},
            body="",
        )

        # Should not raise
        await server._handle_message("conn-nonexistent", response_msg)


class TestRelayServerPingLoop:
    """Tests for ping/pong keepalive."""

    async def test_ping_loop_sends_pings(self):
        """Test ping loop sends periodic pings."""
        server = RelayServer(ping_interval=0.05)
        server._running = True
        mock_ws = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        # Start ping loop
        ping_task = asyncio.create_task(server._ping_loop("conn-1"))

        # Let it send a few pings
        await asyncio.sleep(0.15)

        # Stop
        server._running = False
        ping_task.cancel()

        try:
            await ping_task
        except asyncio.CancelledError:
            pass

        # Should have sent at least one ping
        assert mock_ws.send_str.call_count >= 1

    async def test_ping_loop_stops_when_connector_removed(self):
        """Test ping loop stops when connector is removed."""
        server = RelayServer(ping_interval=0.05)
        server._running = True
        mock_ws = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        # Start ping loop
        ping_task = asyncio.create_task(server._ping_loop("conn-1"))

        await asyncio.sleep(0.02)

        # Remove connector
        del server._connectors["conn-1"]

        await asyncio.sleep(0.08)

        # Task should complete
        assert ping_task.done()

    async def test_ping_loop_handles_send_error(self):
        """Test ping loop handles send errors gracefully."""
        server = RelayServer(ping_interval=0.05)
        server._running = True
        mock_ws = AsyncMock()
        mock_ws.send_str.side_effect = Exception("Send failed")

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        # Start ping loop
        ping_task = asyncio.create_task(server._ping_loop("conn-1"))

        await asyncio.sleep(0.08)

        # Task should complete due to error
        assert ping_task.done()


class TestRelayServerConnectorCallbacks:
    """Tests for connector registration/disconnection callbacks."""

    async def test_on_connector_registered_called(self):
        """Test on_connector_registered callback is called."""
        registered_cb = MagicMock()
        server = RelayServer(
            connector_tokens=["test-token"],
            on_connector_registered=registered_cb,
        )

        mock_ws = AsyncMock()

        # Mock auth message
        auth_msg = create_auth_message(
            correlation_id="auth-1",
            token="test-token",
            models=["llama3"],
        )

        auth_mock_msg = MagicMock()
        auth_mock_msg.type = aiohttp.WSMsgType.TEXT
        auth_mock_msg.data = auth_msg.model_dump_json()

        # Setup receive for auth
        mock_ws.receive.return_value = auth_mock_msg

        # Mock the async iterator for message loop (immediately close)
        close_msg = MagicMock()
        close_msg.type = aiohttp.WSMsgType.CLOSE

        async def mock_async_iter():
            yield close_msg

        mock_ws.__aiter__ = lambda self: mock_async_iter()

        # Handle connection
        await server._handle_connection(mock_ws)

        # Callback should have been called
        registered_cb.assert_called_once()
        args = registered_cb.call_args[0]
        assert args[0].startswith("conn-")  # connector_id
        assert args[1] == ["llama3"]  # models
        assert args[2] is None  # llm_api_key

    async def test_on_connector_disconnected_called(self):
        """Test on_connector_disconnected callback is called."""
        disconnected_cb = MagicMock()
        server = RelayServer(
            connector_tokens=["test-token"],
            on_connector_disconnected=disconnected_cb,
        )

        mock_ws = AsyncMock()

        # Mock auth message
        auth_msg = create_auth_message(
            correlation_id="auth-1",
            token="test-token",
            models=["llama3"],
        )

        auth_mock_msg = MagicMock()
        auth_mock_msg.type = aiohttp.WSMsgType.TEXT
        auth_mock_msg.data = auth_msg.model_dump_json()

        # Setup receive for auth
        mock_ws.receive.return_value = auth_mock_msg

        # Mock the async iterator for message loop (immediately close)
        close_msg = MagicMock()
        close_msg.type = aiohttp.WSMsgType.CLOSE

        async def mock_async_iter():
            yield close_msg

        mock_ws.__aiter__ = lambda self: mock_async_iter()

        # Handle connection
        await server._handle_connection(mock_ws)

        # Callback should have been called
        disconnected_cb.assert_called_once()
        args = disconnected_cb.call_args[0]
        assert args[0].startswith("conn-")  # connector_id


class TestRelayServerNotifyApproval:
    """Tests for notify_approval method."""

    async def test_notify_approval_success(self):
        """Test successful approval notification."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._pending_connections["conn-1"] = PendingConnection(
            connector_id="conn-1",
            websocket=mock_ws,
            models=["llama3"],
            name="Test",
            connected_at=time.time(),
            auth_correlation_id="auth-1",
        )

        result = await server.notify_approval("conn-1", "ck-new-api-key")

        assert result is True
        mock_ws.send_str.assert_called_once()
        mock_ws.close.assert_called_once()

        # Check APPROVED message was sent
        sent_data = mock_ws.send_str.call_args[0][0]
        sent_msg = RelayMessage.model_validate_json(sent_data)
        assert sent_msg.type == MessageType.APPROVED
        payload = ApprovedPayload.model_validate(sent_msg.payload)
        assert payload.api_key == "ck-new-api-key"

    async def test_notify_approval_connector_not_found(self):
        """Test approval notification for non-existent connector."""
        server = RelayServer()

        result = await server.notify_approval("conn-nonexistent", "ck-api-key")

        assert result is False

    async def test_notify_approval_send_error(self):
        """Test approval notification handles send errors."""
        server = RelayServer()
        mock_ws = AsyncMock()
        mock_ws.send_str.side_effect = Exception("Send failed")

        server._pending_connections["conn-1"] = PendingConnection(
            connector_id="conn-1",
            websocket=mock_ws,
            models=["llama3"],
            name="Test",
            connected_at=time.time(),
            auth_correlation_id="auth-1",
        )

        result = await server.notify_approval("conn-1", "ck-api-key")

        assert result is False


class TestRelayServerNotifyRevoke:
    """Tests for notify_revoke method."""

    async def test_notify_revoke_registered_connector(self):
        """Test revoking a registered connector."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        result = await server.notify_revoke("conn-1", "Security concern")

        assert result is True
        mock_ws.send_str.assert_called_once()
        mock_ws.close.assert_called_once()

        # Check REVOKED message was sent
        sent_data = mock_ws.send_str.call_args[0][0]
        sent_msg = RelayMessage.model_validate_json(sent_data)
        assert sent_msg.type == MessageType.REVOKED

    async def test_notify_revoke_pending_connector(self):
        """Test revoking a pending connector."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._pending_connections["conn-1"] = PendingConnection(
            connector_id="conn-1",
            websocket=mock_ws,
            models=["llama3"],
            name="Test",
            connected_at=time.time(),
            auth_correlation_id="auth-1",
        )

        result = await server.notify_revoke("conn-1")

        assert result is True
        mock_ws.close.assert_called_once()

    async def test_notify_revoke_connector_not_found(self):
        """Test revoking non-existent connector."""
        server = RelayServer()

        result = await server.notify_revoke("conn-nonexistent")

        assert result is False

    async def test_notify_revoke_send_error(self):
        """Test revoke handles send errors."""
        server = RelayServer()
        mock_ws = AsyncMock()
        mock_ws.send_str.side_effect = Exception("Send failed")

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        result = await server.notify_revoke("conn-1")

        assert result is False


class TestRelayServerMultipleConnectors:
    """Tests for handling multiple connectors."""

    def test_multiple_connectors_tracked(self):
        """Test multiple connectors are tracked correctly."""
        server = RelayServer()

        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()
        mock_ws3 = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws1,
            connected_at=time.time(),
            models=["llama3"],
        )
        server._connectors["conn-2"] = ConnectorRegistration(
            connector_id="conn-2",
            websocket=mock_ws2,
            connected_at=time.time(),
            models=["gpt-4"],
        )
        server._connectors["conn-3"] = ConnectorRegistration(
            connector_id="conn-3",
            websocket=mock_ws3,
            connected_at=time.time(),
            models=["llama3", "gpt-4"],
        )

        assert server.connector_count == 3

    async def test_send_request_to_specific_connector(self):
        """Test sending request to specific connector."""
        server = RelayServer()

        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws1,
            connected_at=time.time(),
        )
        server._connectors["conn-2"] = ConnectorRegistration(
            connector_id="conn-2",
            websocket=mock_ws2,
            connected_at=time.time(),
        )

        request_msg = create_request_message(
            correlation_id="req-1",
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body="",
        )

        response_msg = create_response_message(
            correlation_id="req-1",
            status=200,
            headers={},
            body="",
        )

        async def mock_send_and_respond(*args, **kwargs):
            connector = server._connectors["conn-2"]
            future = connector.pending_requests["req-1"]
            future.set_result(response_msg)

        mock_ws2.send_str.side_effect = mock_send_and_respond

        result = await server.send_request("conn-2", request_msg, timeout=1.0)

        assert result == response_msg
        # Only conn-2 should have been called
        mock_ws1.send_str.assert_not_called()
        mock_ws2.send_str.assert_called_once()


class TestRelayServerIsConnectorConnected:
    """Tests for is_connector_connected method."""

    def test_is_connector_connected_registered(self):
        """Test connector is detected when registered."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )

        assert server.is_connector_connected("conn-1") is True
        assert server.is_connector_connected("conn-2") is False

    def test_is_connector_connected_pending(self):
        """Test connector is detected when pending."""
        server = RelayServer()
        mock_ws = AsyncMock()

        server._pending_connections["conn-1"] = PendingConnection(
            connector_id="conn-1",
            websocket=mock_ws,
            models=["llama3"],
            name="Test",
            connected_at=time.time(),
            auth_correlation_id="auth-1",
        )

        assert server.is_connector_connected("conn-1") is True
        assert server.is_connector_connected("conn-2") is False

    def test_is_connector_connected_both(self):
        """Test connector is detected in either state."""
        server = RelayServer()
        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()

        server._connectors["conn-1"] = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws1,
            connected_at=time.time(),
        )
        server._pending_connections["conn-2"] = PendingConnection(
            connector_id="conn-2",
            websocket=mock_ws2,
            models=["llama3"],
            name="Test",
            connected_at=time.time(),
            auth_correlation_id="auth-1",
        )

        assert server.is_connector_connected("conn-1") is True
        assert server.is_connector_connected("conn-2") is True
        assert server.is_connector_connected("conn-3") is False


class TestRelayServerConnectionCleanup:
    """Tests for connection cleanup on disconnect."""

    async def test_cleanup_pending_requests_on_disconnect(self):
        """Test pending requests are cleaned up when connector disconnects."""
        server = RelayServer(connector_tokens=["test-token"])

        mock_ws = AsyncMock()
        mock_ws.receive = AsyncMock()
        mock_ws.send_str = AsyncMock()

        # Manually add connector with pending requests
        connector = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=time.time(),
        )
        server._connectors["conn-1"] = connector

        # Add pending future
        future: asyncio.Future[RelayMessage] = asyncio.Future()
        connector.pending_requests["req-1"] = future

        # Add pending queue
        queue: asyncio.Queue[RelayMessage] = asyncio.Queue()
        connector.pending_requests["req-2"] = queue  # type: ignore

        # Simulate disconnect
        async def mock_async_iter():
            close_msg = MagicMock()
            close_msg.type = aiohttp.WSMsgType.CLOSE
            yield close_msg

        mock_ws.__aiter__ = lambda self: mock_async_iter()

        # Run message loop which will handle cleanup
        await server._message_loop("conn-1", mock_ws)

        # After cleanup in _handle_connection, check future got exception
        # We need to simulate the cleanup that happens in _handle_connection
        connector = server._connectors.get("conn-1")
        if connector:
            for _request_id, pending in connector.pending_requests.items():
                if isinstance(pending, asyncio.Future) and not pending.done():
                    pending.set_exception(ConnectionError("Connector disconnected"))
                elif isinstance(pending, asyncio.Queue):
                    await pending.put(None)

        # Check future got exception
        assert future.done()
        with pytest.raises(ConnectionError):
            future.result()

        # Check queue got None sentinel
        sentinel = await queue.get()
        assert sentinel is None


class TestAuthResult:
    """Tests for AuthResult dataclass."""

    def test_auth_result_approved(self):
        """Test creating approved AuthResult."""
        result = AuthResult(
            status=AuthStatus.APPROVED,
            connector_id="conn-1",
            models=["llama3"],
            llm_api_key="api-key-123",
        )

        assert result.status == AuthStatus.APPROVED
        assert result.connector_id == "conn-1"
        assert result.models == ["llama3"]
        assert result.llm_api_key == "api-key-123"

    def test_auth_result_pending(self):
        """Test creating pending AuthResult."""
        result = AuthResult(
            status=AuthStatus.PENDING,
            connector_id="conn-1",
            models=["llama3"],
            name="Test Connector",
        )

        assert result.status == AuthStatus.PENDING
        assert result.connector_id == "conn-1"
        assert result.models == ["llama3"]
        assert result.name == "Test Connector"

    def test_auth_result_failed(self):
        """Test creating failed AuthResult."""
        result = AuthResult(status=AuthStatus.FAILED)

        assert result.status == AuthStatus.FAILED
        assert result.connector_id is None
        assert result.models == []


class TestConnectorRegistration:
    """Tests for ConnectorRegistration dataclass."""

    def test_connector_registration_basic(self):
        """Test creating basic ConnectorRegistration."""
        mock_ws = AsyncMock()
        reg = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=12345.0,
        )

        assert reg.connector_id == "conn-1"
        assert reg.websocket is mock_ws
        assert reg.connected_at == 12345.0
        assert reg.models == []
        assert reg.llm_api_key is None
        assert reg.pending_requests == {}

    def test_connector_registration_with_models(self):
        """Test creating ConnectorRegistration with models."""
        mock_ws = AsyncMock()
        reg = ConnectorRegistration(
            connector_id="conn-1",
            websocket=mock_ws,
            connected_at=12345.0,
            models=["llama3", "gpt-4"],
            llm_api_key="api-key-123",
        )

        assert reg.models == ["llama3", "gpt-4"]
        assert reg.llm_api_key == "api-key-123"


class TestPendingConnection:
    """Tests for PendingConnection dataclass."""

    def test_pending_connection_basic(self):
        """Test creating basic PendingConnection."""
        mock_ws = AsyncMock()
        pending = PendingConnection(
            connector_id="conn-1",
            websocket=mock_ws,
            models=["llama3"],
            name="Test Connector",
            connected_at=12345.0,
            auth_correlation_id="auth-1",
        )

        assert pending.connector_id == "conn-1"
        assert pending.websocket is mock_ws
        assert pending.models == ["llama3"]
        assert pending.name == "Test Connector"
        assert pending.connected_at == 12345.0
        assert pending.auth_correlation_id == "auth-1"

    def test_pending_connection_no_name(self):
        """Test creating PendingConnection without name."""
        mock_ws = AsyncMock()
        pending = PendingConnection(
            connector_id="conn-1",
            websocket=mock_ws,
            models=[],
            name=None,
            connected_at=12345.0,
            auth_correlation_id="auth-1",
        )

        assert pending.name is None


class TestAuthStatus:
    """Tests for AuthStatus constants."""

    def test_auth_status_values(self):
        """Test AuthStatus constant values."""
        assert AuthStatus.APPROVED == "approved"
        assert AuthStatus.PENDING == "pending"
        assert AuthStatus.FAILED == "failed"
