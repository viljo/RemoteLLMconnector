# Tasks: Multi-Connector Architecture Migration

**Input**: Design documents from `/specs/001-llm-api-bridge/`
**Prerequisites**: plan.md, spec.md, data-model.md
**Migration**: Existing single-connector code → multi-connector with centralized keys

**Tests**: Not explicitly requested - tests omitted from task list.

**Organization**: Tasks are grouped by component and user story. This is a migration from existing code, not a greenfield implementation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

---

## Phase 1: Protocol Updates (Foundation)

**Purpose**: Update shared protocol to support multi-connector architecture

- [x] T001 Add `models` field to AuthPayload in src/remotellm/shared/protocol.py
- [x] T002 Add `llm_api_key` field to RequestPayload in src/remotellm/shared/protocol.py
- [x] T003 [P] Update create_auth_message() to accept models list in src/remotellm/shared/protocol.py
- [x] T004 [P] Update create_request_message() to accept llm_api_key in src/remotellm/shared/protocol.py

**Checkpoint**: Protocol supports multi-connector messaging

---

## Phase 2: Broker Updates - Multi-Connector Support (US1, US2)

**Purpose**: Enable broker to manage multiple connectors, route by model, and handle all API keys

### 2.1 Broker Config Updates

- [x] T005 Add `user_api_keys` list to BrokerConfig in src/remotellm/broker/config.py
- [x] T006 Add `connector_configs` list (token → llm_api_key mapping) to BrokerConfig in src/remotellm/broker/config.py
- [x] T007 [P] Update broker CLI to add `--user-api-key` option in src/remotellm/broker/__main__.py
- [x] T008 [P] Update broker CLI to add `--connector-config` option for YAML file in src/remotellm/broker/__main__.py

### 2.2 Tunnel Server Updates

- [x] T009 Update ConnectorRegistration dataclass to include `models` list in src/remotellm/broker/tunnel_server.py
- [x] T010 Update ConnectorRegistration dataclass to include `llm_api_key` in src/remotellm/broker/tunnel_server.py
- [x] T011 Update _authenticate() to extract models from AUTH payload in src/remotellm/broker/tunnel_server.py
- [x] T012 Update _authenticate() to look up llm_api_key from config by token in src/remotellm/broker/tunnel_server.py

### 2.3 Model Router (NEW)

- [x] T013 Create ModelRouter class in src/remotellm/broker/router.py
- [x] T014 Implement build_routes() to create model→connector mapping in src/remotellm/broker/router.py
- [x] T015 Implement get_route(model) returning (connector_id, llm_api_key) in src/remotellm/broker/router.py
- [x] T016 Implement on_connector_registered() to update routes in src/remotellm/broker/router.py
- [x] T017 Implement on_connector_disconnected() to remove routes in src/remotellm/broker/router.py

### 2.4 API Updates

- [x] T018 [US1] Add user API key validation in handle_chat_completions() in src/remotellm/broker/api.py
- [x] T019 [US1] Extract model from request body in handle_chat_completions() in src/remotellm/broker/api.py
- [x] T020 [US1] Use ModelRouter.get_route() to find connector in src/remotellm/broker/api.py
- [x] T021 [US1] Inject llm_api_key into REQUEST payload before forwarding in src/remotellm/broker/api.py
- [x] T022 [US1] Update /v1/models to aggregate from all connected connectors in src/remotellm/broker/api.py
- [x] T023 [US1] Add 404 response for unknown model in src/remotellm/broker/api.py
- [x] T024 [US1] Add 503 response for model with no active connector in src/remotellm/broker/api.py

### 2.5 Broker Main Integration

- [x] T025 Instantiate ModelRouter in Broker class in src/remotellm/broker/main.py
- [x] T026 Wire TunnelServer registration events to ModelRouter in src/remotellm/broker/main.py
- [x] T027 Pass ModelRouter to API server in src/remotellm/broker/main.py

**Checkpoint**: Broker supports multiple connectors with model-based routing

---

## Phase 3: Connector Updates - Pure Transparent Proxy (US1, US2)

**Purpose**: Simplify connector to pure transparent proxy (no API key handling)

### 3.1 Connector Config Updates

- [x] T028 Add `models` list to ConnectorConfig in src/remotellm/connector/config.py
- [x] T029 Remove `api_keys` from ConnectorConfig in src/remotellm/connector/config.py
- [x] T030 Remove `llm_api_key` from ConnectorConfig in src/remotellm/connector/config.py (key now in broker)
- [x] T031 [P] Update connector CLI to add `--model` option (multiple) in src/remotellm/connector/__main__.py
- [x] T032 [P] Update connector CLI to remove `--api-key` and `--llm-api-key` options in src/remotellm/connector/__main__.py

### 3.2 Tunnel Client Updates

- [x] T033 Update AUTH payload to include models list in src/remotellm/connector/tunnel_client.py

### 3.3 LLM Client Updates

