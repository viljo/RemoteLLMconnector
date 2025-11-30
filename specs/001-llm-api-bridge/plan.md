# Implementation Plan: Web Portal Admin Features

**Branch**: `001-llm-api-bridge` | **Date**: 2025-11-30 | **Spec**: [spec.md](spec.md)
**Input**: User request for admin portal features extending the OAuth authentication portal

## Summary

Extend the existing GitLab OAuth authentication portal with role-based access control (admin/user), an admin dashboard showing connected LLM services with model status, user management (list, blacklist, manual add), connector monitoring, and log viewing. The API key display page will also show available models.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: aiohttp, websockets, structlog, authlib, aiohttp-session, jinja2
**Storage**: YAML files (users.yaml extended with roles), in-memory for connector state
**Testing**: pytest, pytest-asyncio
**Target Platform**: Linux server, macOS
**Project Type**: Web application (broker component)
**Performance Goals**: 10 concurrent admin users, real-time status updates
**Constraints**: No external database, file-based storage, minimal dependencies
**Scale/Scope**: 10-100 users, 5-20 connectors

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Transparent Proxy | PASS | Admin UI is separate from proxy flow |
| II. Security-First | PASS | Admin requires auth, uses HTTPS, secrets protected |
| III. Connection Reliability | PASS | Admin UI is informational, doesn't affect tunnel |
| IV. Protocol Compatibility | PASS | No protocol changes needed |
| V. Observability | PASS | Adds visibility into connector state |

**Development Workflow Compliance**:
- uv for dependencies: PASS
- ruff for linting: PASS
- pytest for testing: PASS
- Type hints: PASS

## Project Structure

### Documentation (this feature)

```text
specs/001-llm-api-bridge/
├── plan.md              # This file
├── research.md          # Phase 0 output (extended)
├── data-model.md        # Phase 1 output (extended)
├── quickstart.md        # Phase 1 output (extended)
├── contracts/           # Phase 1 output
│   └── admin-api.yaml   # Admin API endpoints
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
src/remotellm/
├── broker/
│   ├── api.py           # Existing API endpoints
│   ├── admin.py         # NEW: Admin web UI handlers
│   ├── auth.py          # NEW: OAuth + role-based auth (refactored from api)
│   ├── templates/       # NEW: Jinja2 templates
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── success.html # Updated with model list
│   │   ├── admin/
│   │   │   ├── dashboard.html
│   │   │   ├── users.html
│   │   │   ├── connectors.html
│   │   │   └── logs.html
│   └── ...
```

**Structure Decision**: Single web application structure extending existing broker component.

## Complexity Tracking

No violations to justify.

## Feature Requirements

### 1. User Roles (admin/user)

- Add `role` field to User model: "admin" | "user"
- First GitLab admin who registers becomes admin
- Admins can promote/demote other users
- Role determines access to admin pages

### 2. API Display Page Enhancements

- Show list of currently available model names
- Show LLM URL alongside API key
- Real-time model availability (models may go offline)

### 3. Connected Services Status (all users)

- List all connected LLM services (connectors)
- Show models offered by each connector
- Show connection status (connected/disconnected)
- No sensitive info exposed (no API keys, tokens)

### 4. Admin Dashboard

- User management:
  - List all users with roles, created_at, last_used
  - Blacklist/unblacklist users
  - Manually add new users with generated API keys
- Connector monitoring:
  - List all connectors with connection state
  - Models per connector
  - Last seen timestamp
- Log viewing:
  - Recent request logs (last 100)
  - Filter by user, model, status
  - Correlation ID search

## Dependencies (New)

No new dependencies - using existing stack (aiohttp, jinja2, structlog)
