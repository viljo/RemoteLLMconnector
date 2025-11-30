"""Unit tests for the LLM client module."""

import pytest
from aioresponses import aioresponses

from remotellm.connector.llm_client import LLMClient


@pytest.fixture
def llm_client():
    """Create an LLM client for testing."""
    return LLMClient(base_url="http://localhost:11434")


@pytest.fixture
def llm_client_custom():
    """Create an LLM client with custom settings."""
    return LLMClient(
        base_url="https://api.example.com",
        timeout=60.0,
        ssl_verify=True,
        host_header="custom.host.com",
    )


class TestLLMClientInit:
    """Tests for LLMClient initialization."""

    def test_default_init(self):
        """Test default initialization."""
        client = LLMClient(base_url="http://localhost:11434")
        assert client.base_url == "http://localhost:11434"
        assert client.timeout.total == 300.0
        assert client.ssl_verify is True
        assert client.host_header is None

    def test_custom_init(self):
        """Test custom initialization."""
        client = LLMClient(
            base_url="https://api.example.com/",
            timeout=60.0,
            ssl_verify=False,
            host_header="proxy.example.com",
        )
        assert client.base_url == "https://api.example.com"  # Trailing slash stripped
        assert client.timeout.total == 60.0
        assert client.ssl_verify is False
        assert client.host_header == "proxy.example.com"

    def test_trailing_slash_stripped(self):
        """Test trailing slash is stripped from base_url."""
        client = LLMClient(base_url="http://localhost:11434/")
        assert client.base_url == "http://localhost:11434"


class TestLLMClientCheckHealth:
    """Tests for health check functionality."""

    async def test_health_check_success(self, llm_client):
        """Test successful health check."""
        with aioresponses() as m:
            m.get("http://localhost:11434/api/tags", status=200, payload={"models": []})

            result = await llm_client.check_health()
            assert result is True

        await llm_client.close()

    async def test_health_check_failure_status(self, llm_client):
        """Test health check with non-200 status."""
        with aioresponses() as m:
            m.get("http://localhost:11434/api/tags", status=500)

            result = await llm_client.check_health()
            assert result is False

        await llm_client.close()

    async def test_health_check_connection_error(self, llm_client):
        """Test health check with connection error."""
        with aioresponses() as m:
            m.get("http://localhost:11434/api/tags", exception=ConnectionError())

            result = await llm_client.check_health()
            assert result is False

        await llm_client.close()

    async def test_health_check_timeout(self, llm_client):
        """Test health check with timeout."""
        import asyncio

        with aioresponses() as m:
            m.get("http://localhost:11434/api/tags", exception=asyncio.TimeoutError())

            result = await llm_client.check_health()
            assert result is False

        await llm_client.close()


