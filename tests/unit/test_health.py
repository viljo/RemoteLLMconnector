"""Unit tests for health endpoints in broker and connector."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from remotellm.broker.health import HealthServer as BrokerHealthServer
from remotellm.broker.router import ModelRouter
from remotellm.connector.health import HealthServer as ConnectorHealthServer
from remotellm.connector.relay_client import ConnectionState


class TestBrokerHealthInit:
    """Tests for BrokerHealthServer initialization."""

    def test_basic_init(self):
        """Test basic initialization."""
        relay_server = MagicMock()
        router = ModelRouter()

        health_server = BrokerHealthServer(
            port=8080,
            relay_server=relay_server,
            router=router,
        )

        assert health_server.port == 8080
        assert health_server.relay_server is relay_server
        assert health_server.router is router
        assert health_server._runner is None

    def test_init_without_router(self):
        """Test initialization without router."""
        relay_server = MagicMock()

        health_server = BrokerHealthServer(
            port=9090,
            relay_server=relay_server,
            router=None,
        )

        assert health_server.port == 9090
        assert health_server.router is None


class TestBrokerHealthEndpoint(AioHTTPTestCase):
    """Integration tests for broker /health endpoint."""

    async def get_application(self):
        """Create the test application."""
        self.relay_server = MagicMock()
        self.relay_server.connector_count = 0
        self.router = ModelRouter()

        self.health_server = BrokerHealthServer(
            port=8080,
            relay_server=self.relay_server,
            router=self.router,
        )

        return self.health_server._app

    @unittest_run_loop
    async def test_broker_health_endpoint_returns_status(self):
        """Test that /health endpoint returns healthy status."""
        self.relay_server.connector_count = 0

        response = await self.client.request("GET", "/health")

        assert response.status == 200
        data = await response.json()
        assert data["status"] == "healthy"
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0

    @unittest_run_loop
    async def test_broker_health_shows_connector_count(self):
        """Test that /health endpoint shows connector count."""
        self.relay_server.connector_count = 3

        response = await self.client.request("GET", "/health")

        assert response.status == 200
        data = await response.json()
        assert data["connectors_connected"] == 3

    @unittest_run_loop
    async def test_broker_health_shows_models(self):
        """Test that /health endpoint shows available models."""
        # Register some connectors with models
        self.router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4", "gpt-3.5-turbo"],
            llm_api_key=None,
        )
        self.router.on_connector_registered(
            connector_id="conn-2",
            models=["llama3"],
            llm_api_key=None,
        )

        response = await self.client.request("GET", "/health")

        assert response.status == 200
        data = await response.json()
        assert "models" in data
        assert "model_count" in data
        assert data["model_count"] == 3
        assert set(data["models"]) == {"gpt-4", "gpt-3.5-turbo", "llama3"}

    @unittest_run_loop
    async def test_broker_health_without_router(self):
        """Test /health endpoint when no router is configured."""
        # Temporarily set router to None to test that case
        original_router = self.health_server.router
        self.health_server.router = None
        self.relay_server.connector_count = 2

        response = await self.client.request("GET", "/health")

        assert response.status == 200
        data = await response.json()
        assert data["models"] == []
        assert data["model_count"] == 0

        # Restore original router
        self.health_server.router = original_router


class TestBrokerReadyEndpoint(AioHTTPTestCase):
    """Integration tests for broker /ready endpoint."""

    async def get_application(self):
        """Create the test application."""
        self.relay_server = MagicMock()
        self.relay_server.connector_count = 0
        self.router = ModelRouter()

        self.health_server = BrokerHealthServer(
            port=8080,
            relay_server=self.relay_server,
            router=self.router,
        )

        return self.health_server._app

    @unittest_run_loop
    async def test_broker_ready_endpoint_with_connectors(self):
        """Test that /ready returns 200 when connectors are connected."""
        self.relay_server.connector_count = 2

        response = await self.client.request("GET", "/ready")

        assert response.status == 200
        data = await response.json()
        assert data["ready"] is True
        assert data["connectors_connected"] == 2

    @unittest_run_loop
    async def test_broker_ready_endpoint_no_connectors(self):
        """Test that /ready returns 503 when no connectors are connected."""
        self.relay_server.connector_count = 0

        response = await self.client.request("GET", "/ready")

        assert response.status == 503
        data = await response.json()
        assert data["ready"] is False
        assert data["connectors_connected"] == 0


class TestConnectorHealthInit:
    """Tests for ConnectorHealthServer initialization."""

    def test_basic_init(self):
        """Test basic initialization."""
        relay_client = MagicMock()
        llm_client = MagicMock()

        health_server = ConnectorHealthServer(
            port=9091,
            relay_client=relay_client,
            llm_client=llm_client,
        )

        assert health_server.port == 9091
        assert health_server.relay_client is relay_client
        assert health_server.llm_client is llm_client
        assert health_server._runner is None


class TestConnectorHealthEndpoint(AioHTTPTestCase):
    """Integration tests for connector /health endpoint."""

    async def get_application(self):
        """Create the test application."""
        self.relay_client = MagicMock()
        self.relay_client.state = ConnectionState.DISCONNECTED
        self.relay_client.models = []
        self.relay_client.session_id = None

        self.llm_client = MagicMock()
        self.llm_client.check_health = AsyncMock(return_value=False)

        self.health_server = ConnectorHealthServer(
            port=9091,
            relay_client=self.relay_client,
            llm_client=self.llm_client,
        )

        return self.health_server._app

    @unittest_run_loop
    async def test_connector_health_when_connected(self):
        """Test /health endpoint when connector is connected to relay."""
        self.relay_client.state = ConnectionState.CONNECTED
        self.relay_client.session_id = "session-123"
        self.llm_client.check_health = AsyncMock(return_value=True)

        response = await self.client.request("GET", "/health")

        assert response.status == 200
        data = await response.json()
        assert data["status"] == "healthy"
        assert data["relay_connected"] is True
        assert data["relay_state"] == "connected"
        assert data["relay_session_id"] == "session-123"
        assert data["llm_available"] is True

    @unittest_run_loop
    async def test_connector_health_when_disconnected(self):
        """Test /health endpoint when connector is disconnected from relay."""
        self.relay_client.state = ConnectionState.DISCONNECTED
        self.relay_client.session_id = None
        self.llm_client.check_health = AsyncMock(return_value=True)

        response = await self.client.request("GET", "/health")

        assert response.status == 503
        data = await response.json()
        assert data["status"] == "unhealthy"
        assert data["relay_connected"] is False
        assert data["relay_state"] == "disconnected"
        assert data["relay_session_id"] is None
        # LLM can still be available even if relay is disconnected
        assert data["llm_available"] is True

    @unittest_run_loop
    async def test_connector_health_shows_models(self):
        """Test /health endpoint shows registered models."""
        self.relay_client.state = ConnectionState.CONNECTED
        self.relay_client.models = ["llama3.2", "mistral:7b"]
        self.relay_client.session_id = "session-456"
        self.llm_client.check_health = AsyncMock(return_value=True)

        response = await self.client.request("GET", "/health")

        assert response.status == 200
        data = await response.json()
        assert data["models"] == ["llama3.2", "mistral:7b"]

    @unittest_run_loop
    async def test_connector_health_uptime(self):
        """Test /health endpoint includes uptime."""
        self.relay_client.state = ConnectionState.CONNECTED
        self.relay_client.session_id = "session-789"
        self.llm_client.check_health = AsyncMock(return_value=True)

        response = await self.client.request("GET", "/health")

        assert response.status == 200
        data = await response.json()
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0

    @unittest_run_loop
    async def test_connector_health_llm_unavailable(self):
        """Test /health endpoint when LLM is unavailable."""
        self.relay_client.state = ConnectionState.CONNECTED
        self.relay_client.session_id = "session-999"
        self.llm_client.check_health = AsyncMock(return_value=False)

        response = await self.client.request("GET", "/health")

        # Still healthy if relay is connected (LLM is informational)
        assert response.status == 200
        data = await response.json()
        assert data["status"] == "healthy"
        assert data["relay_connected"] is True
        assert data["llm_available"] is False

    @unittest_run_loop
    async def test_connector_health_pending_state(self):
        """Test /health endpoint when connector is pending approval."""
        self.relay_client.state = ConnectionState.PENDING
        self.relay_client.session_id = None
        self.llm_client.check_health = AsyncMock(return_value=True)

        response = await self.client.request("GET", "/health")

        assert response.status == 503
        data = await response.json()
        assert data["status"] == "unhealthy"
        assert data["relay_connected"] is False
        assert data["relay_state"] == "pending"


class TestConnectorReadyEndpoint(AioHTTPTestCase):
    """Integration tests for connector /ready endpoint."""

    async def get_application(self):
        """Create the test application."""
        self.relay_client = MagicMock()
        self.relay_client.state = ConnectionState.DISCONNECTED

        self.llm_client = MagicMock()
        self.llm_client.check_health = AsyncMock(return_value=False)

        self.health_server = ConnectorHealthServer(
            port=9091,
            relay_client=self.relay_client,
            llm_client=self.llm_client,
        )

        return self.health_server._app

    @unittest_run_loop
    async def test_connector_ready_when_relay_and_llm_available(self):
        """Test /ready returns 200 when both relay and LLM are available."""
        self.relay_client.state = ConnectionState.CONNECTED
        self.llm_client.check_health = AsyncMock(return_value=True)

        response = await self.client.request("GET", "/ready")

        assert response.status == 200
        data = await response.json()
        assert data["ready"] is True
        assert data["relay_connected"] is True
        assert data["llm_available"] is True

    @unittest_run_loop
    async def test_connector_ready_when_relay_disconnected(self):
        """Test /ready returns 503 when relay is disconnected."""
        self.relay_client.state = ConnectionState.DISCONNECTED
        self.llm_client.check_health = AsyncMock(return_value=True)

        response = await self.client.request("GET", "/ready")

        assert response.status == 503
        data = await response.json()
        assert data["ready"] is False
        assert data["relay_connected"] is False
        assert data["llm_available"] is True

    @unittest_run_loop
    async def test_connector_ready_when_llm_unavailable(self):
        """Test /ready returns 503 when LLM is unavailable."""
        self.relay_client.state = ConnectionState.CONNECTED
        self.llm_client.check_health = AsyncMock(return_value=False)

        response = await self.client.request("GET", "/ready")

        assert response.status == 503
        data = await response.json()
        assert data["ready"] is False
        assert data["relay_connected"] is True
        assert data["llm_available"] is False

    @unittest_run_loop
    async def test_connector_ready_when_both_unavailable(self):
        """Test /ready returns 503 when both relay and LLM are unavailable."""
        self.relay_client.state = ConnectionState.DISCONNECTED
        self.llm_client.check_health = AsyncMock(return_value=False)

        response = await self.client.request("GET", "/ready")

        assert response.status == 503
        data = await response.json()
        assert data["ready"] is False
        assert data["relay_connected"] is False
        assert data["llm_available"] is False

    @unittest_run_loop
    async def test_connector_ready_when_reconnecting(self):
        """Test /ready returns 503 when connector is reconnecting."""
        self.relay_client.state = ConnectionState.RECONNECTING
        self.llm_client.check_health = AsyncMock(return_value=True)

        response = await self.client.request("GET", "/ready")

        assert response.status == 503
        data = await response.json()
        assert data["ready"] is False
        assert data["relay_connected"] is False


class TestBrokerHealthServerLifecycle:
    """Tests for BrokerHealthServer start/stop lifecycle."""

    async def test_start_stop(self):
        """Test starting and stopping the health server."""
        relay_server = MagicMock()
        relay_server.connector_count = 0
        router = ModelRouter()

        health_server = BrokerHealthServer(
            port=18080,  # Use high port to avoid conflicts
            relay_server=relay_server,
            router=router,
        )

        # Start the server
        await health_server.start()
        assert health_server._runner is not None

        # Stop the server
        await health_server.stop()

    async def test_stop_without_start(self):
        """Test stopping without starting doesn't error."""
        relay_server = MagicMock()
        router = ModelRouter()

        health_server = BrokerHealthServer(
            port=18081,
            relay_server=relay_server,
            router=router,
        )

        # Should not raise
        await health_server.stop()


class TestConnectorHealthServerLifecycle:
    """Tests for ConnectorHealthServer start/stop lifecycle."""

    async def test_start_stop(self):
        """Test starting and stopping the connector health server."""
        relay_client = MagicMock()
        relay_client.state = ConnectionState.CONNECTED

        llm_client = MagicMock()
        llm_client.check_health = AsyncMock(return_value=True)

        health_server = ConnectorHealthServer(
            port=19091,  # Use high port to avoid conflicts
            relay_client=relay_client,
            llm_client=llm_client,
        )

        # Start the server
        await health_server.start()
        assert health_server._runner is not None

        # Stop the server
        await health_server.stop()

    async def test_stop_without_start(self):
        """Test stopping without starting doesn't error."""
        relay_client = MagicMock()
        llm_client = MagicMock()

        health_server = ConnectorHealthServer(
            port=19092,
            relay_client=relay_client,
            llm_client=llm_client,
        )

        # Should not raise
        await health_server.stop()
