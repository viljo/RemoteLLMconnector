#!/usr/bin/env python3
"""Mock LLM server for integration testing.

Provides OpenAI-compatible endpoints for testing the connector and broker.
"""

import json
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler


class MockLLMHandler(BaseHTTPRequestHandler):
    """Handle mock LLM API requests."""

    def log_message(self, format, *args):
        """Log HTTP requests."""
        print(f"[MockLLM] {args[0]}")

    def _send_json_response(self, status: int, data: dict) -> None:
        """Send a JSON response."""
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_streaming_response(self, model: str) -> None:
        """Send a streaming SSE response."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        # Send role chunk
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.flush()

        # Send content chunks
        for word in ["Hello", " from", " mock", " LLM", "!"]:
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": word}, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.05)  # Small delay for realism

        # Send finish chunk
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.flush()

        # Send done
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path == "/health":
            self._send_json_response(200, {"status": "healthy"})

        elif self.path == "/v1/models":
            self._send_json_response(200, {
                "object": "list",
                "data": [
                    {
                        "id": "test-model",
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "mock-llm",
                    }
                ],
            })

        elif self.path == "/api/tags":
            # Ollama-compatible endpoint
            self._send_json_response(200, {
                "models": [
                    {
                        "name": "test-model",
                        "modified_at": "2024-01-01T00:00:00Z",
                        "size": 1000000000,
                    }
                ]
            })

        else:
            self._send_json_response(404, {"error": "Not found"})

    def do_POST(self) -> None:
        """Handle POST requests."""
        if self.path == "/v1/chat/completions":
            # Read request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            try:
                request_data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json_response(400, {
                    "error": {"message": "Invalid JSON", "type": "invalid_request_error"}
                })
                return

            model = request_data.get("model", "test-model")
            messages = request_data.get("messages", [])
            stream = request_data.get("stream", False)

            # Log the request
            print(f"[MockLLM] Chat completion request: model={model}, messages={len(messages)}, stream={stream}")

            if stream:
                self._send_streaming_response(model)
            else:
                # Non-streaming response
                completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
                response = {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "Hello from mock LLM!",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                }
                self._send_json_response(200, response)

        else:
            self._send_json_response(404, {"error": "Not found"})


def main():
    """Run the mock LLM server."""
    host = "0.0.0.0"
    port = 8000

    server = HTTPServer((host, port), MockLLMHandler)
    print(f"[MockLLM] Starting mock LLM server on {host}:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[MockLLM] Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