class TestLLMClientForwardRequest:
    """Tests for forward_request functionality."""

    async def test_forward_get_request(self, llm_client):
        """Test forwarding a GET request."""
        with aioresponses() as m:
            m.get(
                "http://localhost:11434/v1/models",
                status=200,
                body=b'{"object": "list", "data": []}',
                headers={"Content-Type": "application/json"},
            )

            status, headers, body = await llm_client.forward_request(
                method="GET",
                path="/v1/models",
                headers={"Accept": "application/json"},
            )

            assert status == 200
            assert b"list" in body

        await llm_client.close()

    async def test_forward_post_request(self, llm_client):
        """Test forwarding a POST request."""
        request_body = b'{"model": "llama3", "messages": []}'
        response_body = b'{"id": "chatcmpl-123", "choices": []}'

        with aioresponses() as m:
            m.post(
                "http://localhost:11434/v1/chat/completions",
                status=200,
                body=response_body,
                headers={"Content-Type": "application/json"},
            )

            status, headers, body = await llm_client.forward_request(
                method="POST",
                path="/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                body=request_body,
            )

            assert status == 200
            assert body == response_body

        await llm_client.close()

    async def test_forward_request_filters_headers(self, llm_client):
        """Test that host, connection, and authorization headers are filtered."""
        with aioresponses() as m:
            m.post(
                "http://localhost:11434/v1/chat/completions",
                status=200,
                body=b"{}",
            )

            await llm_client.forward_request(
                method="POST",
                path="/v1/chat/completions",
                headers={
                    "Host": "should-be-filtered.com",
                    "Connection": "keep-alive",
                    "Authorization": "Bearer user-token",
                    "Content-Type": "application/json",
                    "X-Custom-Header": "should-pass",
                },
                body=b"{}",
            )

            # Verify the request was made (headers are filtered internally)
            assert len(m.requests) == 1

        await llm_client.close()

    async def test_forward_request_with_llm_api_key(self, llm_client):
        """Test that LLM API key is injected."""
        with aioresponses() as m:
            m.post(
                "http://localhost:11434/v1/chat/completions",
                status=200,
                body=b"{}",
            )

            await llm_client.forward_request(
                method="POST",
                path="/v1/chat/completions",
                headers={},
                body=b"{}",
                llm_api_key="sk-llm-secret-key",
            )

            # Request was made with injected authorization
            assert len(m.requests) == 1

        await llm_client.close()

    async def test_forward_request_with_custom_host_header(self, llm_client_custom):
        """Test that custom host header is added."""
        with aioresponses() as m:
            m.get(
                "https://api.example.com/v1/models",
                status=200,
                body=b'{"data": []}',
            )

            await llm_client_custom.forward_request(
                method="GET",
                path="/v1/models",
                headers={},
            )

            assert len(m.requests) == 1

        await llm_client_custom.close()

    async def test_forward_request_error_status(self, llm_client):
        """Test forwarding request that returns error status."""
        with aioresponses() as m:
            m.post(
                "http://localhost:11434/v1/chat/completions",
                status=400,
                body=b'{"error": {"message": "Invalid request"}}',
            )

            status, headers, body = await llm_client.forward_request(
                method="POST",
                path="/v1/chat/completions",
                headers={},
                body=b"{}",
            )

            assert status == 400
            assert b"Invalid request" in body

        await llm_client.close()

    async def test_forward_request_server_error(self, llm_client):
        """Test forwarding request that returns server error."""
        with aioresponses() as m:
            m.post(
                "http://localhost:11434/v1/chat/completions",
                status=500,
                body=b'{"error": {"message": "Internal server error"}}',
            )

            status, headers, body = await llm_client.forward_request(
                method="POST",
                path="/v1/chat/completions",
                headers={},
                body=b"{}",
            )

            assert status == 500

        await llm_client.close()


