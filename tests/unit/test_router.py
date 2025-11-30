"""Unit tests for the model router module."""

import pytest

from remotellm.broker.router import ConnectorInfo, ModelRouter, RouteInfo


class TestRouteInfo:
    """Tests for RouteInfo dataclass."""

    def test_create_route_info(self):
        """Test creating a RouteInfo."""
        route = RouteInfo(connector_id="conn-123", llm_api_key="sk-key")
        assert route.connector_id == "conn-123"
        assert route.llm_api_key == "sk-key"

    def test_create_route_info_no_key(self):
        """Test creating RouteInfo without API key."""
        route = RouteInfo(connector_id="conn-456", llm_api_key=None)
        assert route.connector_id == "conn-456"
        assert route.llm_api_key is None


class TestConnectorInfo:
    """Tests for ConnectorInfo dataclass."""

    def test_create_connector_info(self):
        """Test creating ConnectorInfo."""
        info = ConnectorInfo(
            connector_id="conn-123",
            models=["gpt-4", "llama3"],
            llm_api_key="sk-key",
        )
        assert info.connector_id == "conn-123"
        assert info.models == ["gpt-4", "llama3"]
        assert info.llm_api_key == "sk-key"

    def test_create_connector_info_empty_models(self):
        """Test creating ConnectorInfo with no models."""
        info = ConnectorInfo(
            connector_id="conn-456",
            models=[],
            llm_api_key=None,
        )
        assert info.models == []