- [x] T034 Update forward_request() to accept llm_api_key parameter in src/remotellm/connector/llm_client.py
- [x] T035 Update forward_streaming_request() to accept llm_api_key parameter in src/remotellm/connector/llm_client.py
- [x] T036 Remove api_key from LLMClient __init__ in src/remotellm/connector/llm_client.py

### 3.4 Connector Main Updates

- [x] T037 [US1] Remove user API key validation from _handle_request() in src/remotellm/connector/main.py
- [x] T038 [US1] Extract llm_api_key from REQUEST payload in _handle_request() in src/remotellm/connector/main.py
- [x] T039 [US1] Pass llm_api_key to LLMClient.forward_request() in src/remotellm/connector/main.py
- [x] T040 [US1] Pass llm_api_key to LLMClient.forward_streaming_request() in src/remotellm/connector/main.py
- [x] T041 [US2] Update Connector __init__ to accept models from config in src/remotellm/connector/main.py

**Checkpoint**: Connector is pure transparent proxy, receives API key per-request

---

## Phase 4: User Story 3 - Health Monitoring Updates (US3)

**Goal**: Update health endpoints for multi-connector visibility

- [x] T042 [US3] Update broker health endpoint to show connector count and models in src/remotellm/broker/health.py
- [x] T043 [US3] Add registered models list to broker health response in src/remotellm/broker/health.py
- [x] T044 [US3] Update connector health endpoint to show registered models in src/remotellm/connector/health.py

**Checkpoint**: Health endpoints reflect multi-connector state

---

## Phase 5: User Story 4 - Service Deployment Updates (US4)

**Goal**: Update service files for new CLI options

- [x] T045 [P] [US4] Update systemd connector service to use --model flags in deploy/systemd/remotellm-connector.service
- [x] T046 [P] [US4] Update systemd broker service to use --user-api-key and --connector-config in deploy/systemd/remotellm-broker.service
- [x] T047 [P] [US4] Update launchd connector plist for new CLI in deploy/launchd/com.remotellm.connector.plist
- [x] T048 [P] [US4] Update launchd broker plist for new CLI in deploy/launchd/com.remotellm.broker.plist
- [x] T049 [US4] Create sample connectors.yaml config file in deploy/connectors.yaml.example
- [x] T050 [US4] Update install scripts for new config structure in deploy/install-linux.sh and deploy/install-macos.sh

**Checkpoint**: Service deployment works with new architecture

---

## Phase 6: Documentation & Polish

**Purpose**: Update documentation and validate

- [x] T051 Update README.md with multi-connector architecture diagram
- [x] T052 Update README.md CLI examples for new options
- [x] T053 Update quickstart.md with multi-connector setup example
- [x] T054 Update CLAUDE.md with new project structure
- [x] T055 Run ruff check and fix any linting issues
- [x] T056 Run mypy and address type errors (pre-existing issues only, no new errors)
- [x] T057 Manual E2E test: Start broker with 2 connectors, route requests by model

**Checkpoint**: Documentation complete, E2E validated

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1: Protocol Updates
    ↓
Phase 2: Broker Updates  ←─── Phase 3: Connector Updates (can parallel)
    ↓                              ↓
    └──────────────┬───────────────┘
                   ↓
Phase 4: Health Updates
    ↓
Phase 5: Service Deployment
    ↓
Phase 6: Documentation & Polish
```

### User Story Dependencies

- **US1 (Request Forwarding)**: Core changes in Phase 2 + Phase 3
- **US2 (Startup/Connection)**: Config changes in Phase 2 + Phase 3
- **US3 (Health Monitoring)**: Phase 4 (depends on Phase 2+3)
- **US4 (Service Deployment)**: Phase 5 (depends on Phase 2+3)

### Parallel Opportunities

**Phase 1**:
- T003, T004 can run in parallel (different functions)

**Phase 2.1**:
- T007, T008 can run in parallel (different CLI options)

**Phase 3.1**:
- T031, T032 can run in parallel (different CLI changes)

**Phase 5**:
- T045, T046, T047, T048 can all run in parallel (different files)

---

## Implementation Strategy

### MVP First

1. Complete Phase 1: Protocol Updates
2. Complete Phase 2: Broker Updates
3. Complete Phase 3: Connector Updates
4. **VALIDATE**: Test multi-connector routing locally
5. Then proceed to Phase 4-6

### Incremental Delivery

| Milestone | What Works |
|-----------|------------|
| After Phase 1 | Protocol ready, existing code still works |
| After Phase 2+3 | Multi-connector routing works |
| After Phase 4 | Health monitoring shows all connectors |
| After Phase 5 | Production deployment ready |
| After Phase 6 | Fully documented and tested |

---

## Notes

- This is a **migration**, not greenfield - existing code paths preserved where possible
- [P] tasks = different files, no dependencies
- [Story] label maps task to user story for traceability
- Broker now owns ALL API keys (user + LLM)
- Connector is pure transparent proxy (no key storage)
- Test with multiple connectors serving different models
