"""Main entry point for the connector component."""

import asyncio
import base64
import json
import signal

from remotellm.connector.config import ConnectorConfig
from remotellm.connector.health import HealthServer
from remotellm.connector.llm_client import LLMClient
from remotellm.connector.tunnel_client import TunnelClient
from remotellm.shared.logging import (
    bind_correlation_id,
    clear_context,
    configure_logging,
    get_logger,
)
from remotellm.shared.protocol import (
    RequestPayload,
    TunnelMessage,
    create_error_message,
    create_response_message,
    create_stream_chunk_message,
    create_stream_end_message,
)

logger = get_logger(__name__)


class Connector:
    """Main connector application."""

    def __init__(self, config: ConnectorConfig):
        """Initialize the connector.

        Args:
            config: Connector configuration
        """
        self.config = config
        self.llm_client = LLMClient(
            config.llm_url,
            timeout=config.llm_timeout,
            ssl_verify=config.llm_ssl_verify,
            host_header=config.llm_host,
        )
        self.tunnel_client: TunnelClient | None = None
        self.health_server: HealthServer | None = None
        self._shutdown_event = asyncio.Event()
        self._in_flight_requests: set[str] = set()

    async def _handle_request(self, message: TunnelMessage) -> None:
        """Handle an incoming request from the tunnel.

        Args:
            message: The request message
        """
        correlation_id = message.id
        bind_correlation_id(correlation_id)
        self._in_flight_requests.add(correlation_id)

        try:
            payload = RequestPayload.model_validate(message.payload)

            # Use broker's API key if provided, otherwise use connector's configured key
            llm_api_key = payload.llm_api_key or self.config.llm_api_key

            logger.info(
                "Received request",
                method=payload.method,
                path=payload.path,
                has_llm_api_key=llm_api_key is not None,
            )

            # Decode body if present
            body = base64.b64decode(payload.body) if payload.body else None

            # Check if streaming is requested
            is_streaming = False
            if body:
                try:
                    request_json = json.loads(body)
                    is_streaming = request_json.get("stream", False)
                except json.JSONDecodeError:
                    pass

            if is_streaming:
                await self._handle_streaming_request(correlation_id, payload, body, llm_api_key)
            else:
                await self._handle_non_streaming_request(correlation_id, payload, body, llm_api_key)

        except Exception as e:
            logger.error("Request handling error", error=str(e))
            error_msg = create_error_message(
                correlation_id=correlation_id,
                status=500,
                error=str(e),
                code="internal_error",
            )
            if self.tunnel_client:
                await self.tunnel_client.send_message(error_msg)
        finally:
            self._in_flight_requests.discard(correlation_id)
            clear_context()

    async def _handle_non_streaming_request(
        self,
        correlation_id: str,
        payload: RequestPayload,
        body: bytes | None,
        llm_api_key: str | None,
    ) -> None:
        """Handle a non-streaming request.

        Args:
            correlation_id: Request correlation ID
            payload: Request payload
            body: Decoded request body
            llm_api_key: LLM API key (injected by broker)
        """
        try:
            status, headers, response_body = await self.llm_client.forward_request(
                method=payload.method,
                path=payload.path,
                headers=payload.headers,
                body=body,
                llm_api_key=llm_api_key,
            )

            # Encode response body
            encoded_body = base64.b64encode(response_body).decode("ascii")

            response_msg = create_response_message(
                correlation_id=correlation_id,
                status=status,
                headers={k: v for k, v in headers.items() if k.lower() != "transfer-encoding"},
                body=encoded_body,
            )
            await self.tunnel_client.send_message(response_msg)
            logger.info("Sent response", status=status)

        except TimeoutError:
            logger.error("LLM request timeout")
            error_msg = create_error_message(
                correlation_id=correlation_id,
                status=504,
                error="Request timeout",
                code="timeout",
            )
            await self.tunnel_client.send_message(error_msg)
        except Exception as e:
            logger.error("LLM request failed", error=str(e))
            error_msg = create_error_message(
                correlation_id=correlation_id,
                status=502,
                error="LLM server unavailable",
                code="llm_unavailable",
            )
            await self.tunnel_client.send_message(error_msg)

    async def _handle_streaming_request(
        self,
        correlation_id: str,
        payload: RequestPayload,
        body: bytes | None,
        llm_api_key: str | None,
    ) -> None:
        """Handle a streaming request.

        Args:
            correlation_id: Request correlation ID
            payload: Request payload
            body: Decoded request body
            llm_api_key: LLM API key (injected by broker)
        """
        try:
            first_chunk = True
            async for data in self.llm_client.forward_streaming_request(
                method=payload.method,
                path=payload.path,
                headers=payload.headers,
                body=body,
                llm_api_key=llm_api_key,
            ):
                if first_chunk:
                    # First yield is (status, headers, b"")
                    status, headers, _ = data
                    if status >= 400:
                        # Error response, send as single response
                        # Read remaining chunks to get error body
                        error_body = b""
                        async for chunk in self.llm_client.forward_streaming_request(
                            method=payload.method,
                            path=payload.path,
                            headers=payload.headers,
                            body=body,
                        ):
                            if not first_chunk:
                                error_body += chunk
                            first_chunk = False
                        error_msg = create_error_message(
                            correlation_id=correlation_id,
                            status=status,
                            error=error_body.decode("utf-8", errors="replace"),
                            code="llm_error",
                        )
                        await self.tunnel_client.send_message(error_msg)
                        return
                    first_chunk = False
                    continue

                # Stream chunks
                chunk_msg = create_stream_chunk_message(
                    correlation_id=correlation_id,
                    chunk=data.decode("utf-8", errors="replace"),
                    done=False,
                )
                await self.tunnel_client.send_message(chunk_msg)

            # Send stream end
            end_msg = create_stream_end_message(correlation_id)
            await self.tunnel_client.send_message(end_msg)
            logger.info("Streaming response complete")

        except TimeoutError:
            logger.error("Streaming request timeout")
            error_msg = create_error_message(
                correlation_id=correlation_id,
                status=504,
                error="Request timeout",
                code="timeout",
            )
            await self.tunnel_client.send_message(error_msg)
        except Exception as e:
            logger.error("Streaming request failed", error=str(e))
            error_msg = create_error_message(
                correlation_id=correlation_id,
                status=502,
                error="LLM server unavailable",
                code="llm_unavailable",
            )
            await self.tunnel_client.send_message(error_msg)

    async def _discover_models(self) -> list[str]:
        """Discover available models from the LLM server.

        Returns:
            List of model IDs available on the LLM server
        """
        try:
            models_data = await self.llm_client.get_models()
            models = [m["id"] for m in models_data.get("data", [])]
            logger.info("Discovered models from LLM", models=models, count=len(models))
            return models
        except Exception as e:
            logger.warning("Failed to discover models from LLM", error=str(e))
            return []

    async def run(self) -> None:
        """Run the connector."""
        configure_logging(self.config.log_level)
        logger.info(
            "Starting connector", llm_url=self.config.llm_url, broker_url=self.config.broker_url
        )

        # Discover models from LLM server (use config.models as override if specified)
        if self.config.models:
            models = self.config.models
            logger.info("Using configured models", models=models)
        else:
            models = await self._discover_models()
            if not models:
                logger.warning("No models discovered, connector will still connect")

        # Create tunnel client with models list
        self.tunnel_client = TunnelClient(
            broker_url=self.config.broker_url,
            broker_token=self.config.broker_token,
            request_handler=self._handle_request,
            models=models,
            reconnect_base_delay=self.config.reconnect_base_delay,
            reconnect_max_delay=self.config.reconnect_max_delay,
        )

        # Start health server
        self.health_server = HealthServer(
            port=self.config.health_port,
            tunnel_client=self.tunnel_client,
            llm_client=self.llm_client,
        )
        await self.health_server.start()

        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Run tunnel client
        try:
            await self.tunnel_client.run()
        except Exception as e:
            logger.error("Connector error", error=str(e))
        finally:
            await self.cleanup()

    async def shutdown(self) -> None:
        """Initiate graceful shutdown."""
        logger.info("Shutting down connector")
        self._shutdown_event.set()

        # Wait for in-flight requests to complete (with timeout)
        if self._in_flight_requests:
            logger.info("Waiting for in-flight requests", count=len(self._in_flight_requests))
            for _ in range(30):  # Wait up to 30 seconds
                if not self._in_flight_requests:
                    break
                await asyncio.sleep(1)

        if self.tunnel_client:
            await self.tunnel_client.stop()

    async def cleanup(self) -> None:
        """Clean up resources."""
        if self.health_server:
            await self.health_server.stop()
        await self.llm_client.close()
        logger.info("Connector stopped")


async def run_connector(config: ConnectorConfig) -> None:
    """Run the connector with the given configuration.

    Args:
        config: Connector configuration
    """
    connector = Connector(config)
    await connector.run()
