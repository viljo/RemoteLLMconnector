# Quickstart: RemoteLLMconnector

Get your local LLM accessible externally in 5 minutes.

## Prerequisites

- Python 3.11+
- A local LLM server running with OpenAI-compatible API (e.g., Ollama, llama.cpp, vLLM)
- A running broker instance (or use a hosted broker service)

## Installation

```bash
# Using uv (recommended)
uv pip install remotellm

# Or using pip
pip install remotellm
```

## Running the Connector

The connector bridges your local LLM to the broker and registers which models it serves:

```bash
# With command line options
python -m remotellm.connector \
  --llm-url http://localhost:11434 \
  --broker-url wss://broker.example.com:8444 \
  --broker-token your-connector-token \
  --model llama3.2 \
  --model codellama

# Or with environment variables
export REMOTELLM_LLM_URL="http://localhost:11434"
export REMOTELLM_BROKER_URL="wss://broker.example.com:8444"
export REMOTELLM_BROKER_TOKEN="your-connector-token"
export REMOTELLM_MODELS="llama3.2,codellama"
python -m remotellm.connector
```

**Note**: The connector no longer handles API keys. All API key management is centralized in the broker.

## Running the Broker

The broker exposes the API for external users and manages all API keys:

```bash
# With command line options
python -m remotellm.broker \
  --host 0.0.0.0 \
  --port 8443 \
  --connector-token your-connector-token \
  --user-api-key sk-user-key-1 \
  --user-api-key sk-user-key-2 \
  --connector-config /etc/remotellm/connectors.yaml

# Or with environment variables
export REMOTELLM_BROKER_HOST="0.0.0.0"
export REMOTELLM_BROKER_PORT="8443"
export REMOTELLM_BROKER_CONNECTOR_TOKENS="your-connector-token"
export REMOTELLM_BROKER_USER_API_KEYS="sk-user-key-1,sk-user-key-2"
export REMOTELLM_BROKER_CONNECTOR_CONFIG="/etc/remotellm/connectors.yaml"
python -m remotellm.broker
```

### Connector Configuration File

Create `/etc/remotellm/connectors.yaml` to map connector tokens to LLM API keys:

```yaml
connectors:
  - token: "your-connector-token"
    llm_api_key: "sk-your-openai-api-key"
  - token: "another-connector-token"
    # No llm_api_key for LLMs that don't require auth
```

## Verifying the Setup

### 1. Check Health Endpoints

Connector health:
```bash
curl http://localhost:8080/health
```

Expected response:
```json
{
  "status": "healthy",
  "tunnel_connected": true,
  "tunnel_state": "connected",
  "llm_available": true,
  "models": ["llama3.2", "codellama"],
  "uptime_seconds": 42.5
}
```

Broker health:
```bash
curl http://broker-host:8080/health
```

Expected response:
```json
{
  "status": "healthy",
  "connectors_connected": 1,
  "models": ["llama3.2", "codellama"],
  "model_count": 2,
  "uptime_seconds": 120.5
}
```

### 2. Test API Access

```bash
curl http://broker-host:8443/v1/chat/completions \
  -H "Authorization: Bearer sk-user-key-1" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

The broker routes the request to the connector serving `llama3.2` and injects the LLM API key.

### 3. Test with Cline (VS Code)

In Cline settings:
- API Provider: Custom
- Base URL: `http://broker-host:8443/v1`
- API Key: `sk-your-api-key`

## Common Issues

### "No connectors available"

- Ensure connector is running and connected to broker
- Check connector logs for authentication errors
- Verify `--broker-token` matches broker's `--connector-token`

### "Model not found" (404)

- Check that the connector registered the requested model with `--model`
- Verify model name matches exactly (case-sensitive)
- Check broker health endpoint to see available models

### "LLM unavailable"

- Verify local LLM is running: `curl http://localhost:11434/api/tags`
- Check `--llm-url` matches your LLM server address

### "Invalid or missing API key"

- Ensure API key is passed with `Authorization: Bearer <key>` header
- Check key is in broker's `--user-api-key` list (not connector)

## Multi-Connector Setup Example

Run multiple connectors to serve different models through a single broker:

### 1. Broker Configuration

```yaml
# /etc/remotellm/connectors.yaml
connectors:
  - token: "openai-connector-token"
    llm_api_key: "sk-your-openai-key"
  - token: "ollama-connector-token"
    # No API key needed for local Ollama
```

Start the broker:
```bash
python -m remotellm.broker \
  --port 8443 \
  --connector-token openai-connector-token \
  --connector-token ollama-connector-token \
  --user-api-key sk-user-key \
  --connector-config /etc/remotellm/connectors.yaml
```

### 2. OpenAI Connector (on machine A)

```bash
python -m remotellm.connector \
  --llm-url https://api.openai.com \
  --broker-url wss://broker.example.com:8444 \
  --broker-token openai-connector-token \
  --model gpt-4 \
  --model gpt-3.5-turbo
```

