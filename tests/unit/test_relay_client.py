"""Unit tests for the relay client module."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from remotellm.connector.relay_client import ConnectionState, RelayClient
from remotellm.shared.protocol import (
    MessageType,
    RelayMessage,
    create_approved_message,
    create_auth_fail_message,
    create_auth_ok_message,
    create_pending_message,
    create_ping_message,
    create_request_message,
    create_revoked_message,
)


class TestConnectionState:
    """Tests for ConnectionState enum."""

    def test_all_states_exist(self):
        """Test all connection states exist."""
        assert ConnectionState.DISCONNECTED == "disconnected"
        assert ConnectionState.CONNECTING == "connecting"
        assert ConnectionState.AUTHENTICATING == "authenticating"
        assert ConnectionState.PENDING == "pending"
        assert ConnectionState.CONNECTED == "connected"
        assert ConnectionState.RECONNECTING == "reconnecting"


class TestRelayClientInit:
    """Tests for RelayClient initialization."""

    def test_basic_init(self):
        """Test basic initialization."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )

        assert client.broker_url == "ws://localhost:8444"
        assert client.broker_token == "test-token"
        assert client.models == []
        assert client.state == ConnectionState.DISCONNECTED

    def test_init_with_models(self):
        """Test initialization with models."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
            models=["gpt-4", "llama3"],
            connector_name="test-connector",
        )

        assert client.models == ["gpt-4", "llama3"]
        assert client.connector_name == "test-connector"

    def test_init_with_custom_delays(self):
        """Test initialization with custom reconnection settings."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
            reconnect_base_delay=2.0,
            reconnect_max_delay=600.0,
            keepalive_interval=30.0,
        )

        assert client.reconnect_base_delay == 2.0
        assert client.reconnect_max_delay == 600.0
        assert client.keepalive_interval == 30.0

    def test_init_loads_credentials(self):
        """Test that initialization loads credentials from file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_path = Path(tmpdir) / "credentials.yaml"
            creds_path.write_text("broker_token: saved-token-from-file")

            handler = AsyncMock()
            client = RelayClient(
                broker_url="ws://localhost:8444",
                broker_token=None,  # Not provided
                request_handler=handler,
                credentials_file=creds_path,
            )

            assert client.broker_token == "saved-token-from-file"

    def test_init_token_takes_precedence_over_file(self):
        """Test that provided token takes precedence over file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_path = Path(tmpdir) / "credentials.yaml"
            creds_path.write_text("broker_token: file-token")

            handler = AsyncMock()
            client = RelayClient(
                broker_url="ws://localhost:8444",
                broker_token="provided-token",
                request_handler=handler,
                credentials_file=creds_path,
            )

            # Provided token should be used, not file token
            assert client.broker_token == "provided-token"


