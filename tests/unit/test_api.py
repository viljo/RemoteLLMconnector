"""Unit tests for the broker API module."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from remotellm.broker.api import BrokerAPI, create_error_response
from remotellm.broker.router import ModelRouter
from remotellm.shared.protocol import (
    MessageType,
    RelayMessage,
    create_error_message,
    create_response_message,
    create_stream_chunk_message,
    create_stream_end_message,
)


class TestCreateErrorResponse:
    """Tests for create_error_response helper."""

    def test_create_error_response_basic(self):
        """Test creating basic error response."""
        response = create_error_response(
            status=400,
            message="Bad request",
            error_type="invalid_request_error",
        )

        assert response.status == 400
        assert response.content_type == "application/json"

    def test_create_error_response_with_code(self):
        """Test creating error response with code."""
        response = create_error_response(
            status=404,
            message="Model not found",
            error_type="invalid_request_error",
            code="model_not_found",
        )

        assert response.status == 404


class TestBrokerAPIInit:
    """Tests for BrokerAPI initialization."""

    def test_basic_init(self):
        """Test basic initialization."""
        relay_server = MagicMock()
        router = ModelRouter()

        api = BrokerAPI(
            relay_server=relay_server,
            router=router,
        )

        assert api.relay_server is relay_server
        assert api.router is router
        assert api.user_api_keys == []
        assert api.request_timeout == 300.0

    def test_init_with_api_keys(self):
        """Test initialization with API keys."""
        relay_server = MagicMock()
        router = ModelRouter()

        api = BrokerAPI(
            relay_server=relay_server,
            router=router,
            user_api_keys=["sk-key-1", "sk-key-2"],
            request_timeout=60.0,
        )

        assert api.user_api_keys == ["sk-key-1", "sk-key-2"]
        assert api.request_timeout == 60.0

    def test_app_property(self):
        """Test app property returns aiohttp app."""
        relay_server = MagicMock()
        router = ModelRouter()

        api = BrokerAPI(relay_server=relay_server, router=router)

        assert isinstance(api.app, web.Application)


class TestBrokerAPIValidation:
    """Tests for API key validation."""

    def test_validate_no_keys_configured(self):
        """Test validation passes when no keys configured."""
        relay_server = MagicMock()
        router = ModelRouter()

        api = BrokerAPI(
            relay_server=relay_server,
            router=router,
            user_api_keys=[],
        )

        request = MagicMock()
        request.headers = {}

        result = api._validate_user_api_key(request)
        assert result is None  # No error

    def test_validate_missing_header(self):
        """Test validation fails with missing header."""
        relay_server = MagicMock()
        router = ModelRouter()

        api = BrokerAPI(
            relay_server=relay_server,
            router=router,
            user_api_keys=["sk-valid-key"],
        )

        request = MagicMock()
        request.headers = {}

        result = api._validate_user_api_key(request)
        assert result == "Missing or invalid Authorization header"

    def test_validate_invalid_header_format(self):
        """Test validation fails with invalid header format."""
        relay_server = MagicMock()
        router = ModelRouter()

        api = BrokerAPI(
            relay_server=relay_server,
            router=router,
            user_api_keys=["sk-valid-key"],
        )

        request = MagicMock()
        request.headers = {"authorization": "Basic abc123"}

        result = api._validate_user_api_key(request)
        assert result == "Missing or invalid Authorization header"

    def test_validate_invalid_key(self):
        """Test validation fails with invalid key."""
        relay_server = MagicMock()
        router = ModelRouter()

        api = BrokerAPI(
            relay_server=relay_server,
            router=router,
            user_api_keys=["sk-valid-key"],
        )

        request = MagicMock()
        request.headers = {"authorization": "Bearer sk-wrong-key"}

        result = api._validate_user_api_key(request)
        assert result == "Invalid API key"

    def test_validate_valid_key(self):
        """Test validation passes with valid key."""
        relay_server = MagicMock()
        router = ModelRouter()

        api = BrokerAPI(
            relay_server=relay_server,
            router=router,
            user_api_keys=["sk-valid-key"],
        )

        request = MagicMock()
        request.headers = {"authorization": "Bearer sk-valid-key"}

        result = api._validate_user_api_key(request)
        assert result is None  # No error


class TestBrokerAPIChatCompletions(AioHTTPTestCase):
    """Integration tests for chat completions endpoint."""

    async def get_application(self):
        """Create the test application."""
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

    @unittest_run_loop
    async def test_missing_auth(self):
        """Test request without authentication."""
        response = await self.client.request(
            "POST",
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": []},
        )

        assert response.status == 401
        data = await response.json()
        assert "authentication_error" in str(data)

    @unittest_run_loop
    async def test_invalid_auth(self):
        """Test request with invalid authentication."""
        response = await self.client.request(
            "POST",
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer wrong-key"},
        )

        assert response.status == 401

    @unittest_run_loop
    async def test_missing_model(self):
        """Test request without model field."""
        response = await self.client.request(
            "POST",
            "/v1/chat/completions",
            json={"messages": []},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status == 400
        data = await response.json()
        assert data["error"]["code"] == "missing_model"

    @unittest_run_loop
    async def test_model_not_found(self):
        """Test request for unknown model."""
        response = await self.client.request(
            "POST",
            "/v1/chat/completions",
            json={"model": "unknown-model", "messages": []},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status == 404
        data = await response.json()
        assert data["error"]["code"] == "model_not_found"

    @unittest_run_loop
    async def test_connector_unavailable(self):
        """Test request when connector disconnected."""
        # Register model but don't add connector to _connectors
        self.router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )

        response = await self.client.request(
            "POST",
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status == 503
        data = await response.json()
        assert data["error"]["code"] == "connector_unavailable"

    @unittest_run_loop
    async def test_successful_request(self):
        """Test successful chat completion request."""
        # Register model and connector
        self.router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key="sk-llm-key",
        )

        # Mock connector
        mock_connector = MagicMock()
        self.relay_server._connectors = {"conn-1": mock_connector}

        # Mock response
        response_body = json.dumps(
            {
                "id": "chatcmpl-123",
                "choices": [{"message": {"content": "Hello!"}}],
            }
        ).encode()
        mock_response = create_response_message(
            correlation_id="req-123",
            status=200,
            headers={"content-type": "application/json"},
            body=base64.b64encode(response_body).decode(),
        )

        self.relay_server.send_request = AsyncMock(return_value=mock_response)

        response = await self.client.request(
            "POST",
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status == 200
        data = await response.json()
        assert data["id"] == "chatcmpl-123"

    @unittest_run_loop
    async def test_request_timeout(self):
        """Test request timeout handling."""
        self.router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key=None,
        )

        mock_connector = MagicMock()
        self.relay_server._connectors = {"conn-1": mock_connector}

        # Make send_request timeout
        self.relay_server.send_request = AsyncMock(side_effect=TimeoutError())

        response = await self.client.request(
            "POST",
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status == 504
        data = await response.json()
        assert data["error"]["code"] == "timeout"

    @unittest_run_loop
    async def test_connector_disconnected_error(self):
        """Test handling when connector disconnects during request."""
        self.router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key=None,
        )

        mock_connector = MagicMock()
        self.relay_server._connectors = {"conn-1": mock_connector}

        # Make send_request raise ConnectionError
        self.relay_server.send_request = AsyncMock(
            side_effect=ConnectionError("Connector disconnected")
        )

        response = await self.client.request(
            "POST",
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status == 502
        data = await response.json()
        assert data["error"]["code"] == "connector_unavailable"

    @unittest_run_loop
    async def test_error_response_from_connector(self):
        """Test handling error response from connector."""
        self.router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key=None,
        )

        mock_connector = MagicMock()
        self.relay_server._connectors = {"conn-1": mock_connector}

        # Return error message
        error_response = create_error_message(
            correlation_id="req-123",
            status=500,
            error="LLM server error",
            code="llm_error",
        )
        self.relay_server.send_request = AsyncMock(return_value=error_response)

        response = await self.client.request(
            "POST",
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status == 500


class TestBrokerAPIModels(AioHTTPTestCase):
    """Integration tests for models endpoint."""

    async def get_application(self):
        """Create the test application."""
        self.relay_server = MagicMock()
        self.router = ModelRouter()

        self.api = BrokerAPI(
            relay_server=self.relay_server,
            router=self.router,
            user_api_keys=["sk-test-key"],
        )

        return self.api.app

    @unittest_run_loop
    async def test_models_missing_auth(self):
        """Test models request without authentication."""
        response = await self.client.request("GET", "/v1/models")

        assert response.status == 401

    @unittest_run_loop
    async def test_models_empty(self):
        """Test models request when no models available."""
        response = await self.client.request(
            "GET",
            "/v1/models",
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status == 200
        data = await response.json()
        assert data["object"] == "list"
        assert data["data"] == []

    @unittest_run_loop
    async def test_models_with_connectors(self):
        """Test models request with registered connectors."""
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

        response = await self.client.request(
            "GET",
            "/v1/models",
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status == 200
        data = await response.json()
        assert data["object"] == "list"
        assert len(data["data"]) == 3

        model_ids = [m["id"] for m in data["data"]]
        assert "gpt-4" in model_ids
        assert "gpt-3.5-turbo" in model_ids
        assert "llama3" in model_ids

    @unittest_run_loop
    async def test_models_response_format(self):
        """Test that models response has correct OpenAI format."""
        self.router.on_connector_registered(
            connector_id="conn-1",
            models=["test-model"],
            llm_api_key=None,
        )

        response = await self.client.request(
            "GET",
            "/v1/models",
            headers={"Authorization": "Bearer sk-test-key"},
        )

        data = await response.json()
        model = data["data"][0]

        assert "id" in model
        assert model["object"] == "model"
        assert "created" in model
        assert model["owned_by"] == "remotellm"


class TestBrokerAPIStreaming(AioHTTPTestCase):
    """Tests for streaming responses."""

    async def get_application(self):
        """Create the test application."""
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

    @unittest_run_loop
    async def test_streaming_request(self):
        """Test streaming chat completion request."""
        self.router.on_connector_registered(
            connector_id="conn-1",
            models=["gpt-4"],
            llm_api_key=None,
        )

        mock_connector = MagicMock()
        self.relay_server._connectors = {"conn-1": mock_connector}

        # Create async queue with chunks
        queue = asyncio.Queue()
        await queue.put(
            create_stream_chunk_message("req-1", 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n')
        )
        await queue.put(create_stream_end_message("req-1"))

        self.relay_server.send_request_streaming = AsyncMock(return_value=queue)

        response = await self.client.request(
            "POST",
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": [], "stream": True},
            headers={"Authorization": "Bearer sk-test-key"},
        )

        assert response.status == 200
        assert response.content_type == "text/event-stream"

        # Read streaming response
        content = await response.content.read()
        assert b"Hi" in content