### 3. Ollama Connector (on machine B)

```bash
python -m remotellm.connector \
  --llm-url http://localhost:11434 \
  --broker-url wss://broker.example.com:8444 \
  --broker-token ollama-connector-token \
  --model llama3.2 \
  --model codellama
```

### 4. Using the API

```bash
# Route to OpenAI
curl http://broker.example.com:8443/v1/chat/completions \
  -H "Authorization: Bearer sk-user-key" \
  -d '{"model": "gpt-4", "messages": [...]}'

# Route to Ollama
curl http://broker.example.com:8443/v1/chat/completions \
  -H "Authorization: Bearer sk-user-key" \
  -d '{"model": "llama3.2", "messages": [...]}'
```

## Running as a Service

### Linux (systemd)

Use the provided installation script:

```bash
# Install both connector and broker
sudo ./deploy/install-linux.sh both

# Or install individually
sudo ./deploy/install-linux.sh connector
sudo ./deploy/install-linux.sh broker
```

After installation:

```bash
# Edit configuration
sudo nano /etc/remotellm/connector.env
sudo nano /etc/remotellm/broker.env

# Start and enable services
sudo systemctl enable --now remotellm-connector
sudo systemctl enable --now remotellm-broker

# Check status
sudo systemctl status remotellm-connector
sudo systemctl status remotellm-broker

# View logs
sudo journalctl -u remotellm-connector -f
sudo journalctl -u remotellm-broker -f
```

### macOS (launchd)

Use the provided installation script:

```bash
# Install connector (runs as user)
./deploy/install-macos.sh connector

# Edit configuration
nano ~/Library/LaunchAgents/com.remotellm.connector.plist

# Load and start
launchctl load -w ~/Library/LaunchAgents/com.remotellm.connector.plist

# View logs
tail -f /usr/local/var/log/remotellm/connector.log
```

## GitLab OAuth Authentication (Optional)

Enable self-service API key issuance via GitLab OAuth:

### 1. Create GitLab OAuth Application

1. Go to GitLab → Settings → Applications
2. Create new application:
   - Name: `RemoteLLM Broker`
   - Redirect URI: `https://broker.example.com/auth/callback`
   - Scopes: `read_user`
3. Save the Client ID and Client Secret

### 2. Configure Broker with OAuth

```bash
python -m remotellm.broker \
  --port 8443 \
  --connector-token your-connector-token \
  --gitlab-url https://gitlab.com \
  --gitlab-client-id your-client-id \
  --gitlab-client-secret your-client-secret \
  --gitlab-redirect-uri https://broker.example.com/auth/callback \
  --users-file /etc/remotellm/users.yaml \
  --session-secret $(openssl rand -hex 32) \
  --public-url https://broker.example.com
```

Or with environment variables:
```bash
export REMOTELLM_BROKER_GITLAB_URL="https://gitlab.com"
export REMOTELLM_BROKER_GITLAB_CLIENT_ID="your-client-id"
export REMOTELLM_BROKER_GITLAB_CLIENT_SECRET="your-client-secret"
export REMOTELLM_BROKER_GITLAB_REDIRECT_URI="https://broker.example.com/auth/callback"
export REMOTELLM_BROKER_USERS_FILE="/etc/remotellm/users.yaml"
export REMOTELLM_BROKER_SESSION_SECRET="$(openssl rand -hex 32)"
export REMOTELLM_BROKER_PUBLIC_URL="https://broker.example.com"
```

### 3. User Self-Service Flow

1. User visits `https://broker.example.com/auth/login`
2. Clicks "Login with GitLab"
3. Authenticates via GitLab OAuth
4. Receives API key and LLM URL on success page:
   ```
   Your API Key: sk-abc123...
   LLM API URL: https://broker.example.com/v1

   Example:
   curl https://broker.example.com/v1/chat/completions \
     -H "Authorization: Bearer sk-abc123..." \
     -H "Content-Type: application/json" \
     -d '{"model": "llama3.2", "messages": [...]}'
   ```

### 4. User Management

Users are stored in `/etc/remotellm/users.yaml`:

```yaml
users:
  - gitlab_username: alice
    gitlab_id: 12345
    api_key: sk-abc123...
    created_at: "2025-11-30T10:00:00Z"
    last_used: "2025-11-30T15:30:00Z"
    blocked: false
```

To block a user, edit the file and set `blocked: true`:
```bash
sudo nano /etc/remotellm/users.yaml
# Set blocked: true for the user
```

## Next Steps

- [Configuration Reference](./data-model.md) - All configuration options
- [API Contract](./contracts/openapi.yaml) - OpenAPI specification
- [OAuth API Contract](./contracts/oauth-api.yaml) - OAuth endpoints specification
- [Tunnel Protocol](./contracts/tunnel-protocol.md) - Internal protocol details