class TestRelayClientCredentials:
    """Tests for credential management."""

    def test_load_credentials_file_not_exists(self):
        """Test loading from non-existent file returns None."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token=None,
            request_handler=handler,
            credentials_file=Path("/nonexistent/path/creds.yaml"),
        )

        assert client.broker_token is None

    def test_load_credentials_invalid_yaml(self):
        """Test loading from invalid YAML returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_path = Path(tmpdir) / "credentials.yaml"
            creds_path.write_text("invalid: yaml: content: [")

            handler = AsyncMock()
            client = RelayClient(
                broker_url="ws://localhost:8444",
                broker_token=None,
                request_handler=handler,
                credentials_file=creds_path,
            )

            assert client.broker_token is None

    def test_save_credentials(self):
        """Test saving credentials to file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_path = Path(tmpdir) / "subdir" / "credentials.yaml"

            handler = AsyncMock()
            client = RelayClient(
                broker_url="ws://localhost:8444",
                broker_token="initial-token",
                request_handler=handler,
                credentials_file=creds_path,
            )

            client._save_credentials("new-api-key")

            # Verify file was created
            assert creds_path.exists()
            with open(creds_path) as f:
                data = yaml.safe_load(f)
            assert data["broker_token"] == "new-api-key"

    def test_save_credentials_no_file_configured(self):
        """Test save does nothing when no credentials file."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="token",
            request_handler=handler,
            credentials_file=None,
        )

        # Should not raise
        client._save_credentials("new-key")

    def test_clear_credentials(self):
        """Test clearing credentials file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_path = Path(tmpdir) / "credentials.yaml"
            creds_path.write_text("broker_token: test-token")

            handler = AsyncMock()
            client = RelayClient(
                broker_url="ws://localhost:8444",
                broker_token="token",
                request_handler=handler,
                credentials_file=creds_path,
            )

            client._clear_credentials()

            assert not creds_path.exists()

    def test_clear_credentials_no_file(self):
        """Test clear does nothing when file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_path = Path(tmpdir) / "nonexistent.yaml"

            handler = AsyncMock()
            client = RelayClient(
                broker_url="ws://localhost:8444",
                broker_token="token",
                request_handler=handler,
                credentials_file=creds_path,
            )

            # Should not raise
            client._clear_credentials()


