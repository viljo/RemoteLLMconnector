"""Comprehensive error handling and edge case tests for the RemoteLLM system.

Tests various failure scenarios including:
- Malformed requests
- Invalid/missing fields
- Timeouts and network errors
- Authentication failures
- Connector failures
- Streaming errors
"""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aiohttp.test_utils import AioHTTPTestCase

from remotellm.broker.api import BrokerAPI, create_error_response
from remotellm.broker.router import ModelRouter
from remotellm.connector.llm_client import LLMClient
from remotellm.shared.protocol import (
    create_error_message,
    create_response_message,
    create_stream_chunk_message,
)


class TestMalformedRequests(AioHTTPTestCase):
    """Tests for malformed request handling."""

    async def get_application(self):
        """Create application for testing."""
        self.relay_server = MagicMock()
        self.relay_server._connectors = {}
        self.router = ModelRouter()

        self.api = BrokerAPI(
            relay_server=self.relay_server,
            router=self.router,
            user_api_keys=["sk-test-key"],
            request_timeout=5.0,
        )
        return self.api.app

    async def test_malformed_json_body(self):
        """Test request with malformed JSON in body."""
        resp = await self.client.request(
            "POST",
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-test-key",
                "Content-Type": "application/json",
            },
            data=b"{this is not valid json}",
        )

        assert resp.status == 400
        data = await resp.json()
        assert data["error"]["type"] == "invalid_request_error"
        assert data["error"]["code"] == "missing_model"

    async def test_empty_request_body(self):
        """Test request with empty body."""
        resp = await self.client.request(
            "POST",
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-test-key",
                "Content-Type": "application/json",
            },
            data=b"",
        )

        assert resp.status == 400
        data = await resp.json()
        assert data["error"]["type"] == "invalid_request_error"
        assert data["error"]["code"] == "missing_model"

    async def test_missing_model_field(self):
        """Test request missing required 'model' field."""
        request_body = {
            "messages": [{"role": "user", "content": "Hello"}],
        }

        resp = await self.client.request(
            "POST",
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-test-key",
                "Content-Type": "application/json",
            },
            json=request_body,
        )

        assert resp.status == 400
        data = await resp.json()
        assert data["error"]["message"] == "Missing 'model' field in request"
        assert data["error"]["code"] == "missing_model"

    async def test_null_model_field(self):
        """Test request with null model field."""
        request_body = {
            "model": None,
            "messages": [{"role": "user", "content": "Hello"}],
        }

        resp = await self.client.request(
            "POST",
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-test-key",
                "Content-Type": "application/json",
            },
            json=request_body,
        )

        assert resp.status == 400
        data = await resp.json()
        assert data["error"]["code"] == "missing_model"

    async def test_empty_messages_array(self):
        """Test request with empty messages array.

        Note: The broker validates the model field, but validation of messages
        is delegated to the LLM server. This test ensures the request is
        forwarded correctly even with empty messages.
        """
        # Register connector for the model
        self.router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        self.relay_server._connectors = {"conn-1": MagicMock()}

        # Mock LLM response with validation error
        error_msg = create_error_message(
            correlation_id="req-123",
            status=400,
            error="messages array cannot be empty",
            code="invalid_request_error",
        )
        self.relay_server.send_request = AsyncMock(return_value=error_msg)

        request_body = {
            "model": "gpt-4",
            "messages": [],
        }

        resp = await self.client.request(
            "POST",
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-test-key",
                "Content-Type": "application/json",
            },
            json=request_body,
        )

        # Request should be forwarded and error returned
        assert resp.status == 400

    async def test_invalid_message_role(self):
        """Test request with invalid message role.

        Similar to empty messages, role validation is delegated to the LLM.
        """
        # Register connector
        self.router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        self.relay_server._connectors = {"conn-1": MagicMock()}

        # Mock LLM response with validation error
        error_msg = create_error_message(
            correlation_id="req-123",
            status=400,
            error="Invalid message role: 'invalid_role'",
            code="invalid_request_error",
        )
        self.relay_server.send_request = AsyncMock(return_value=error_msg)

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "invalid_role", "content": "Hello"}],
        }

        resp = await self.client.request(
            "POST",
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-test-key",
                "Content-Type": "application/json",
            },
            json=request_body,
        )

        assert resp.status == 400


