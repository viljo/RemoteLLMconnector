# Data Model: LLM API Bridge (Multi-Connector, Centralized Keys)

**Feature**: 001-llm-api-bridge
**Date**: 2025-11-29
**Updated**: All API keys centralized in broker for transparent proxy paradigm

## Components

### LLM_connector (runs locally, multiple instances)

- Pure transparent proxy - **NO API key handling**
- Connects to local LLM
- Initiates tunnel to broker
- Registers its available models with broker
- Receives LLM API key per-request from broker (if needed)

### LLM_broker (runs on server, single instance)

- **Manages ALL API keys** (user keys + LLM keys)
- Accepts tunnel connections from multiple connectors
- Validates user API keys
- Routes requests to appropriate connector based on model
- Injects LLM API key into requests before forwarding

## Entities

### ConnectorConfig (Simplified)

```yaml
# Connector configuration - NO API keys
llm_url: "http://localhost:11434"       # LLM endpoint
broker_url: "wss://broker.example.com"  # Broker WebSocket URL
broker_token: "conn-token-1"            # Token to authenticate with broker
models:                                  # Models this connector serves
  - "llama3.2"
  - "mistral-7b"
health_port: 8081
# NO api_keys, NO llm_api_key - pure transparent proxy
```

### BrokerConfig (All Keys Here)

```yaml
# Broker configuration - ALL API keys
port: 8443
host: "0.0.0.0"
health_port: 8080

# External user authentication
user_api_keys:
  - "sk-user-key-1"
  - "sk-user-key-2"

# Connector definitions with LLM keys
connectors:
  - name: "local-ollama"
    token: "conn-token-1"
    llm_api_key: null                   # Local LLM, no key needed

  - name: "openai-proxy"
    token: "conn-token-2"
    llm_api_key: "sk-openai-xxx"        # Injected by broker

  - name: "anthropic-proxy"
    token: "conn-token-3"
    llm_api_key: "sk-ant-xxx"           # Injected by broker
```

### TunnelMessage (shared protocol)

```python
{
    "type": "AUTH" | "AUTH_OK" | "AUTH_FAIL" | "REQUEST" | "RESPONSE" |
            "STREAM_CHUNK" | "STREAM_END" | "ERROR" | "PING" | "PONG",
    "id": "correlation-id",
    "payload": { ... }
}
```

#### AUTH Payload (connector → broker)

```python
{
    "token": "conn-token-1",
    "connector_version": "1.0.0",
    "models": ["llama3.2", "mistral-7b"]
}
```

#### REQUEST Payload (broker → connector) - UPDATED

```python
{
    "method": "POST",
    "path": "/v1/chat/completions",
    "headers": {"Content-Type": "application/json"},
    "body": "base64-encoded-body",
    "llm_api_key": "sk-openai-xxx"  # NEW: Injected by broker, null if not needed
}
```

### ConnectorRegistration (broker tracks connected connectors)

```python
{
    "connector_id": "conn-abc123",
    "connected_at": "datetime",
    "websocket": "<websocket connection>",
    "models": ["llama3.2", "mistral-7b"],
    "llm_api_key": "sk-xxx" | None,      # From broker config
    "pending_requests": {}
}
```

### ModelRoute (broker routing table)

```python
# In-memory, built from connector registrations + config
model_routes = {
    "llama3.2": {
        "connector_id": "conn-abc123",
        "llm_api_key": None
    },
    "gpt-4o": {
        "connector_id": "conn-def456",
        "llm_api_key": "sk-openai-xxx"
    },
}
```

## Data Flow

### Request Routing with Key Injection

```
External User                LLM_broker                    Connector                  LLM
     |                           |                              |                        |
     |-- POST /v1/chat           |                              |                        |
     |   Auth: Bearer sk-user-1  |                              |                        |
     |   model: "gpt-4o"    ---->|                              |                        |
     |                           |                              |                        |
     |                           |-- Validate user key          |                        |
     |                           |-- Look up model route        |                        |
     |                           |   "gpt-4o" → conn-def456     |                        |
     |                           |   llm_api_key: sk-openai-xxx |                        |
     |                           |                              |                        |
     |                           |-- REQUEST (tunnel) --------->|                        |
     |                           |   + llm_api_key in payload   |                        |
     |                           |                              |                        |
     |                           |                              |-- POST /v1/chat ------>|
     |                           |                              |   Auth: Bearer sk-openai|
     |                           |                              |<-- streaming tokens ---|
     |                           |<-- STREAM_CHUNK -------------|                        |
     |<-- SSE stream ------------|                              |                        |
```

