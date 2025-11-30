# Research: LLM API Bridge

**Feature**: 001-llm-api-bridge
**Date**: 2025-11-29

## Technology Decisions

### 1. Relay Protocol

**Decision**: WebSocket over TLS

**Rationale**:
- Bidirectional communication required for request/response flow
- NAT-friendly (outbound connection from connector)
- Native streaming support for LLM token-by-token responses
- Well-supported in Python asyncio ecosystem (websockets library)
- Lower overhead than HTTP polling

**Alternatives Considered**:
- HTTP/2 Server Push: More complex, less bidirectional control
- gRPC streaming: Heavier dependency, overkill for this use case
- Raw TCP relay: No built-in framing, harder to debug
- MQTT: Message broker pattern doesn't fit request/response model

### 2. Async HTTP Client

**Decision**: aiohttp

**Rationale**:
- Native asyncio support (constitution requires asyncio)
- Streaming request/response bodies without buffering
- Connection pooling for local LLM requests
- Mature, well-maintained library
- Supports both client and server modes

**Alternatives Considered**:
- httpx: Good alternative, slightly less mature async support
- requests + threading: Not asyncio-native, violates constitution
- urllib3: No native async support

### 3. WebSocket Library

**Decision**: websockets

**Rationale**:
- Pure asyncio implementation
- Clean API for both client and server
- Automatic ping/pong for connection health
- Built-in reconnection patterns
- Minimal dependencies

**Alternatives Considered**:
- aiohttp websockets: Less flexible for custom protocols
- socket.io: Too heavy, designed for different use case
- autobahn: More complex, Twisted heritage

### 4. Configuration Management

**Decision**: pydantic-settings + python-dotenv

**Rationale**:
- Type-safe configuration with validation
- Automatic environment variable binding
- Support for .env files and config files
- Constitution requires env var + config file support
- Pydantic v2 is fast and well-maintained

**Alternatives Considered**:
- dynaconf: More features than needed
- python-decouple: Less type safety
- Raw os.environ: No validation, error-prone

### 5. Structured Logging

**Decision**: structlog

**Rationale**:
- JSON output by default (constitution requires JSON logging)
- Async-friendly
- Context binding for correlation IDs
- Minimal overhead
- Easy integration with stdlib logging

**Alternatives Considered**:
- python-json-logger: Less ergonomic API
- loguru: Not as structured-first
- stdlib logging + custom formatter: More boilerplate

### 6. Rate Limiting

**Decision**: In-memory token bucket per API key

**Rationale**:
- Simple, no external dependencies
- Sufficient for 10 concurrent users
- Per-API-key as specified in assumptions
- Can be upgraded to Redis-backed if scaling needed later

**Alternatives Considered**:
- Redis-backed: Overkill for 10 users, adds dependency
- Fixed window: Less smooth than token bucket
- External rate limiter: Unnecessary complexity

### 7. CLI Framework

**Decision**: Click

**Rationale**:
- Simple, declarative command definitions
- Async support via asyncio.run()
- Well-documented, stable
- Minimal footprint

**Alternatives Considered**:
- Typer: Based on Click, adds type hints but more dependencies
- argparse: More verbose
- Fire: Magic-based, less explicit

## Dependency Summary

**Core Dependencies** (production):
```
aiohttp>=3.9.0
websockets>=12.0
pydantic>=2.5.0
pydantic-settings>=2.1.0
python-dotenv>=1.0.0
structlog>=24.1.0
click>=8.1.0
```

**Development Dependencies**:
```
pytest>=8.0.0
pytest-asyncio>=0.23.0
pytest-cov>=4.1.0
ruff>=0.2.0
mypy>=1.8.0
```

## Open Questions (Resolved)

| Question | Resolution |
|----------|------------|
| Relay protocol | WebSocket - bidirectional, streaming-friendly |
| How to handle relay reconnection | Exponential backoff with jitter, max 5 retries then alert |
| API key storage format | JSON file or environment variables, operator choice |
| Health endpoint binding | Separate HTTP server on configurable port (default 8080) |
| Metrics format | Prometheus-compatible text format via health endpoint |

