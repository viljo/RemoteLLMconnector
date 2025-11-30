# Tunnel Protocol Specification

**Feature**: 001-llm-api-bridge
**Date**: 2025-11-29

## Overview

The tunnel protocol defines how the connector communicates with the external API provider over WebSocket. This is a custom protocol that wraps HTTP requests/responses for transport through the tunnel.

## Connection Establishment

1. Connector initiates WebSocket connection to `tunnel_url`
2. Connector sends `AUTH` message with `tunnel_token`
3. Provider responds with `AUTH_OK` or `AUTH_FAIL`
4. On `AUTH_OK`, connection is established and ready for requests

## Message Format

All messages are JSON-encoded with the following base structure:

```json
{
  "type": "<message_type>",
  "id": "<correlation_id>",
  "payload": { ... }
}
```

## Message Types

### Connector → Provider

#### AUTH
Sent immediately after WebSocket connection.

```json
{
  "type": "AUTH",
  "id": "auth-1",
  "payload": {
    "token": "<tunnel_token>",
    "connector_version": "1.0.0"
  }
}
```

#### RESPONSE
Sent when forwarding LLM response back.

```json
{
  "type": "RESPONSE",
  "id": "<request_correlation_id>",
  "payload": {
    "status": 200,
    "headers": {
      "content-type": "application/json"
    },
    "body": "<response_body_base64>"
  }
}
```

#### STREAM_CHUNK
Sent for each chunk of a streaming response.

```json
{
  "type": "STREAM_CHUNK",
  "id": "<request_correlation_id>",
  "payload": {
    "chunk": "<sse_event_data>",
    "done": false
  }
}
```

#### STREAM_END
Sent when streaming response completes.

```json
{
  "type": "STREAM_END",
  "id": "<request_correlation_id>",
  "payload": {
    "done": true
  }
}
```

#### ERROR
Sent when request processing fails.

```json
{
  "type": "ERROR",
  "id": "<request_correlation_id>",
  "payload": {
    "status": 502,
    "error": "LLM server unavailable",
    "code": "llm_unavailable"
  }
}
```

#### PING
Sent periodically to keep connection alive.

```json
{
  "type": "PING",
  "id": "ping-<timestamp>",
  "payload": {}
}
```

### Provider → Connector

#### AUTH_OK
Confirms successful authentication.

```json
{
  "type": "AUTH_OK",
  "id": "auth-1",
  "payload": {
    "session_id": "<session_uuid>"
  }
}
```

#### AUTH_FAIL
Rejects authentication.

```json
{
  "type": "AUTH_FAIL",
  "id": "auth-1",
  "payload": {
    "error": "Invalid token"
  }
}
```

#### REQUEST
Forwards an API request from external user.

```json
{
  "type": "REQUEST",
  "id": "<correlation_id>",
  "payload": {
    "method": "POST",
    "path": "/v1/chat/completions",
    "headers": {
      "authorization": "Bearer <user_api_key>",
      "content-type": "application/json"
    },
    "body": "<request_body_base64>"
  }
}
```

#### PONG
Response to PING.

```json
{
  "type": "PONG",
  "id": "ping-<timestamp>",
  "payload": {}
}
```

#### CANCEL
Requests cancellation of an in-flight request.

```json
{
  "type": "CANCEL",
  "id": "<request_correlation_id>",
  "payload": {}
}
```

## State Machine

```
DISCONNECTED
    │
    ├── connect() → CONNECTING
    │
CONNECTING
    │
    ├── send AUTH
    │
    ├── recv AUTH_OK → AUTHENTICATED
    │
    ├── recv AUTH_FAIL → DISCONNECTED
    │
    ├── timeout → DISCONNECTED (retry with backoff)
    │
AUTHENTICATED (ready for requests)
    │
    ├── recv REQUEST → process and send RESPONSE/STREAM_*/ERROR
    │
    ├── send PING → wait for PONG
    │
    ├── connection lost → DISCONNECTED (auto-reconnect)
    │
    ├── recv CANCEL → abort in-flight request
    │
    └── shutdown → DISCONNECTING

DISCONNECTING
    │
    └── complete in-flight → DISCONNECTED
```

## Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `auth_failed` | 401 | Invalid or missing API key |
| `rate_limited` | 429 | Rate limit exceeded |
| `llm_unavailable` | 502 | Local LLM not responding |
| `llm_error` | 502 | Local LLM returned error |
| `timeout` | 504 | Request timed out |
| `internal_error` | 500 | Connector internal error |

## Timeouts

| Operation | Timeout | Notes |
|-----------|---------|-------|
| WebSocket connect | 30s | Initial connection |
| AUTH response | 10s | After sending AUTH |
| PING/PONG | 30s | Connection health check |
| Request processing | 300s | From REQUEST to final RESPONSE/STREAM_END |

## Reconnection Policy

On connection loss:
1. Wait `base_delay * 2^attempt` seconds (with jitter)
2. Attempt reconnection
3. If successful, re-authenticate
4. If failed, increment attempt counter
5. After `max_retries` failures, log error and wait longer before retrying

Default: `base_delay=1.0`, `max_retries=5`