### Model Discovery (Aggregated)

```
External User                LLM_broker                    Connectors
     |                           |                              |
     |-- GET /v1/models -------->|                              |
     |   Auth: Bearer sk-user-1  |                              |
     |                           |                              |
     |                           |-- Aggregate models from      |
     |                           |   all registered connectors  |
     |                           |                              |
     |<-- models: [              |                              |
     |     "llama3.2",           | (from conn-abc123)           |
     |     "mistral-7b",         | (from conn-abc123)           |
     |     "gpt-4o",             | (from conn-def456)           |
     |     "claude-3-5-sonnet"   | (from conn-ghi789)           |
     |   ] ----------------------|                              |
```

### Connector Registration

```
Connector                    LLM_broker
     |                           |
     |-- AUTH                    |
     |   token: "conn-token-2"   |
     |   models: ["gpt-4o"] ---->|
     |                           |
     |                           |-- Validate token
     |                           |-- Look up llm_api_key from config
     |                           |-- Register connector with key
     |                           |-- Add models to routing table
     |                           |
     |<-- AUTH_OK                |
     |   session_id: "conn-456" -|
```

## Storage

- **Config**: Environment variables + YAML files
- **Runtime state**: In-memory only
  - Connector registrations (with LLM keys from config)
  - Model routing table
  - Pending requests
- **No database required**

## API Key Flow

| Key Type | Location | Purpose | Validated By |
|----------|----------|---------|--------------|
| User API Key | Broker config | External user auth | Broker API |
| Connector Token | Both configs | Connector-broker auth | Broker tunnel |
| LLM API Key | Broker config | LLM server auth | LLM server |

**Key never leaves broker config** - only injected into tunnel REQUEST payloads.

## Error Handling

| Scenario | Response |
|----------|----------|
| Invalid user API key | 401 Unauthorized (broker) |
| Unknown model | 404 Not Found (broker) |
| No connector for model | 503 Service Unavailable (broker) |
| Connector disconnected | 503 Service Unavailable (broker) |
| LLM timeout | 504 Gateway Timeout (connector) |
| LLM auth error | 401 from LLM (passed through) |
| LLM error | Pass through status code |

## Security Considerations

1. **LLM API keys** only transmitted over encrypted tunnel (WSS)
2. **Keys in transit** only broker→connector, never to external users
3. **Connector compromise** doesn't expose keys (no local storage)
4. **Key rotation** happens in one place (broker config)
5. **Audit trail** - broker logs which connector/model served request

---

## GitLab OAuth Authentication Portal (Extension)

**Date**: 2025-11-30
**Purpose**: Self-service API key issuance via GitLab OAuth

### New Entities

#### User (stored in users.yaml)

```python
@dataclass
class User:
    gitlab_username: str      # GitLab username (unique identifier for display)
    gitlab_id: int            # GitLab user ID (immutable, used for lookup)
    api_key: str              # Generated API key (sk-xxx format)
    created_at: datetime      # When user first authenticated
    last_used: datetime | None  # Last API request timestamp
    blocked: bool             # Operator can set to block access
```

**YAML representation**:
```yaml
users:
  - gitlab_username: "alice"
    gitlab_id: 12345
    api_key: "sk-a1b2c3d4e5f6789012345678901234ab"
    created_at: "2025-11-30T10:00:00Z"
    last_used: "2025-11-30T15:30:00Z"
    blocked: false
```

#### OAuthConfig (added to BrokerConfig)

```python
@dataclass
class OAuthConfig:
    gitlab_url: str           # GitLab instance URL (default: https://gitlab.com)
    client_id: str            # GitLab OAuth app client ID
    client_secret: str        # GitLab OAuth app client secret
    redirect_uri: str         # Callback URL for OAuth flow
    session_secret: str       # Secret for encrypting session cookies
    users_file: Path          # Path to users.yaml file
    public_url: str           # Public URL displayed to users (e.g., https://broker.example.com)
```