## Architecture Diagram (Production)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Production Environment                          │
│                                                                             │
│   LOCAL NETWORK                           │         CLOUD/SERVER            │
│   ─────────────                           │         ────────────            │
│                                           │                                 │
│  ┌─────────────┐    ┌─────────────────┐   │   ┌─────────────────┐   ┌─────────────┐
│  │ Local LLM   │<-->│  LLM_connector  │<──┼──>│   LLM_broker    │<->│ External    │
│  │ (Ollama,    │    │                 │ NAT   │                 │   │ Users       │
│  │  llama.cpp) │    │ - relay_client │relay │ - relay_server │   │ (Cline,     │
│  │             │    │ - llm_client    │   │   │ - api           │   │  API calls) │
│  │             │    │ - auth          │   │   │ - router        │   │             │
│  │             │    │ - ratelimit     │   │   │ - registry      │   │             │
│  └─────────────┘    └─────────────────┘   │   └─────────────────┘   └─────────────┘
│                                           │                                 │
└─────────────────────────────────────────────────────────────────────────────┘

Request flow:
  External User -> LLM_broker (api) -> [NAT relay] -> LLM_connector -> Local LLM

Response flow:
  Local LLM -> LLM_connector -> [NAT relay] -> LLM_broker -> External User
```

## Request Flow

1. External user sends request to LLM_broker API endpoint
2. LLM_broker routes request through WebSocket relay to LLM_connector
3. LLM_connector validates API key (local validation)
4. LLM_connector checks rate limit for API key
5. LLM_connector forwards request to local LLM (aiohttp client)
6. Local LLM streams response tokens
7. LLM_connector forwards each token through relay (no buffering)
8. LLM_broker delivers response to external user
9. Both components log request with correlation ID and latency metrics

## End-to-End Test Setup

### 8. E2E Test Strategy

**Decision**: Use a public LLM API proxy exposed locally on 127.0.0.1 as a mock LLM server

**Rationale**:
- No need to run actual LLM inference during tests (slow, resource-intensive)
- Public LLM APIs (OpenAI, Anthropic) provide real OpenAI-compatible responses
- Proxy on 127.0.0.1 simulates the "local LLM" scenario
- Tests validate the full request flow without local GPU/model requirements
- Deterministic and fast test execution

**Architecture for E2E Tests**:

```
┌────────────────────────────────────────────────────────────────────────────┐
│                           E2E Test Environment                              │
│                                                                            │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌─────────────┐
│  │ llm.viljo.se │ <--> │ LLM_connector│ <--> │  LLM_broker  │ <--> │ Test client │
│  │ /v1          │      │ (under test) │ NAT  │ (under test) │      │ (pytest)    │
│  │              │      │              │ relay              │      │             │
│  │ simulates    │      │ src/remotellm│      │ src/remotellm│      │ simulates   │
│  │ local LLM    │      │ /connector/  │      │ /broker/     │      │ external    │
│  │              │      │              │      │              │      │ user        │
│  └──────────────┘      └──────────────┘      └──────────────┘      └─────────────┘
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘

Request flow:
  Test client -> LLM_broker -> [NAT relay] -> LLM_connector -> llm.viljo.se/v1

Response flow:
  llm.viljo.se/v1 -> LLM_connector -> [NAT relay] -> LLM_broker -> Test client
```

**Note**: In E2E tests, both `LLM_connector` and `LLM_broker` are the actual production code from `src/remotellm/`. The test client simulates external users making API calls.

### 9. LLM_broker (Production Code)

**Decision**: Use actual `src/remotellm/broker/` code in E2E tests

**Rationale**:
- Tests the real production code, not mocks
- aiohttp WebSocket server accepts relay connections
- Full control over configuration for negative testing
- Runs in-process with pytest-asyncio

**E2E Test Setup**:
```python
# tests/e2e/conftest.py
@pytest.fixture
async def broker():
    """Start LLM_broker on 127.0.0.1:9999"""
    from remotellm.broker.main import create_broker
    broker = await create_broker(port=9999)
    yield broker
    await broker.shutdown()

@pytest.fixture
async def connector(broker):
    """Start LLM_connector connecting to broker and llm.viljo.se"""
    from remotellm.connector.main import create_connector
    connector = await create_connector(
        llm_url="https://llm.viljo.se/v1",
        broker_url="ws://127.0.0.1:9999"
    )
    yield connector
    await connector.shutdown()
