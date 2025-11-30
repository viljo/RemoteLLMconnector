"""HTTP client for communicating with the local LLM server."""

from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from remotellm.shared.logging import get_logger

logger = get_logger(__name__)


class LLMClient:
    """Async HTTP client for the local LLM server."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 300.0,
        ssl_verify: bool = True,
        host_header: str | None = None,
    ):
        """Initialize the LLM client.

        Args:
            base_url: Base URL of the local LLM server (e.g., http://localhost:11434)
            timeout: Request timeout in seconds
            ssl_verify: Whether to verify SSL certificates
            host_header: Custom Host header for reverse proxy setups
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.ssl_verify = ssl_verify
        self.host_header = host_header
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the HTTP session."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self.ssl_verify)
            self._session = aiohttp.ClientSession(timeout=self.timeout, connector=connector)
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def check_health(self) -> bool:
        """Check if the LLM server is reachable.

        Returns:
            True if server is reachable, False otherwise
        """
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.base_url}/api/tags", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                return resp.status == 200
        except Exception as e:
            logger.warning("LLM health check failed", error=str(e))
            return False

    async def forward_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None = None,
        llm_api_key: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Forward a request to the LLM server (non-streaming).

        Args:
            method: HTTP method
            path: Request path (e.g., /v1/chat/completions)
            headers: Request headers
            body: Request body
            llm_api_key: API key for the LLM server (injected by broker)

        Returns:
            Tuple of (status_code, response_headers, response_body)
        """
        session = await self._get_session()
        url = f"{self.base_url}{path}"

        # Filter headers that shouldn't be forwarded
        forward_headers = {
            k: v
            for k, v in headers.items()
            if k.lower() not in ("host", "connection", "authorization")
        }

        # Add custom Host header if configured (for reverse proxy setups)
        if self.host_header:
            forward_headers["Host"] = self.host_header

        # Add LLM API key if provided (from broker)
        if llm_api_key:
            forward_headers["Authorization"] = f"Bearer {llm_api_key}"

        logger.debug("Forwarding request to LLM", method=method, url=url)

        async with session.request(
            method=method,
            url=url,
            headers=forward_headers,
            data=body,
        ) as resp:
            response_body = await resp.read()
            response_headers = dict(resp.headers)
            return resp.status, response_headers, response_body

    async def forward_streaming_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None = None,
        llm_api_key: str | None = None,
    ) -> AsyncIterator[tuple[int, dict[str, str], bytes] | bytes]:
        """Forward a request to the LLM server with streaming response.

        Args:
            method: HTTP method
            path: Request path
            headers: Request headers
            body: Request body
            llm_api_key: API key for the LLM server (injected by broker)

        Yields:
            First yield: (status_code, response_headers, b"")
            Subsequent yields: Response body chunks
        """
        session = await self._get_session()
        url = f"{self.base_url}{path}"

        # Filter headers that shouldn't be forwarded
        forward_headers = {
            k: v
            for k, v in headers.items()
            if k.lower() not in ("host", "connection", "authorization")
        }

        # Add custom Host header if configured (for reverse proxy setups)
        if self.host_header:
            forward_headers["Host"] = self.host_header

        # Add LLM API key if provided (from broker)
        if llm_api_key:
            forward_headers["Authorization"] = f"Bearer {llm_api_key}"

        logger.debug("Forwarding streaming request to LLM", method=method, url=url)

        async with session.request(
            method=method,
            url=url,
            headers=forward_headers,
            data=body,
        ) as resp:
            # First yield the status and headers
            response_headers = dict(resp.headers)
            yield (resp.status, response_headers, b"")

            # Stream the response body chunks
            async for chunk in resp.content.iter_any():
                if chunk:
                    yield chunk

    async def get_models(self) -> dict[str, Any]:
        """Get list of available models from the LLM server.

        Returns:
            Dictionary with model list data
        """
        session = await self._get_session()

        # Try OpenAI-compatible endpoint first
        try:
            async with session.get(f"{self.base_url}/v1/models") as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass

        # Fall back to Ollama-style endpoint
        try:
            async with session.get(f"{self.base_url}/api/tags") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Convert Ollama format to OpenAI format
                    models = data.get("models", [])
                    return {
                        "object": "list",
                        "data": [
                            {
                                "id": m.get("name", m.get("model", "unknown")),
                                "object": "model",
                                "created": 0,
                                "owned_by": "local",
                            }
                            for m in models
                        ],
                    }
        except Exception as e:
            logger.error("Failed to get models", error=str(e))
            raise

        return {"object": "list", "data": []}