**Extended BrokerConfig**:
```yaml
# Broker configuration with OAuth
port: 8443
host: "0.0.0.0"
health_port: 8080

# OAuth configuration
oauth:
  gitlab_url: "https://gitlab.com"
  client_id: "your-gitlab-app-id"
  client_secret: "${GITLAB_CLIENT_SECRET}"  # From env var
  redirect_uri: "https://broker.example.com/auth/callback"
  session_secret: "${SESSION_SECRET}"        # From env var
  users_file: "/etc/remotellm/users.yaml"
  public_url: "https://broker.example.com"

# Connector definitions (unchanged)
connectors:
  - token: "conn-token-1"
    llm_api_key: null
```

#### OAuthSession (in-memory, encrypted cookie)

```python
@dataclass
class OAuthSession:
    state: str                # CSRF protection token
    created_at: datetime      # Session creation time
```

### OAuth Data Flow

#### Login Flow

```
User Browser                    Broker                         GitLab
     |                            |                               |
     |-- GET /auth/login -------->|                               |
     |                            |-- Generate state token        |
     |                            |-- Store in session cookie     |
     |<-- 302 Redirect to         |                               |
     |    GitLab OAuth URL -------|                               |
     |                            |                               |
     |--------------------------- GitLab OAuth --------------------
     |                                                            |
     |<-- 302 Redirect with code, state -------------------------|
     |                            |                               |
     |-- GET /auth/callback       |                               |
     |    ?code=xxx&state=yyy --->|                               |
     |                            |-- Verify state matches cookie |
     |                            |-- Exchange code for token --->|
     |                            |<-- Access token --------------|
     |                            |-- GET /api/v4/user ---------->|
     |                            |<-- {id, username} ------------|
     |                            |                               |
     |                            |-- Load users.yaml             |
     |                            |-- Find or create user         |
     |                            |-- Save users.yaml             |
     |                            |                               |
     |<-- Success page with:      |                               |
     |    - API key               |                               |
     |    - LLM URL               |                               |
     |    - Example curl ---------|                               |
```

#### API Request with OAuth-issued Key

```
External User                    Broker                    users.yaml
     |                             |                           |
     |-- POST /v1/chat/completions |                           |
     |   Auth: Bearer sk-abc123    |                           |
     |                             |                           |
     |                             |-- Load users.yaml ------->|
     |                             |<-- User list --------------|
     |                             |                           |
     |                             |-- Find user by api_key    |
     |                             |-- Check blocked == false  |
     |                             |-- Update last_used ------>|
     |                             |                           |
     |                             |-- Forward to connector    |
     |<-- Response ----------------|                           |
```

### Storage

| Data | Location | Persistence |
|------|----------|-------------|
| User records | `users.yaml` file | Persistent |
| OAuth sessions | Encrypted cookies | Browser session |
| GitLab tokens | Not stored | Transient (single request) |

### API Key Lookup Performance

For typical scale (10-100 users):
- Load entire `users.yaml` into memory on startup
- Reload on file change (inotify/fswatch) or periodic (60s)
- O(n) lookup by API key (dict for O(1) if needed later)

### Error Handling (OAuth)

| Scenario | Response |
|----------|----------|
| Missing GitLab config | OAuth endpoints return 503 |
| Invalid state token | 400 Bad Request (CSRF detected) |
| GitLab unreachable | Error page, suggest retry |
| Token exchange fails | Error page with details |
| User blocked | Blocked page with contact info |
| users.yaml read error | 500 Internal Server Error |

### Security (OAuth-specific)

1. **State parameter**: Random 32-byte hex, validated on callback
2. **Session cookies**: Encrypted with Fernet, HttpOnly, Secure, SameSite=Lax
3. **API keys**: Generated with `secrets.token_hex(16)`, never logged
4. **GitLab tokens**: Used once for user info, not stored
5. **users.yaml**: File permissions 600, atomic writes
