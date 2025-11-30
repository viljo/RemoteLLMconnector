"""HTTP API server for external users."""

import asyncio
import base64
import json
import uuid
from typing import TYPE_CHECKING

from aiohttp import web

from remotellm.shared.logging import bind_correlation_id, clear_context, get_logger
from remotellm.shared.models import ErrorDetail, ErrorResponse
from remotellm.shared.protocol import (
    MessageType,
    RelayMessage,
    create_request_message,
)

if TYPE_CHECKING:
    from remotellm.broker.router import ModelRouter
    from remotellm.broker.relay_server import RelayServer

logger = get_logger(__name__)


def create_error_response(
    status: int, message: str, error_type: str, code: str | None = None
) -> web.Response:
    """Create a JSON error response.

    Args:
        status: HTTP status code
        message: Error message
        error_type: Error type
        code: Optional error code

    Returns:
        aiohttp Response with error JSON
    """
    error = ErrorResponse(error=ErrorDetail(message=message, type=error_type, code=code))
    return web.json_response(error.model_dump(), status=status)


class BrokerAPI:
    """HTTP API server for external users."""

    def __init__(
        self,
        relay_server: "RelayServer",
        router: "ModelRouter",
        user_api_keys: list[str] | None = None,
        request_timeout: float = 300.0,
    ):
        """Initialize the API server.

        Args:
            relay_server: The relay server for routing requests
            router: The model router for request routing
            user_api_keys: Valid API keys for user authentication (empty = no auth)
            request_timeout: Request timeout in seconds
        """
        self.relay_server = relay_server
        self.router = router
        self.user_api_keys = user_api_keys or []
        self.request_timeout = request_timeout
        self._app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Set up API routes."""
        self._app.router.add_post("/v1/chat/completions", self._handle_chat_completions)
        self._app.router.add_get("/v1/models", self._handle_models)

    @property
    def app(self) -> web.Application:
        """Get the aiohttp application."""
        return self._app

    def _validate_user_api_key(self, request: web.Request) -> str | None:
        """Validate user API key from Authorization header.

        Args:
            request: The incoming request

        Returns:
            Error message if validation fails, None if valid
        """
        if not self.user_api_keys:
            # No API keys configured = no auth required
            return None

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return "Missing or invalid Authorization header"

        token = auth_header[7:]  # Strip "Bearer "
        if token not in self.user_api_keys:
            return "Invalid API key"

        return None

    async def _handle_chat_completions(
        self, request: web.Request
    ) -> web.Response | web.StreamResponse:
        """Handle POST /v1/chat/completions endpoint.

        Args:
            request: The incoming request

        Returns:
            Response with completion result
        """
        correlation_id = f"req-{uuid.uuid4().hex[:12]}"
        bind_correlation_id(correlation_id)

        try:
            # Validate user API key (T018)
            auth_error = self._validate_user_api_key(request)
            if auth_error:
                logger.warning("Authentication failed", error=auth_error)
                return create_error_response(
                    status=401,
                    message=auth_error,
                    error_type="authentication_error",
                    code="invalid_api_key",
                )

            # Read request body
            body = await request.read()

            # Parse request to extract model and streaming flag (T019)
            is_streaming = False
            model: str | None = None
            try:
                request_json = json.loads(body)
                is_streaming = request_json.get("stream", False)
                model = request_json.get("model")
            except Exception:
                pass

            if not model:
                return create_error_response(
                    status=400,
                    message="Missing 'model' field in request",
                    error_type="invalid_request_error",
                    code="missing_model",
                )

            # Route request to connector by model (T020)
            route = self.router.get_route(model)
            if not route:
                # Check if model exists but connector disconnected (T023, T024)
                logger.warning("No route for model", model=model)
                return create_error_response(
                    status=404,
                    message=f"Model '{model}' not found",
                    error_type="invalid_request_error",
                    code="model_not_found",
                )

            connector_id, llm_api_key = route

            # Verify connector is still connected
            connector = self.relay_server._connectors.get(connector_id)
            if not connector:
                logger.error("Connector disconnected", connector_id=connector_id, model=model)
                return create_error_response(
                    status=503,
                    message=f"No active connector for model '{model}'",
                    error_type="service_unavailable",
                    code="connector_unavailable",
                )

            # Build headers to forward (strip user auth, connector uses llm_api_key)
            headers = {
                "content-type": request.content_type or "application/json",
            }

            # Encode body
            encoded_body = base64.b64encode(body).decode("ascii")

            # Create request message with injected llm_api_key (T021)
            relay_msg = create_request_message(
                correlation_id=correlation_id,
                method=request.method,
                path=request.path,
                headers=headers,
                body=encoded_body,
                llm_api_key=llm_api_key,
            )

            logger.info(
                "Forwarding request",
                connector_id=connector_id,
                model=model,
                streaming=is_streaming,
                has_llm_api_key=llm_api_key is not None,
            )

            if is_streaming:
                return await self._handle_streaming_response(connector_id, relay_msg, request)
            else:
                return await self._handle_non_streaming_response(connector_id, relay_msg)

        except Exception as e:
            logger.error("Request handling error", error=str(e))
            return create_error_response(
                status=500,
                message="Internal server error",
                error_type="internal_error",
            )
        finally:
            clear_context()

    async def _handle_non_streaming_response(
        self,
        connector_id: str,
        message: RelayMessage,
    ) -> web.Response:
        """Handle a non-streaming response.

        Args:
            connector_id: ID of the connector
            message: The request message

        Returns:
            HTTP response
        """
        try:
            response = await self.relay_server.send_request(
                connector_id=connector_id,
                message=message,
                timeout=self.request_timeout,
            )

            if response.type == MessageType.ERROR:
                status = response.payload.get("status", 500)
                error_msg = response.payload.get("error", "Unknown error")
                code = response.payload.get("code", "unknown")
                return create_error_response(status, error_msg, code, code)

            # Decode response
            status = response.payload.get("status", 200)
            headers = response.payload.get("headers", {})
            body = base64.b64decode(response.payload.get("body", ""))

            # Build response
            resp = web.Response(
                status=status,
                body=body,
                content_type=headers.get("content-type", "application/json"),
            )

            # Copy relevant headers
            for key in ("x-request-id",):
                if key in headers:
                    resp.headers[key] = headers[key]

            logger.info("Sent response", status=status)
            return resp

        except TimeoutError:
            logger.error("Request timeout")
            return create_error_response(
                status=504,
                message="Request timeout",
                error_type="timeout",
                code="timeout",
            )
        except ConnectionError as e:
            logger.error("Connector disconnected", error=str(e))
            return create_error_response(
                status=502,
                message="Connector unavailable",
                error_type="bad_gateway",
                code="connector_unavailable",
            )

    async def _handle_streaming_response(
        self,
        connector_id: str,
        message: RelayMessage,
        request: web.Request,
    ) -> web.StreamResponse:
        """Handle a streaming response.

        Args:
            connector_id: ID of the connector
            message: The request message
            request: The original HTTP request

        Returns:
            Streaming HTTP response
        """
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

        try:
            # Get streaming queue
            queue = await self.relay_server.send_request_streaming(
                connector_id=connector_id,
                message=message,
            )

            await response.prepare(request)

            # Stream chunks
            while True:
                try:
                    chunk_msg = await asyncio.wait_for(queue.get(), timeout=self.request_timeout)

                    if chunk_msg is None:
                        # End of stream
                        break

                    if chunk_msg.type == MessageType.ERROR:
                        # Send error as SSE event
                        error_data = f"data: {chunk_msg.model_dump_json()}\n\n"
                        await response.write(error_data.encode())
                        break

                    if chunk_msg.type == MessageType.STREAM_CHUNK:
                        chunk = chunk_msg.payload.get("chunk", "")
                        if chunk:
                            await response.write(chunk.encode())

                    if chunk_msg.type == MessageType.STREAM_END:
                        break

                except TimeoutError:
                    logger.error("Streaming timeout")
                    break

            logger.info("Streaming response complete")
            return response

        except ConnectionError as e:
            logger.error("Connector disconnected during streaming", error=str(e))
            # Try to write error before closing
            try:
                error_event = 'data: {"error": "Connector disconnected"}\n\n'
                await response.write(error_event.encode())
            except Exception:
                pass
            return response

    async def _handle_models(self, request: web.Request) -> web.Response:
        """Handle GET /v1/models endpoint.

        Returns aggregated list of models from all connected connectors (T022).

        Args:
            request: The incoming request

        Returns:
            Response with model list
        """
        correlation_id = f"req-{uuid.uuid4().hex[:12]}"
        bind_correlation_id(correlation_id)

        try:
            # Validate user API key
            auth_error = self._validate_user_api_key(request)
            if auth_error:
                logger.warning("Authentication failed", error=auth_error)
                return create_error_response(
                    status=401,
                    message=auth_error,
                    error_type="authentication_error",
                    code="invalid_api_key",
                )

            # Get all available models from router
            available_models = self.router.available_models

            if not available_models:
                logger.info("No models available")
                # Return empty list, not an error
                return web.json_response(
                    {
                        "object": "list",
                        "data": [],
                    }
                )

            # Build OpenAI-compatible models response
            import time

            models_data = []
            for model in available_models:
                models_data.append(
                    {
                        "id": model,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "remotellm",
                    }
                )

            logger.info("Returning models", count=len(models_data))

            return web.json_response(
                {
                    "object": "list",
                    "data": models_data,
                }
            )

        except Exception as e:
            logger.error("Models request error", error=str(e))
            return create_error_response(
                status=500,
                message="Internal server error",
                error_type="internal_error",
            )
        finally:
            clear_context()
