# RemoteLLMconnector

LLM API Bridge - Expose local LLM servers externally via a NAT-friendly relay.

## Overview

RemoteLLMconnector enables local LLM servers (Ollama, llama.cpp, vLLM, etc.) to be accessed from the internet through an OpenAI-compatible API. The system uses a WebSocket relay that works behind NAT/firewalls without port forwarding.

### Architecture

```
External User                    Cloud Server                    Your Networks
     |                               |                               |
     |  POST /v1/chat/completions    |                               |
     |  model: "gpt-4"               |                               |
     |------------------------------>|                               |
     |                          [Broker]                             |
     |                        (Model Router)                         |
     |                               |                               |
     |                               |<----- WebSocket Relay ------>[Connector A]
     |                               |       (serves: gpt-4)         [OpenAI API]
     |                               |                               |
     |                               |<----- WebSocket Relay ------>[Connector B]
     |                               |       (serves: llama3.2)      [Ollama]
     |                               |                               |
     |<------ SSE Response ----------|                               |
```

**Multi-Connector Support**: Multiple connectors can register different models. The broker routes requests to the appropriate connector based on the requested model.

### Components

- **Broker**: Runs on a cloud server, accepts WebSocket relay connections, exposes OpenAI-compatible HTTP API
- **Connector**: Runs locally, connects to broker via WebSocket, forwards requests to local LLM

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/RemoteLLMconnector.git
cd RemoteLLMconnector

# Install with uv
uv sync
```

## Quick Start

### 1. Start the Broker (on your server)

```bash
python -m remotellm.broker \
  --port 8443 \
  --connector-token your-secret-token \
  --user-api-key sk-user-api-key \
  --connector-config /etc/remotellm/connectors.yaml \
  --health-port 8080
```

### 2. Start the Connector (on your local machine)

```bash
python -m remotellm.connector \
  --llm-url http://localhost:11434 \
  --broker-url ws://your-server.com:8444 \
  --broker-token your-secret-token \
  --model llama3.2 \
  --model codellama \
  --health-port 8081
```

**Note**: The connector registers the models it serves. The broker handles all API key management (both user and LLM API keys).

### 3. Make API Requests

```bash
curl http://your-server.com:8443/v1/chat/completions \
  -H "Authorization: Bearer sk-user-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

The broker routes the request to the connector serving `llama3.2` and injects the appropriate LLM API key.

## Configuration

### Broker Options

| Option | Env Variable | Default | Description |
|--------|--------------|---------|-------------|
| `--host` | `REMOTELLM_BROKER_HOST` | `0.0.0.0` | Host to bind to |
| `--port` | `REMOTELLM_BROKER_PORT` | `8443` | HTTP API port (relay at /ws) |
| `--connector-token` | `REMOTELLM_BROKER_CONNECTOR_TOKENS` | - | Token(s) for connector auth |
| `--user-api-key` | `REMOTELLM_BROKER_USER_API_KEYS` | - | API key(s) for external users |
| `--connector-config` | `REMOTELLM_BROKER_CONNECTOR_CONFIG` | - | YAML file with connector configs |
| `--health-port` | `REMOTELLM_BROKER_HEALTH_PORT` | `8080` | Health endpoint port |
| `--log-level` | `REMOTELLM_BROKER_LOG_LEVEL` | `INFO` | Logging level |

### Connector Options

| Option | Env Variable | Default | Description |
|--------|--------------|---------|-------------|
| `--llm-url` | `REMOTELLM_LLM_URL` | `http://localhost:11434` | Local LLM server URL |
| `--broker-url` | `REMOTELLM_BROKER_URL` | - | Broker WebSocket URL (required) |
| `--broker-token` | `REMOTELLM_BROKER_TOKEN` | - | Broker auth token (required) |
| `--model` | `REMOTELLM_MODELS` | - | Model name(s) served by this connector |
| `--health-port` | `REMOTELLM_HEALTH_PORT` | `8080` | Health endpoint port |
| `--log-level` | `REMOTELLM_LOG_LEVEL` | `INFO` | Logging level |

### Connector Configuration File

The broker uses a YAML file to map connector tokens to LLM API keys:

```yaml
# /etc/remotellm/connectors.yaml
connectors:
  - token: "connector-token-1"
    llm_api_key: "sk-your-openai-api-key"
  - token: "connector-token-2"
    llm_api_key: "sk-your-anthropic-key"
  - token: "connector-token-3"
    # No llm_api_key for LLMs that don't require auth (e.g., local Ollama)
```

## API Endpoints

### Broker API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | OpenAI-compatible chat completions |
| `/v1/models` | GET | List available models |
| `/health` | GET | Health check status |

### Health Endpoints

Both components expose health endpoints:

```bash
# Broker health
curl http://localhost:8080/health
# {"status": "healthy", "connectors_connected": 2, "models": ["gpt-4", "llama3.2"], "model_count": 2, ...}

# Connector health
curl http://localhost:8081/health
# {"status": "healthy", "relay_connected": true, "llm_available": true, "models": ["llama3.2"], ...}

# Connector readiness
curl http://localhost:8081/ready
# {"ready": true, "relay_connected": true, "llm_available": true}
```

## Running as a Service

### Linux (systemd)

```bash
sudo ./deploy/install-linux.sh
```

### macOS (launchd)

```bash
./deploy/install-macos.sh
```

See [quickstart.md](specs/001-llm-api-bridge/quickstart.md) for detailed service configuration.

## Development

```bash
# Install dev dependencies
uv sync --all-extras

# Run tests
pytest

# Lint and format
ruff check . && ruff format .

# Type check
mypy src/remotellm/
```

## Features

- OpenAI-compatible API (`/v1/chat/completions`, `/v1/models`)
- **Multi-connector architecture**: Multiple connectors serving different models
- **Model-based routing**: Requests routed to correct connector by model name
- **Centralized API key management**: Broker manages all API keys (user + LLM)
- Streaming responses (SSE)
- NAT-friendly WebSocket relay
- Automatic reconnection with exponential backoff
- Health monitoring endpoints with model visibility
- Structured JSON logging with correlation IDs
- Graceful shutdown
- systemd/launchd service support

## License

MIT