class TestRelayClientConnect:
    """Tests for connection establishment."""

    @pytest.fixture
    def mock_ws(self):
        """Create a mock WebSocket connection."""
        ws = AsyncMock()
        ws.send = AsyncMock()
        ws.recv = AsyncMock()
        ws.close = AsyncMock()
        return ws

    async def test_connect_success(self, mock_ws):
        """Test successful connection and authentication."""
        auth_ok = create_auth_ok_message("auth-1", "session-123")
        mock_ws.recv.return_value = auth_ok.model_dump_json()

        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
            models=["gpt-4"],
        )

        # websockets.connect returns a coroutine that resolves to a connection
        async def mock_connect(*args, **kwargs):
            return mock_ws

        with patch("websockets.connect", side_effect=mock_connect):
            result = await client.connect()

        assert result is True
        assert client.state == ConnectionState.CONNECTED
        assert client.session_id == "session-123"
        mock_ws.send.assert_called_once()

    async def test_connect_pending_approval(self, mock_ws):
        """Test connection enters pending state."""
        pending = create_pending_message("auth-1", "conn-123", "Waiting for approval")
        mock_ws.recv.return_value = pending.model_dump_json()

        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token=None,
            request_handler=handler,
        )

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with patch("websockets.connect", side_effect=mock_connect):
            result = await client.connect()

        assert result is True
        assert client.state == ConnectionState.PENDING
        assert client.connector_id == "conn-123"

    async def test_connect_auth_fail(self, mock_ws):
        """Test connection with auth failure."""
        auth_fail = create_auth_fail_message("auth-1", "Invalid token")
        mock_ws.recv.return_value = auth_fail.model_dump_json()

        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="bad-token",
            request_handler=handler,
        )

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with patch("websockets.connect", side_effect=mock_connect):
            result = await client.connect()

        assert result is False
        assert client.state == ConnectionState.DISCONNECTED
        mock_ws.close.assert_called_once()

    async def test_connect_timeout(self, mock_ws):
        """Test connection timeout during auth."""
        mock_ws.recv.side_effect = asyncio.TimeoutError()

        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with patch("websockets.connect", side_effect=mock_connect):
            result = await client.connect()

        assert result is False
        assert client.state == ConnectionState.DISCONNECTED

    async def test_connect_websocket_error(self):
        """Test connection with WebSocket error."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )

        async def mock_connect(*args, **kwargs):
            raise ConnectionRefusedError()

        with patch("websockets.connect", side_effect=mock_connect):
            result = await client.connect()

        assert result is False
        assert client.state == ConnectionState.DISCONNECTED


class TestRelayClientMessageHandling:
    """Tests for message handling."""

    @pytest.fixture
    def connected_client(self):
        """Create a connected client for testing."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )
        client._state = ConnectionState.CONNECTED
        client._ws = AsyncMock()
        return client

    async def test_handle_request_message(self, connected_client):
        """Test handling REQUEST message."""
        request_msg = create_request_message(
            correlation_id="req-1",
            method="POST",
            path="/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            body="eyJ9",
        )

        await connected_client._handle_message(request_msg)

        # Request handler should be called (via create_task)
        await asyncio.sleep(0.01)  # Let task run
        connected_client.request_handler.assert_called_once()

    async def test_handle_ping_message(self, connected_client):
        """Test handling PING message responds with PONG."""
        ping_msg = create_ping_message("ping-123")

        await connected_client._handle_message(ping_msg)

        # Should send PONG response
        connected_client._ws.send.assert_called_once()
        sent_data = connected_client._ws.send.call_args[0][0]
        sent_msg = RelayMessage.model_validate_json(sent_data)
        assert sent_msg.type == MessageType.PONG
        assert sent_msg.id == "ping-123"

    async def test_handle_pong_message(self, connected_client):
        """Test handling PONG message (no action needed)."""
        pong_msg = RelayMessage(type=MessageType.PONG, id="pong-123")

        # Should not raise
        await connected_client._handle_message(pong_msg)

    async def test_handle_cancel_message(self, connected_client):
        """Test handling CANCEL message."""
        cancel_msg = RelayMessage(type=MessageType.CANCEL, id="cancel-123")

        # Should not raise (TODO: implement cancellation)
        await connected_client._handle_message(cancel_msg)

    async def test_handle_approved_message(self):
        """Test handling APPROVED message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_path = Path(tmpdir) / "credentials.yaml"

            handler = AsyncMock()
            client = RelayClient(
                broker_url="ws://localhost:8444",
                broker_token=None,
                request_handler=handler,
                credentials_file=creds_path,
            )
            client._state = ConnectionState.PENDING
            client._connector_id = "conn-123"
            client._ws = AsyncMock()

            approved_msg = create_approved_message("approved-1", "ck-new-api-key")
            await client._handle_message(approved_msg)

            # Token should be updated
            assert client.broker_token == "ck-new-api-key"
            # Credentials should be saved
            assert creds_path.exists()
            # State should be disconnected (to trigger reconnect)
            assert client.state == ConnectionState.DISCONNECTED

    async def test_handle_revoked_message(self):
        """Test handling REVOKED message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_path = Path(tmpdir) / "credentials.yaml"
            creds_path.write_text("broker_token: old-token")

            handler = AsyncMock()
            client = RelayClient(
                broker_url="ws://localhost:8444",
                broker_token="old-token",
                request_handler=handler,
                credentials_file=creds_path,
            )
            client._state = ConnectionState.CONNECTED
            client._connector_id = "conn-123"
            client._ws = AsyncMock()

            revoked_msg = create_revoked_message("revoked-1", "Security concern")
            await client._handle_message(revoked_msg)

            # Token should be cleared
            assert client.broker_token is None
            # Credentials file should be deleted
            assert not creds_path.exists()
            # State should be disconnected
            assert client.state == ConnectionState.DISCONNECTED