class TestModelRouter:
    """Tests for ModelRouter class."""

    @pytest.fixture
    def router(self):
        """Create a fresh router for each test."""
        return ModelRouter()

    def test_empty_router(self, router):
        """Test newly created router is empty."""
        assert router.available_models == []
        assert router.connector_count == 0

    def test_register_single_connector(self, router):
        """Test registering a single connector."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4", "llama3"],
            llm_api_key="sk-key-1",
        )

        assert router.connector_count == 1
        assert "gpt-4" in router.available_models
        assert "llama3" in router.available_models

    def test_get_route(self, router):
        """Test getting a route for a model."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-key-1",
        )

        route = router.get_route("gpt-4")
        assert route is not None
        connector_id, llm_api_key = route
        assert connector_id == "conn-1"
        assert llm_api_key == "sk-key-1"

    def test_get_route_not_found(self, router):
        """Test getting route for unknown model."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-key-1",
        )

        route = router.get_route("unknown-model")
        assert route is None

    def test_register_multiple_connectors(self, router):
        """Test registering multiple connectors."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-key-1",
        )
        router.on_connector_registered(
            connector_id="conn-2",
            models=["llama3", "codellama"],
            llm_api_key="sk-key-2",
        )

        assert router.connector_count == 2
        assert len(router.available_models) == 3

        # Check routing
        gpt4_route = router.get_route("gpt-4")
        assert gpt4_route[0] == "conn-1"

        llama_route = router.get_route("llama3")
        assert llama_route[0] == "conn-2"

    def test_first_connector_wins_for_same_model(self, router):
        """Test that first connector wins when same model is registered twice."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-key-1",
        )
        router.on_connector_registered(
            connector_id="conn-2",
            models=["gpt-4"],  # Same model
            llm_api_key="sk-key-2",
        )

        route = router.get_route("gpt-4")
        assert route[0] == "conn-1"  # First connector wins

    def test_disconnect_connector(self, router):
        """Test disconnecting a connector."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4", "llama3"],
            llm_api_key="sk-key-1",
        )
        router.on_connector_registered(
            connector_id="conn-2",
            models=["codellama"],
            llm_api_key="sk-key-2",
        )

        # Disconnect first connector
        router.on_connector_disconnected("conn-1")

        assert router.connector_count == 1
        assert router.get_route("gpt-4") is None
        assert router.get_route("llama3") is None
        assert router.get_route("codellama") is not None

    def test_disconnect_unknown_connector(self, router):
        """Test disconnecting an unknown connector doesn't error."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key=None,
        )

        # Should not raise
        router.on_connector_disconnected("unknown-conn")

        # Original connector still there
        assert router.connector_count == 1

    def test_disconnect_allows_model_rerouting(self, router):
        """Test that disconnecting allows model to be routed to another connector."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-key-1",
        )
        router.on_connector_registered(
            connector_id="conn-2",
            models=["gpt-4"],  # Same model
            llm_api_key="sk-key-2",
        )

        # First connector wins initially
        route = router.get_route("gpt-4")
        assert route[0] == "conn-1"

        # Disconnect first connector
        router.on_connector_disconnected("conn-1")

        # Now second connector should handle gpt-4
        route = router.get_route("gpt-4")
        assert route[0] == "conn-2"

    def test_get_connector_models(self, router):
        """Test getting models for a specific connector."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4", "llama3"],
            llm_api_key=None,
        )

        models = router.get_connector_models("conn-1")
        assert models == ["gpt-4", "llama3"]

    def test_get_connector_models_unknown(self, router):
        """Test getting models for unknown connector."""
        models = router.get_connector_models("unknown")
        assert models is None

    def test_get_all_models_with_connectors(self, router):
        """Test getting all models with their connector IDs."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key=None,
        )
        router.on_connector_registered(
            connector_id="conn-2",
            models=["llama3", "codellama"],
            llm_api_key=None,
        )

        mapping = router.get_all_models_with_connectors()
        assert mapping["gpt-4"] == "conn-1"
        assert mapping["llama3"] == "conn-2"
        assert mapping["codellama"] == "conn-2"

    def test_get_connector_info(self, router):
        """Test getting connector info for display."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4", "llama3"],
            llm_api_key="sk-key",  # Should not appear in output
        )
        router.on_connector_registered(
            connector_id="conn-2",
            models=["codellama"],
            llm_api_key=None,
        )

        info = router.get_connector_info()
        assert len(info) == 2

        # Find conn-1 info
        conn1_info = next(i for i in info if i["id"] == "conn-1")
        assert conn1_info["models"] == ["gpt-4", "llama3"]
        assert conn1_info["model_count"] == 2
        assert conn1_info["connected"] is True
        assert "llm_api_key" not in conn1_info  # Should not expose secrets

    def test_empty_connector_info(self, router):
        """Test getting connector info when no connectors."""
        info = router.get_connector_info()
        assert info == []

    def test_connector_with_no_api_key(self, router):
        """Test connector registration without API key."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["local-model"],
            llm_api_key=None,
        )

        route = router.get_route("local-model")
        assert route is not None
        connector_id, llm_api_key = route
        assert connector_id == "conn-1"
        assert llm_api_key is None

    def test_build_routes_clears_old_routes(self, router):
        """Test that build_routes clears and rebuilds routes."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["model-a"],
            llm_api_key=None,
        )

        assert router.get_route("model-a") is not None

        # Manually manipulate to verify rebuild clears old data
        router._routes["orphan-model"] = RouteInfo("orphan", None)

        # Registering new connector triggers rebuild
        router.on_connector_registered(
            connector_id="conn-2",
            models=["model-b"],
            llm_api_key=None,
        )

        # Orphan should be gone after rebuild
        assert router.get_route("orphan-model") is None
        assert router.get_route("model-a") is not None
        assert router.get_route("model-b") is not None

    def test_register_connector_with_empty_models(self, router):
        """Test registering a connector with no models."""
        router.on_connector_registered(
            connector_id="conn-empty",
            models=[],
            llm_api_key=None,
        )

        assert router.connector_count == 1
        assert router.available_models == []

    def test_available_models_property(self, router):
        """Test available_models returns correct list."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["b-model", "a-model"],
            llm_api_key=None,
        )

        models = router.available_models
        assert "a-model" in models
        assert "b-model" in models
        assert len(models) == 2

    def test_connector_count_property(self, router):
        """Test connector_count returns correct count."""
        assert router.connector_count == 0

        router.on_connector_registered("conn-1", ["m1"], None)
        assert router.connector_count == 1

        router.on_connector_registered("conn-2", ["m2"], None)
        assert router.connector_count == 2

        router.on_connector_disconnected("conn-1")
        assert router.connector_count == 1


class TestModelRouterConcurrency:
    """Tests for router behavior with rapid changes."""

    @pytest.fixture
    def router(self):
        """Create a fresh router for each test."""
        return ModelRouter()

    def test_rapid_connect_disconnect(self, router):
        """Test rapid connector connect/disconnect cycles."""
        for i in range(10):
            router.on_connector_registered(f"conn-{i}", [f"model-{i}"], None)

        assert router.connector_count == 10
        assert len(router.available_models) == 10

        for i in range(5):
            router.on_connector_disconnected(f"conn-{i}")

        assert router.connector_count == 5
        assert len(router.available_models) == 5

    def test_duplicate_registration(self, router):
        """Test registering same connector ID twice updates it."""
        router.on_connector_registered(
            connector_id="conn-1",
            models=["model-a"],
            llm_api_key="key-1",
        )
        router.on_connector_registered(
            connector_id="conn-1",
            models=["model-b"],
            llm_api_key="key-2",
        )

        # Should still be only 1 connector
        assert router.connector_count == 1

        # Should have new models
        assert router.get_route("model-a") is None
        route = router.get_route("model-b")
        assert route is not None
        assert route[1] == "key-2"
