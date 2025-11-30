# RemoteLLMconnector Development Guidelines

Auto-generated from feature plans. Last updated: 2025-11-29

## Components

- **LLM_connector**: Runs locally, connects to local LLM, relays to broker
- **LLM_broker**: Runs on server, accepts relay connections, exposes API for external users

## Technologies

- Python 3.11+, asyncio
- aiohttp (HTTP client/server)
- websockets (relay protocol)
- pydantic-settings (config)
- structlog (JSON logging)
- click (CLI)
- pytest + pytest-asyncio

## Project Structure

```text
src/remotellm/
├── shared/              # Shared protocol and models
│   ├── protocol.py      # RelayMessage types (AUTH, REQUEST, etc.)
│   ├── models.py        # Pydantic models (ChatCompletionRequest, etc.)
│   └── logging.py       # structlog configuration
├── connector/           # LLM_connector (pure transparent proxy)
│   ├── __main__.py      # CLI: python -m remotellm.connector
│   ├── config.py        # ConnectorConfig settings (models list)
│   ├── main.py          # Connector application
│   ├── health.py        # /health endpoint (shows registered models)
│   ├── relay_client.py  # WebSocket client to broker (sends models in AUTH)
│   └── llm_client.py    # HTTP client to local LLM (receives API key per-request)
└── broker/              # LLM_broker (centralized API key management)
    ├── __main__.py      # CLI: python -m remotellm.broker
    ├── config.py        # BrokerConfig settings (user_api_keys, connector_configs)
    ├── main.py          # Broker application
    ├── health.py        # /health endpoint (shows all available models)
    ├── relay_server.py  # WebSocket server for connectors
    ├── router.py        # Model-based routing (ModelRouter class)
    └── api.py           # HTTP API for external users (model routing, API key injection)

deploy/
├── systemd/             # Linux service files
│   ├── remotellm-connector.service
│   └── remotellm-broker.service
├── launchd/             # macOS service files
│   ├── com.remotellm.connector.plist
│   └── com.remotellm.broker.plist
├── connectors.yaml.example  # Example connector config (token → llm_api_key)
├── install-linux.sh     # Linux installation script
└── install-macos.sh     # macOS installation script

tests/
├── unit/
├── integration/
└── e2e/
    └── cassettes/       # Recorded responses from llm.viljo.se
```

## Commands

```bash
uv sync                              # Install deps
uv sync --all-extras                 # Install deps including dev tools
python -m remotellm.connector --help # Connector CLI help
python -m remotellm.broker --help    # Broker CLI help
pytest                               # Run tests
ruff check . && ruff format .        # Lint and format
mypy src/remotellm/                  # Type check
```

## Running Locally

```bash
# Terminal 1: Start broker
python -m remotellm.broker \
  --port 8443 \
  --connector-token mytoken \
  --user-api-key sk-test-key \
  --connector-config connectors.yaml

# Terminal 2: Start connector
python -m remotellm.connector \
  --llm-url http://localhost:11434 \
  --broker-url ws://localhost:8444 \
  --broker-token mytoken \
  --model llama3.2

# Terminal 3: Test API
curl http://localhost:8443/v1/chat/completions \
  -H "Authorization: Bearer sk-test-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "llama3.2", "messages": [{"role": "user", "content": "Hi"}]}'
```

Note: Connector no longer accepts `--api-key`. All API keys are managed by the broker.

## E2E Test Endpoint

- URL: `https://llm.viljo.se/v1`
- API key in `.env.test` (gitignored)

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->

## Active Technologies
- Python 3.11+ + aiohttp, websockets, pydantic, structlog, click (001-llm-api-bridge)
- In-memory (config via env/YAML) (001-llm-api-bridge)
- Python 3.11+ + aiohttp (existing), aiohttp-session, authlib (GitLab OAuth) (001-llm-api-bridge)
- YAML file for API keys + user info (simple, operator-editable) (001-llm-api-bridge)

## Recent Changes
- 001-llm-api-bridge: Added Python 3.11+ + aiohttp, websockets, pydantic, structlog, click