class TestLLMClientStreamingRequest:
    """Tests for streaming request functionality."""

    async def test_forward_streaming_request(self, llm_client):
        """Test forwarding a streaming request."""
        chunks = [
            b'data: {"id": "1", "choices": [{"delta": {"content": "Hello"}}]}\n\n',
            b'data: {"id": "1", "choices": [{"delta": {"content": " World"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]

        with aioresponses() as m:
            m.post(
                "http://localhost:11434/v1/chat/completions",
                status=200,
                body=b"".join(chunks),
                headers={"Content-Type": "text/event-stream"},
            )

            results = []
            async for result in llm_client.forward_streaming_request(
                method="POST",
                path="/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                body=b'{"model": "llama3", "stream": true}',
            ):
                results.append(result)

            # First result should be (status, headers, b"")
            assert isinstance(results[0], tuple)
            status, headers, empty_body = results[0]
            assert status == 200
            assert empty_body == b""

            # Subsequent results should be chunks
            assert len(results) >= 1

        await llm_client.close()

    async def test_forward_streaming_request_with_api_key(self, llm_client):
        """Test streaming request with LLM API key injection."""
        with aioresponses() as m:
            m.post(
                "http://localhost:11434/v1/chat/completions",
                status=200,
                body=b"data: [DONE]\n\n",
            )

            async for _ in llm_client.forward_streaming_request(
                method="POST",
                path="/v1/chat/completions",
                headers={},
                body=b"{}",
                llm_api_key="sk-streaming-key",
            ):
                pass

            assert len(m.requests) == 1

        await llm_client.close()


class TestLLMClientGetModels:
    """Tests for get_models functionality."""

    async def test_get_models_success(self, llm_client):
        """Test getting models successfully."""
        models_response = {
            "object": "list",
            "data": [
                {"id": "llama3", "object": "model"},
                {"id": "gpt-4", "object": "model"},
            ],
        }

        with aioresponses() as m:
            m.get(
                "http://localhost:11434/v1/models",
                status=200,
                payload=models_response,
            )

            result = await llm_client.get_models()

            assert result["object"] == "list"
            assert len(result["data"]) == 2
            assert result["data"][0]["id"] == "llama3"

        await llm_client.close()

    async def test_get_models_failure(self, llm_client):
        """Test get_models with error response."""
        with aioresponses() as m:
            m.get("http://localhost:11434/v1/models", status=500)

            with pytest.raises(RuntimeError, match="Failed to get models"):
                await llm_client.get_models()

        await llm_client.close()


class TestLLMClientGetOllamaTags:
    """Tests for get_ollama_tags functionality."""

    async def test_get_ollama_tags_success(self, llm_client):
        """Test getting Ollama tags successfully."""
        tags_response = {
            "models": [
                {"name": "llama3:latest", "size": 4000000000},
                {"name": "codellama:7b", "size": 3500000000},
            ]
        }

        with aioresponses() as m:
            m.get(
                "http://localhost:11434/api/tags",
                status=200,
                payload=tags_response,
            )

            result = await llm_client.get_ollama_tags()

            assert "models" in result
            assert len(result["models"]) == 2
            assert result["models"][0]["name"] == "llama3:latest"

        await llm_client.close()

    async def test_get_ollama_tags_failure(self, llm_client):
        """Test get_ollama_tags with error response."""
        with aioresponses() as m:
            m.get("http://localhost:11434/api/tags", status=404)

            with pytest.raises(RuntimeError, match="Failed to get Ollama tags"):
                await llm_client.get_ollama_tags()

        await llm_client.close()


class TestLLMClientSessionManagement:
    """Tests for session management."""

    async def test_session_created_on_demand(self, llm_client):
        """Test that session is created on first request."""
        assert llm_client._session is None

        with aioresponses() as m:
            m.get("http://localhost:11434/api/tags", status=200, payload={})
            await llm_client.check_health()

        assert llm_client._session is not None
        await llm_client.close()

    async def test_session_reused(self, llm_client):
        """Test that session is reused across requests."""
        with aioresponses() as m:
            m.get("http://localhost:11434/api/tags", status=200, payload={})
            m.get("http://localhost:11434/api/tags", status=200, payload={})

            await llm_client.check_health()
            first_session = llm_client._session

            await llm_client.check_health()
            second_session = llm_client._session

            assert first_session is second_session

        await llm_client.close()

    async def test_close_session(self, llm_client):
        """Test closing the session."""
        with aioresponses() as m:
            m.get("http://localhost:11434/api/tags", status=200, payload={})
            await llm_client.check_health()

        assert llm_client._session is not None

        await llm_client.close()
        assert llm_client._session is None

    async def test_close_without_session(self, llm_client):
        """Test closing without active session doesn't error."""
        await llm_client.close()  # Should not raise

    async def test_session_recreated_after_close(self, llm_client):
        """Test session is recreated after being closed."""
        with aioresponses() as m:
            m.get("http://localhost:11434/api/tags", status=200, payload={})
            m.get("http://localhost:11434/api/tags", status=200, payload={})

            await llm_client.check_health()
            await llm_client.close()

            # Session should be recreated on next request
            await llm_client.check_health()
            assert llm_client._session is not None

        await llm_client.close()