class TestModelRouting:
    """Tests for model routing error cases."""

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

    async def test_invalid_model_name(self, api_setup):
        """Test request for model that doesn't exist."""
        api, relay_server, router = api_setup

        request_body = {
            "model": "nonexistent-model",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {"authorization": "Bearer sk-test-key"}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        assert response.status == 404
        body = json.loads(response.body)
        assert body["error"]["message"] == "Model 'nonexistent-model' not found"
        assert body["error"]["code"] == "model_not_found"

    async def test_connector_disconnected_after_routing(self, api_setup):
        """Test when connector disconnects after route is found but before request."""
        api, relay_server, router = api_setup

        # Register model
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )

        # Don't add connector to relay_server._connectors (simulates disconnect)

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {"authorization": "Bearer sk-test-key"}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        assert response.status == 503
        body = json.loads(response.body)
        assert body["error"]["type"] == "service_unavailable"
        assert body["error"]["code"] == "connector_unavailable"


class TestAuthentication:
    """Tests for authentication error cases."""

    @pytest.fixture
    def api_setup(self):
        """Set up API with authentication enabled."""
        relay_server = MagicMock()
        relay_server._connectors = {}
        router = ModelRouter()

        api = BrokerAPI(
            relay_server=relay_server,
            router=router,
            user_api_keys=["sk-valid-key", "sk-another-key"],
            request_timeout=5.0,
        )

        return api, relay_server, router

    async def test_missing_authorization_header(self, api_setup):
        """Test request without Authorization header."""
        api, relay_server, router = api_setup

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        assert response.status == 401
        body = json.loads(response.body)
        assert body["error"]["type"] == "authentication_error"
        assert body["error"]["code"] == "invalid_api_key"

    async def test_invalid_authorization_format(self, api_setup):
        """Test request with malformed Authorization header."""
        api, relay_server, router = api_setup

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {"authorization": "InvalidFormat sk-key"}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        assert response.status == 401
        body = json.loads(response.body)
        assert body["error"]["type"] == "authentication_error"

    async def test_invalid_api_key(self, api_setup):
        """Test request with invalid API key."""
        api, relay_server, router = api_setup

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {"authorization": "Bearer sk-invalid-key"}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        assert response.status == 401
        body = json.loads(response.body)
        assert body["error"]["message"] == "Invalid API key"
        assert body["error"]["code"] == "invalid_api_key"

    async def test_no_auth_required_when_disabled(self):
        """Test that requests work without auth when API keys list is empty."""
        relay_server = MagicMock()
        relay_server._connectors = {}
        router = ModelRouter()

        # Create API with empty user_api_keys (no auth)
        api = BrokerAPI(
            relay_server=relay_server,
            router=router,
            user_api_keys=[],
            request_timeout=5.0,
        )

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        relay_server._connectors = {"conn-1": MagicMock()}

        # Mock response
        response_msg = create_response_message(
            correlation_id="req-123",
            status=200,
            headers={"Content-Type": "application/json"},
            body=base64.b64encode(b'{"result": "ok"}').decode(),
        )
        relay_server.send_request = AsyncMock(return_value=response_msg)

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {}  # No auth header
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        # Should succeed without auth
        assert response.status == 200


class TestTimeouts:
    """Tests for timeout scenarios."""

    @pytest.fixture
    def api_setup(self):
        """Set up API with short timeout."""
        relay_server = MagicMock()
        relay_server._connectors = {}
        router = ModelRouter()

        api = BrokerAPI(
            relay_server=relay_server,
            router=router,
            user_api_keys=["sk-test-key"],
            request_timeout=0.1,  # Very short timeout
        )

        return api, relay_server, router

    async def test_request_timeout(self, api_setup):
        """Test timeout when LLM takes too long to respond."""
        api, relay_server, router = api_setup

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        relay_server._connectors = {"conn-1": MagicMock()}

        # Mock send_request to raise TimeoutError
        relay_server.send_request = AsyncMock(side_effect=TimeoutError("Request timeout"))

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {"authorization": "Bearer sk-test-key"}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        assert response.status == 504
        body = json.loads(response.body)
        assert body["error"]["type"] == "timeout"
        assert body["error"]["code"] == "timeout"


class TestLLMErrors:
    """Tests for errors from the LLM server."""

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

    async def test_llm_returns_500_error(self, api_setup):
        """Test when LLM server returns HTTP 500."""
        api, relay_server, router = api_setup

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        relay_server._connectors = {"conn-1": MagicMock()}

        # Mock error response
        error_msg = create_error_message(
            correlation_id="req-123",
            status=500,
            error="Internal server error",
            code="internal_error",
        )
        relay_server.send_request = AsyncMock(return_value=error_msg)

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {"authorization": "Bearer sk-test-key"}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        assert response.status == 500
        body = json.loads(response.body)
        assert body["error"]["message"] == "Internal server error"

    async def test_llm_returns_503_error(self, api_setup):
        """Test when LLM server returns HTTP 503 (service unavailable)."""
        api, relay_server, router = api_setup

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        relay_server._connectors = {"conn-1": MagicMock()}

        # Mock 503 error
        error_msg = create_error_message(
            correlation_id="req-123",
            status=503,
            error="Model is loading",
            code="service_unavailable",
        )
        relay_server.send_request = AsyncMock(return_value=error_msg)

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {"authorization": "Bearer sk-test-key"}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        assert response.status == 503