```

### 10. llm.viljo.se/v1 (Simulates Local LLM)

**Decision**: Use llm.viljo.se/v1 as the "local LLM" endpoint in E2E tests

**Rationale**:
- Real OpenAI-compatible API responses without running local model
- LLM_connector connects to this as if it were a local LLM
- Can record/replay responses for deterministic tests
- API key stored in test env vars (not committed)

**Implementation Options**:

| Option | Pros | Cons |
|--------|------|------|
| A. Live proxy to OpenAI | Real responses, validates integration | Requires API key, costs money, non-deterministic |
| B. Recorded responses | Deterministic, free, fast | May drift from real API |
| C. Hybrid (record/replay) | Best of both, can refresh recordings | More complex setup |

**Decision**: Option C (Hybrid record/replay)
- Default: replay pre-recorded responses (fast, deterministic)
- CI env var to enable live mode for periodic validation
- pytest-recording or VCR.py for HTTP cassette management

### 11. E2E Test Cases

| Test Case | Description | Mock Behavior |
|-----------|-------------|---------------|
| `test_e2e_chat_completion` | Full request/response flow | Normal response |
| `test_e2e_streaming` | Streaming tokens via SSE | Chunked response |
| `test_e2e_auth_rejection` | Invalid API key | Connector rejects |
| `test_e2e_rate_limit` | Exceed rate limit | 429 response |
| `test_e2e_llm_unavailable` | LLM proxy down | 502 response |
| `test_e2e_relay_reconnect` | Relay drops mid-request | Connector recovers |
| `test_e2e_graceful_shutdown` | Shutdown with in-flight | Request completes |

### 12. Test Dependencies (Additional)

```
# E2E test dependencies
pytest-recording>=0.13.0  # HTTP cassette recording
aioresponses>=0.7.0       # Async HTTP mocking (for unit tests)
```

### 13. E2E Test Configuration

```yaml
# tests/e2e/config.yaml
mock_relay_port: 9999
llm_proxy_port: 11434
llm_proxy_target: "https://llm.viljo.se/v1"
llm_proxy_mode: "replay"  # or "live" or "record"
cassette_dir: "tests/e2e/cassettes"
test_api_key: "sk-test-key-for-e2e-min-32-chars"
```

**Environment Variables for Live Mode** (store in `.env.test`, never commit):
```bash
# .env.test (gitignored)
LLM_PROXY_TARGET=https://llm.viljo.se/v1
LLM_PROXY_API_KEY=sk-d1a5b8b8cd4e4ac899f26ff5219e0b80
LLM_PROXY_MODE=live
```

### E2E Test Directory Structure

```text
tests/
├── e2e/
│   ├── __init__.py
│   ├── conftest.py          # E2E fixtures (starts broker + connector)
│   ├── cassettes/           # Recorded HTTP responses from llm.viljo.se
│   │   ├── chat_completion.yaml
│   │   └── streaming.yaml
│   ├── test_full_flow.py    # Happy path E2E tests
│   ├── test_error_cases.py  # Error handling E2E tests
│   └── test_resilience.py   # Reconnection, shutdown tests
```

**Note**: No mock files needed - E2E tests use actual `src/remotellm/broker/` and `src/remotellm/connector/` code.

---

## GitLab OAuth Authentication Portal (Extension)

**Date**: 2025-11-30

### 14. GitLab OAuth2 Integration with Python/aiohttp

**Decision**: Use `authlib` library with aiohttp integration

**Rationale**:
- `authlib` is the de-facto standard for OAuth in Python
- Supports async operations with aiohttp
- Well-maintained, widely used (5k+ GitHub stars)
- GitLab-specific documentation available
- Handles PKCE, state validation, token refresh automatically

**Alternatives Considered**:
- `python-gitlab`: Full GitLab API client, overkill for just OAuth
- `oauthlib`: Lower-level, requires more boilerplate
- Manual implementation: Error-prone, security risks

**GitLab OAuth Specifics**:
- Authorization URL: `https://gitlab.com/oauth/authorize`
- Token URL: `https://gitlab.com/oauth/token`
- User info URL: `https://gitlab.com/api/v4/user`
- Required scope: `read_user` (for username and ID)
- Response fields needed: `id`, `username`

### 15. Session Management for aiohttp

