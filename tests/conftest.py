"""Shared pytest fixtures for RemoteLLMconnector tests."""

import asyncio
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from remotellm.broker.router import ModelRouter
from remotellm.shared.protocol import (
    RelayMessage,
    create_auth_ok_message,
    create_response_message,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_yaml_file(temp_dir):
    """Create a temporary YAML file."""

    def _create(filename: str, content: dict[str, Any]) -> Path:
        path = temp_dir / filename
        with open(path, "w") as f:
            yaml.dump(content, f)
        return path

    return _create


@pytest.fixture
def model_router():
    """Create a model router instance."""
    return ModelRouter()


@pytest.fixture
def populated_router(model_router):
    """Create a router with some connectors registered."""
    model_router.on_connector_registered(
        connector_id="conn-1",
        models=["gpt-4", "gpt-3.5-turbo"],
        llm_api_key="sk-key-1",
    )
    model_router.on_connector_registered(
        connector_id="conn-2",
        models=["llama3", "codellama"],
        llm_api_key="sk-key-2",
    )
    return model_router


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket connection."""
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock()
    ws.close = AsyncMock()
    ws.closed = False
    return ws


@pytest.fixture
def mock_websocket_with_auth(mock_websocket):
    """Create a mock WebSocket that responds with AUTH_OK."""
    auth_ok = create_auth_ok_message("auth-1", "session-123")
    mock_websocket.recv.return_value = auth_ok.model_dump_json()
    return mock_websocket


@pytest.fixture
def mock_relay_server():
    """Create a mock relay server."""
    server = MagicMock()
    server._connectors = {}
    server.send_request = AsyncMock()
    server.get_connector = MagicMock(return_value=None)
    server.is_connector_connected = MagicMock(return_value=False)
    return server


@pytest.fixture
def mock_connector_registration():
    """Create a mock connector registration."""
    from dataclasses import dataclass, field

    @dataclass
    class MockConnectorRegistration:
        connector_id: str = "conn-test"
        websocket: AsyncMock = field(default_factory=AsyncMock)
        models: list[str] = field(default_factory=lambda: ["test-model"])
        pending_requests: dict = field(default_factory=dict)

    return MockConnectorRegistration()


@pytest.fixture
def sample_chat_request():
    """Create a sample chat completion request."""
    return {
        "model": "gpt-4",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ],
        "temperature": 0.7,
        "max_tokens": 100,
    }


@pytest.fixture
def sample_chat_response():
    """Create a sample chat completion response."""
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello! How can I help you?"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }


@pytest.fixture
def sample_streaming_chunks():
    """Create sample streaming response chunks."""
    return [
        'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n',
        'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n',
        'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4","choices":[{"index":0,"delta":{"content":"!"},"finish_reason":null}]}\n\n',
        'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
        "data: [DONE]\n\n",
    ]


@pytest.fixture
def sample_model_list():
    """Create a sample model list response."""
    return {
        "object": "list",
        "data": [
            {"id": "gpt-4", "object": "model", "created": 1700000000, "owned_by": "openai"},
            {"id": "llama3", "object": "model", "created": 1700000000, "owned_by": "meta"},
        ],
    }


@pytest.fixture
def sample_error_response():
    """Create a sample error response."""
    return {
        "error": {
            "message": "Model not found",
            "type": "invalid_request_error",
            "code": "model_not_found",
        }
    }


# Event loop configuration for async tests
@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default event loop policy."""
    return asyncio.DefaultEventLoopPolicy()