class TestConnectorDisconnection:
    """Tests for connector disconnection scenarios."""

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

    async def test_connector_disconnects_mid_request(self, api_setup):
        """Test when connector disconnects while processing request."""
        api, relay_server, router = api_setup

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        relay_server._connectors = {"conn-1": MagicMock()}

        # Mock send_request to raise ConnectionError
        relay_server.send_request = AsyncMock(
            side_effect=ConnectionError("Connector disconnected")
        )

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {"authorization": "Bearer sk-test-key"}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        assert response.status == 502
        body = json.loads(response.body)
        assert body["error"]["type"] == "bad_gateway"
        assert body["error"]["code"] == "connector_unavailable"


class TestStreamingErrors:
    """Tests for streaming error scenarios."""

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

    async def test_streaming_timeout(self, api_setup):
        """Test timeout during streaming response.

        Note: The streaming response is prepared before data is sent,
        so this test verifies the response object is created even if
        the stream times out later during iteration.
        """
        api, relay_server, router = api_setup

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        relay_server._connectors = {"conn-1": MagicMock()}

        # Create a queue that never sends data (simulates timeout)
        async def create_hanging_queue(*_args, **_kwargs):
            queue = asyncio.Queue()
            # Don't put anything in queue - will timeout on get
            return queue

        relay_server.send_request_streaming = AsyncMock(
            side_effect=create_hanging_queue
        )

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        }

        # Create a proper aiohttp request mock
        from aiohttp.test_utils import make_mocked_request

        request = make_mocked_request(
            "POST",
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-test-key",
                "Content-Type": "application/json",
            },
        )
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        # Streaming response should be returned (timeout happens during iteration)
        assert response.status == 200

    async def test_streaming_error_mid_stream(self, api_setup):
        """Test error occurring in the middle of a stream."""
        api, relay_server, router = api_setup

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        relay_server._connectors = {"conn-1": MagicMock()}

        # Create queue with some chunks then error
        async def create_error_queue(*_args, **_kwargs):
            queue = asyncio.Queue()
            # Add a few chunks
            await queue.put(create_stream_chunk_message("req-1", "data: chunk1\n\n"))
            await queue.put(create_stream_chunk_message("req-1", "data: chunk2\n\n"))
            # Add error
            await queue.put(create_error_message("req-1", 500, "Stream error", "stream_error"))
            return queue

        relay_server.send_request_streaming = AsyncMock(
            side_effect=create_error_queue
        )

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        }

        # Create a proper aiohttp request mock
        from aiohttp.test_utils import make_mocked_request

        request = make_mocked_request(
            "POST",
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-test-key",
                "Content-Type": "application/json",
            },
        )
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        # Response should start successfully
        assert response.status == 200

    async def test_connector_disconnects_during_stream(self, api_setup):
        """Test connector disconnection during streaming."""
        api, relay_server, router = api_setup

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        relay_server._connectors = {"conn-1": MagicMock()}

        # Mock send_request_streaming to raise ConnectionError
        relay_server.send_request_streaming = AsyncMock(
            side_effect=ConnectionError("Connector disconnected")
        )

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {"authorization": "Bearer sk-test-key"}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        response = await api._handle_chat_completions(request)

        # Should still return streaming response (error written to stream)
        assert response.status == 200


class TestLLMClientErrors:
    """Tests for LLMClient error handling."""

    async def test_connection_refused(self):
        """Test handling of connection refused error."""
        client = LLMClient(base_url="http://localhost:9999")  # Invalid port

        with pytest.raises(aiohttp.ClientError):
            await client.forward_request(
                method="POST",
                path="/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                body=b'{"model": "test"}',
            )

        await client.close()

    async def test_invalid_url(self):
        """Test handling of invalid URL."""
        client = LLMClient(base_url="not-a-valid-url")

        with pytest.raises(aiohttp.ClientError):
            await client.forward_request(
                method="POST",
                path="/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                body=b'{"model": "test"}',
            )

        await client.close()

    async def test_timeout_in_llm_client(self):
        """Test timeout handling in LLMClient."""
        # Create client with very short timeout
        client = LLMClient(base_url="http://localhost:11434", timeout=0.001)

        # This should timeout (assuming no server at localhost:11434)
        with pytest.raises((asyncio.TimeoutError, aiohttp.ClientError)):
            await client.forward_request(
                method="POST",
                path="/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                body=b'{"model": "test"}',
            )

        await client.close()