**Decision**: Use `aiohttp-session` with encrypted cookie storage

**Rationale**:
- Official aiohttp session middleware
- Cookie-based (no server-side state needed)
- Encrypted storage protects OAuth state parameter
- Simple API, well-documented

**Configuration**:
```python
from aiohttp_session import setup
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from cryptography.fernet import Fernet

# Generate key: Fernet.generate_key()
setup(app, EncryptedCookieStorage(secret_key))
```

**Session Data**:
- `oauth_state`: CSRF protection during OAuth flow
- `gitlab_user`: Username after successful auth (optional, for display)

### 16. YAML File Storage for Users

**Decision**: Use `pyyaml` with atomic file writes

**Rationale**:
- Human-readable and editable by operators
- Already a common dependency in Python projects
- No database setup required
- Can be version-controlled if desired

**Implementation Pattern**:
```python
import yaml
from pathlib import Path
import tempfile
import shutil

def save_users(users: list, filepath: Path) -> None:
    """Atomic write to prevent corruption."""
    temp_fd, temp_path = tempfile.mkstemp(dir=filepath.parent)
    try:
        with os.fdopen(temp_fd, 'w') as f:
            yaml.safe_dump({'users': users}, f)
        shutil.move(temp_path, filepath)
    except:
        os.unlink(temp_path)
        raise
```

**File Locking**: Not implemented initially (low concurrency, atomic writes sufficient)

### 17. API Key Generation

**Decision**: Use `secrets.token_hex(16)` with `sk-` prefix

**Rationale**:
- 128 bits of entropy (sufficient for this use case)
- `secrets` module is cryptographically secure
- `sk-` prefix follows OpenAI convention (familiar to users)
- Hex encoding is URL-safe and easy to copy

**Format**: `sk-{32 hex characters}` (e.g., `sk-a1b2c3d4e5f6789012345678901234ab`)

### 18. HTML Templates

**Decision**: Use Jinja2 with embedded CSS (no external dependencies)

**Rationale**:
- Jinja2 is standard for Python web templating
- Already used by aiohttp ecosystem
- Inline CSS keeps templates self-contained
- No need for build step or asset pipeline

**Template Structure**:
- `login.html`: GitLab login button, minimal styling
- `success.html`: API key + LLM URL display, copy buttons
- `blocked.html`: Error message for blocked users
- `error.html`: Generic error page

### 19. LLM URL Display

**Decision**: Use configurable `public_url` from broker config

**Rationale**:
- Broker may be behind reverse proxy (different URL than bind address)
- Operators set the URL users should use
- Displayed on success page as `{public_url}/v1`

**Display Format**:
```
Your API Key: sk-abc123... [Copy]
LLM API URL: https://broker.example.com/v1 [Copy]

Example:
curl https://broker.example.com/v1/chat/completions \
  -H "Authorization: Bearer sk-abc123..." \
  -H "Content-Type: application/json" \
  -d '{"model": "llama3.2", "messages": [{"role": "user", "content": "Hello"}]}'
```

### OAuth Dependencies (Additional)

| Package | Version | Purpose |
|---------|---------|---------|
| authlib | >=1.3.0 | GitLab OAuth client |
| aiohttp-session | >=2.12.0 | Cookie session management |
| cryptography | >=41.0.0 | Cookie encryption |
| pyyaml | >=6.0 | Users file read/write |
| jinja2 | >=3.0 | HTML templating |

**Note**: `cryptography` and `pyyaml` may already be transitive dependencies.

### OAuth Security Considerations

1. **CSRF Protection**: OAuth state parameter stored in encrypted session cookie
2. **Cookie Security**: HttpOnly, Secure flags; SameSite=Lax
3. **File Permissions**: users.yaml should be 600 (owner read/write only)
4. **Secret Management**: `gitlab_client_secret` and `session_secret` via env vars
5. **API Key Exposure**: Only shown once on success page (not stored in session)

### OAuth Open Questions (Resolved)

| Question | Resolution |
|----------|------------|
| What GitLab scope is needed? | `read_user` - provides username and ID |
| How to handle GitLab being down? | Return error page, existing API keys still work |
| Multiple GitLab instances? | Single instance per broker deployment (config) |
| API key rotation? | Manual: operator deletes user from yaml, user re-authenticates |