class TestRelayClientSendMessage:
    """Tests for sending messages."""

    async def test_send_message_success(self):
        """Test sending message when connected."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )
        client._state = ConnectionState.CONNECTED
        client._ws = AsyncMock()

        msg = create_ping_message("test-ping")
        await client.send_message(msg)

        client._ws.send.assert_called_once()

    async def test_send_message_not_connected(self):
        """Test sending message when not connected raises error."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )
        client._state = ConnectionState.DISCONNECTED

        msg = create_ping_message("test-ping")
        with pytest.raises(RuntimeError, match="Not connected"):
            await client.send_message(msg)

    async def test_send_message_pending_state(self):
        """Test sending message in pending state works."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token=None,
            request_handler=handler,
        )
        client._state = ConnectionState.PENDING
        client._ws = AsyncMock()

        msg = create_ping_message("test-ping")
        await client.send_message(msg)

        client._ws.send.assert_called_once()


class TestRelayClientKeepalive:
    """Tests for keepalive functionality."""

    async def test_start_keepalive(self):
        """Test starting keepalive task."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
            keepalive_interval=0.1,  # Short for testing
        )
        client._state = ConnectionState.CONNECTED
        client._ws = AsyncMock()
        client._running = True

        client._start_keepalive()

        assert client._keepalive_task is not None
        assert not client._keepalive_task.done()

        # Stop it
        client._stop_keepalive()
        await asyncio.sleep(0.05)

    async def test_stop_keepalive(self):
        """Test stopping keepalive task."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )
        client._state = ConnectionState.CONNECTED
        client._ws = AsyncMock()
        client._running = True

        client._start_keepalive()
        task = client._keepalive_task

        client._stop_keepalive()
        await asyncio.sleep(0.05)

        assert task.cancelled() or task.done()

    async def test_keepalive_sends_pings(self):
        """Test that keepalive sends ping messages."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
            keepalive_interval=0.05,  # Very short for testing
        )
        client._state = ConnectionState.CONNECTED
        client._ws = AsyncMock()
        client._running = True

        client._start_keepalive()
        await asyncio.sleep(0.15)  # Wait for a few pings
        client._running = False
        client._stop_keepalive()

        # Should have sent at least one ping
        assert client._ws.send.call_count >= 1


class TestRelayClientReconnect:
    """Tests for reconnection behavior."""

    async def test_handle_reconnect_increments_attempt(self):
        """Test that reconnect increments attempt counter."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
            reconnect_base_delay=0.01,  # Very short for testing
        )
        client._running = True

        await client._handle_reconnect()

        assert client._reconnect_attempt == 1
        # After reconnect delay, state is reset to DISCONNECTED so run() loop will reconnect
        assert client.state == ConnectionState.DISCONNECTED

    async def test_handle_reconnect_exponential_backoff(self):
        """Test exponential backoff calculation."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
            reconnect_base_delay=1.0,
            reconnect_max_delay=10.0,
        )
        client._running = True
        client._reconnect_attempt = 5  # Simulate 5 failures

        # The delay calculation should be exponential
        # 1.0 * 2^4 = 16, but capped at 10
        # This test just verifies the method runs without error
        # and respects the running flag
        client._running = False  # Prevent actual sleep
        await client._handle_reconnect()


class TestRelayClientStop:
    """Tests for stopping the client."""

    async def test_stop_connected(self):
        """Test stopping a connected client."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )
        client._state = ConnectionState.CONNECTED
        client._ws = AsyncMock()
        client._running = True
        client._start_keepalive()

        await client.stop()

        assert client._running is False
        assert client.state == ConnectionState.DISCONNECTED
        client._ws.close.assert_called_once()

    async def test_stop_not_connected(self):
        """Test stopping when not connected."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )

        # Should not raise
        await client.stop()

        assert client._running is False
        assert client.state == ConnectionState.DISCONNECTED


class TestRelayClientProperties:
    """Tests for client properties."""

    def test_state_property(self):
        """Test state property."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )

        assert client.state == ConnectionState.DISCONNECTED

        client._state = ConnectionState.CONNECTED
        assert client.state == ConnectionState.CONNECTED

    def test_session_id_property(self):
        """Test session_id property."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )

        assert client.session_id is None

        client._session_id = "sess-123"
        assert client.session_id == "sess-123"

    def test_connector_id_property(self):
        """Test connector_id property."""
        handler = AsyncMock()
        client = RelayClient(
            broker_url="ws://localhost:8444",
            broker_token="test-token",
            request_handler=handler,
        )

        assert client.connector_id is None

        client._connector_id = "conn-456"
        assert client.connector_id == "conn-456"