class TestRequestSizeAndFormat:
    """Tests for request size and format validation."""

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

    async def test_very_large_request_body(self, api_setup):
        """Test handling of very large request body."""
        api, relay_server, router = api_setup

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        relay_server._connectors = {"conn-1": MagicMock()}

        # Mock response
        response_msg = create_response_message(
            correlation_id="req-123",
            status=200,
            headers={"Content-Type": "application/json"},
            body=base64.b64encode(b'{"result": "ok"}').decode(),
        )
        relay_server.send_request = AsyncMock(return_value=response_msg)

        # Create large request (10MB of content)
        large_content = "x" * (10 * 1024 * 1024)
        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": large_content}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "application/json"
        request.headers = {"authorization": "Bearer sk-test-key"}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        # Should handle large request (no size limit at broker level)
        response = await api._handle_chat_completions(request)

        # The broker should forward it successfully
        assert response.status == 200

    async def test_invalid_content_type(self, api_setup):
        """Test request with invalid content-type header."""
        api, relay_server, router = api_setup

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        relay_server._connectors = {"conn-1": MagicMock()}

        # Mock response
        response_msg = create_response_message(
            correlation_id="req-123",
            status=200,
            headers={"Content-Type": "application/json"},
            body=base64.b64encode(b'{"result": "ok"}').decode(),
        )
        relay_server.send_request = AsyncMock(return_value=response_msg)

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        request = MagicMock()
        request.method = "POST"
        request.path = "/v1/chat/completions"
        request.content_type = "text/plain"  # Wrong content type
        request.headers = {"authorization": "Bearer sk-test-key"}
        request.read = AsyncMock(return_value=json.dumps(request_body).encode())

        # Broker forwards regardless of content-type (validation at LLM)
        response = await api._handle_chat_completions(request)

        # Should still forward the request
        assert response.status == 200


class TestErrorResponseFormat:
    """Tests for error response formatting."""

    def test_create_error_response_basic(self):
        """Test basic error response creation."""
        response = create_error_response(
            status=400,
            message="Bad request",
            error_type="invalid_request_error",
        )

        assert response.status == 400
        body = json.loads(response.body)
        assert body["error"]["message"] == "Bad request"
        assert body["error"]["type"] == "invalid_request_error"
        assert body["error"]["code"] is None

    def test_create_error_response_with_code(self):
        """Test error response with error code."""
        response = create_error_response(
            status=401,
            message="Invalid API key",
            error_type="authentication_error",
            code="invalid_api_key",
        )

        assert response.status == 401
        body = json.loads(response.body)
        assert body["error"]["message"] == "Invalid API key"
        assert body["error"]["type"] == "authentication_error"
        assert body["error"]["code"] == "invalid_api_key"

    def test_error_response_json_serializable(self):
        """Test that error responses are properly JSON serializable."""
        response = create_error_response(
            status=500,
            message="Internal server error with unicode: \u4e16\u754c",
            error_type="internal_error",
            code="unicode_test",
        )

        assert response.status == 500
        body = json.loads(response.body)
        assert "\u4e16\u754c" in body["error"]["message"]


class TestConcurrentErrorScenarios:
    """Tests for error handling under concurrent load."""

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

    async def test_concurrent_requests_with_errors(self, api_setup):
        """Test handling of multiple concurrent requests with errors."""
        api, relay_server, router = api_setup

        # Register connector
        router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )
        relay_server._connectors = {"conn-1": MagicMock()}

        # Mock some requests to succeed, some to fail
        call_count = [0]

        async def mixed_responses(*_args, **_kwargs):
            call_count[0] += 1
            if call_count[0] % 3 == 0:
                raise ConnectionError("Connector error")
            elif call_count[0] % 3 == 1:
                return create_error_message("req", 500, "Error", "error")
            else:
                return create_response_message(
                    "req", 200, {"Content-Type": "application/json"},
                    base64.b64encode(b'{"ok": true}').decode()
                )

        relay_server.send_request = AsyncMock(side_effect=mixed_responses)

        request_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        async def make_request():
            request = MagicMock()
            request.method = "POST"
            request.path = "/v1/chat/completions"
            request.content_type = "application/json"
            request.headers = {"authorization": "Bearer sk-test-key"}
            request.read = AsyncMock(return_value=json.dumps(request_body).encode())
            return await api._handle_chat_completions(request)

        # Make 10 concurrent requests
        responses = await asyncio.gather(*[make_request() for _ in range(10)])

        # All should return valid responses (some errors, some success)
        assert len(responses) == 10
        success_count = sum(1 for r in responses if r.status == 200)
        error_count = sum(1 for r in responses if r.status in (500, 502))

        # Should have a mix of successes and errors
        assert success_count > 0
        assert error_count > 0
        assert success_count + error_count == 10
