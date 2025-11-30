"""Integration tests for the complete relay request flow."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from remotellm.broker.api import BrokerAPI
from remotellm.broker.router import ModelRouter
from remotellm.connector.llm_client import LLMClient
from remotellm.shared.protocol import (
    MessageType,
    RelayMessage,
    create_auth_message,
    create_auth_ok_message,
    create_error_message,
    create_request_message,
    create_response_message,
    create_stream_chunk_message,
    create_stream_end_message,
)


class TestProtocolFlow:
    """Tests for the protocol message flow."""

    async def test_auth_flow_success(self):
        """Test successful authentication flow."""
        # Connector sends AUTH
        auth_msg = create_auth_message(
            correlation_id="auth-1",
            token="valid-token",
            models=["gpt-4", "llama3"],
            name="test-connector",
        )

        assert auth_msg.type == MessageType.AUTH
        assert auth_msg.payload["token"] == "valid-token"
        assert auth_msg.payload["models"] == ["gpt-4", "llama3"]

        # Broker responds with AUTH_OK
        auth_ok = create_auth_ok_message(
            correlation_id="auth-1",
            session_id="session-123",
        )

        assert auth_ok.type == MessageType.AUTH_OK
        assert auth_ok.payload["session_id"] == "session-123"

    async def test_request_response_flow(self):
        """Test request/response message flow."""
        # Create request with body
        request_body = json.dumps({
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }).encode()

        request_msg = create_request_message(
            correlation_id="req-123",
            method="POST",
            path="/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            body=base64.b64encode(request_body).decode(),
            llm_api_key="sk-llm-key",
        )

        assert request_msg.type == MessageType.REQUEST
        assert request_msg.payload["method"] == "POST"
        assert request_msg.payload["llm_api_key"] == "sk-llm-key"

        # Decode and verify body
        decoded_body = base64.b64decode(request_msg.payload["body"])
        body_json = json.loads(decoded_body)
        assert body_json["model"] == "gpt-4"

        # Create response
        response_body = json.dumps({
            "id": "chatcmpl-123",
            "choices": [{"message": {"content": "Hello!"}}],
        }).encode()

        response_msg = create_response_message(
            correlation_id="req-123",
            status=200,
            headers={"Content-Type": "application/json"},
            body=base64.b64encode(response_body).decode(),
        )

        assert response_msg.type == MessageType.RESPONSE
        assert response_msg.payload["status"] == 200

    async def test_streaming_flow(self):
        """Test streaming message flow."""
        correlation_id = "stream-123"

        # First chunk with role
        chunk1 = create_stream_chunk_message(
            correlation_id,
            'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
        )
        assert chunk1.type == MessageType.STREAM_CHUNK

        # Content chunks
        chunk2 = create_stream_chunk_message(
            correlation_id,
            'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
        )
        chunk3 = create_stream_chunk_message(
            correlation_id,
            'data: {"choices":[{"delta":{"content":"!"}}]}\n\n',
        )

        # Final chunk with finish reason
        chunk4 = create_stream_chunk_message(
            correlation_id,
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        )

        # Stream end
        end_msg = create_stream_end_message(correlation_id)
        assert end_msg.type == MessageType.STREAM_END

    async def test_error_flow(self):
        """Test error message flow."""
        error_msg = create_error_message(
            correlation_id="req-123",
            status=500,
            error="Internal server error",
            code="internal_error",
        )

        assert error_msg.type == MessageType.ERROR
        assert error_msg.payload["status"] == 500
        assert error_msg.payload["error"] == "Internal server error"


class TestRouterIntegration:
    """Tests for router integration with API."""

    @pytest.fixture
    def router(self):
        """Create a model router."""
        return ModelRouter()

    async def test_route_building(self, router):
        """Test that routes are correctly built when connectors register."""
        # Initially empty
        assert router.available_models == []
        assert router.connector_count == 0

        # Register first connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4", "gpt-3.5-turbo"],
            llm_api_key="sk-key-1",
        )

        assert router.connector_count == 1
        assert "gpt-4" in router.available_models
        assert "gpt-3.5-turbo" in router.available_models

        # Register second connector
        router.on_connector_registered(
            connector_id="conn-2",
            models=["llama3", "codellama"],
            llm_api_key="sk-key-2",
        )

        assert router.connector_count == 2
        assert len(router.available_models) == 4

        # Verify routing
        gpt4_route = router.get_route("gpt-4")
        assert gpt4_route[0] == "conn-1"
        assert gpt4_route[1] == "sk-key-1"

        llama_route = router.get_route("llama3")
        assert llama_route[0] == "conn-2"
        assert llama_route[1] == "sk-key-2"

    async def test_route_failover_on_disconnect(self, router):
        """Test that routes update when connectors disconnect."""
        # Register two connectors with overlapping models
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-key-1",
        )
        router.on_connector_registered(
            connector_id="conn-2",
            models=["gpt-4", "llama3"],
            llm_api_key="sk-key-2",
        )

        # First connector wins for gpt-4
        route = router.get_route("gpt-4")
        assert route[0] == "conn-1"

        # Disconnect first connector
        router.on_connector_disconnected("conn-1")

        # Now second connector handles gpt-4
        route = router.get_route("gpt-4")
        assert route[0] == "conn-2"
        assert route[1] == "sk-key-2"


class TestAPIWithMockedRelay:
    """Tests for API with mocked relay server."""

    @pytest.fixture
    def api_setup(self):
        """Set up API with mocked dependencies."""
        relay_server = MagicMock()
        relay_server._connectors = {}
        router = ModelRouter()

        api = BrokerAPI(
            relay_server=relay_server,
            router=router,
            user_api_keys=["sk-test-key"],
            request_timeout=5.0,
        )

        return api, relay_server, router

    async def test_full_request_flow(self, api_setup):
        """Test complete request flow through API."""
        api, relay_server, router = api_setup

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )

        # Add connector to relay server
        mock_connector = MagicMock()
        relay_server._connectors = {"conn-1": mock_connector}

        # Create mock response
        response_body = json.dumps({
            "id": "chatcmpl-123",
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
        }).encode()

        mock_response = create_response_message(
            correlation_id="req-123",
            status=200,
            headers={"Content-Type": "application/json"},
            body=base64.b64encode(response_body).decode(),
        )

        relay_server.send_request = AsyncMock(return_value=mock_response)

        # Simulate request handling
        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {"authorization": "Bearer sk-test-key"}
        request.read = AsyncMock(return_value=json.dumps({
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode())

        response = await api._handle_chat_completions(request)

        assert response.status == 200
        relay_server.send_request.assert_called_once()


class TestMessageSerialization:
    """Tests for message serialization across the protocol."""

    async def test_message_roundtrip(self):
        """Test message survives JSON serialization roundtrip."""
        original = create_request_message(
            correlation_id="roundtrip-1",
            method="POST",
            path="/v1/chat/completions",
            headers={"Content-Type": "application/json", "X-Custom": "value"},
            body=base64.b64encode(b'{"test": "data"}').decode(),
            llm_api_key="sk-secret-key",
        )

        # Serialize
        json_str = original.model_dump_json()

        # Deserialize
        restored = RelayMessage.model_validate_json(json_str)

        assert restored.type == original.type
        assert restored.id == original.id
        assert restored.payload["method"] == original.payload["method"]
        assert restored.payload["llm_api_key"] == original.payload["llm_api_key"]

    async def test_binary_body_encoding(self):
        """Test that binary bodies are properly base64 encoded."""
        # Create binary body with special characters
        binary_body = b'\x00\x01\x02\xff\xfe\xfd'

        encoded = base64.b64encode(binary_body).decode()
        request_msg = create_request_message(
            correlation_id="binary-1",
            method="POST",
            path="/test",
            headers={},
            body=encoded,
        )

        # Verify we can decode it back
        decoded = base64.b64decode(request_msg.payload["body"])
        assert decoded == binary_body

    async def test_unicode_content(self):
        """Test handling of unicode content."""
        unicode_body = json.dumps({
            "messages": [{"content": "Hello \u4e16\u754c! \ud83d\ude00"}]
        }).encode("utf-8")

        encoded = base64.b64encode(unicode_body).decode()
        request_msg = create_request_message(
            correlation_id="unicode-1",
            method="POST",
            path="/test",
            headers={},
            body=encoded,
        )

        # Serialize and deserialize
        json_str = request_msg.model_dump_json()
        restored = RelayMessage.model_validate_json(json_str)

        # Decode body
        decoded = base64.b64decode(restored.payload["body"])
        body_json = json.loads(decoded.decode("utf-8"))

        assert "Hello" in body_json["messages"][0]["content"]


class TestConcurrentRequests:
    """Tests for concurrent request handling."""

    async def test_multiple_requests_same_model(self):
        """Test handling multiple concurrent requests to same model."""
        router = ModelRouter()
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-key",
        )

        # Simulate multiple concurrent route lookups
        routes = await asyncio.gather(*[
            asyncio.to_thread(router.get_route, "gpt-4")
            for _ in range(100)
        ])

        # All should return same route
        for route in routes:
            assert route is not None
            assert route[0] == "conn-1"

    async def test_router_modification_during_lookup(self):
        """Test router handles modifications during lookups."""
        router = ModelRouter()
        router.on_connector_registered(
            connector_id="conn-1",
            models=["model-a"],
            llm_api_key=None,
        )

        async def lookup_loop():
            results = []
            for _ in range(50):
                route = router.get_route("model-a")
                results.append(route)
                await asyncio.sleep(0.001)
            return results

        async def modify_loop():
            for i in range(10):
                await asyncio.sleep(0.005)
                router.on_connector_registered(
                    connector_id=f"conn-{i+2}",
                    models=["model-a"],
                    llm_api_key=None,
                )

        # Run concurrently - should not raise exceptions
        results, _ = await asyncio.gather(lookup_loop(), modify_loop())

        # Some lookups may return None during rebuild, but should not crash
        valid_results = [r for r in results if r is not None]
        assert len(valid_results) > 0
