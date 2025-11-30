#!/bin/bash
# Test script for the RemoteLLM web portal with GitLab OAuth
#
# This script starts:
# - 1 broker with OAuth configured
# - 2 connectors with different models each
#
# Prerequisites:
# - GitLab OAuth application configured
# - Set environment variables (see below)
#
# Required environment variables:
#   GITLAB_URL           - Your GitLab instance URL (e.g., https://gitlab.com)
#   GITLAB_CLIENT_ID     - OAuth application client ID
#   GITLAB_CLIENT_SECRET - OAuth application client secret
#
# Optional environment variables:
#   BROKER_PORT          - Broker port (default: 9443)
#   PUBLIC_URL           - Public URL for callbacks (default: http://localhost:9443)

set -e

# Configuration
BROKER_PORT="${BROKER_PORT:-9443}"
PUBLIC_URL="${PUBLIC_URL:-http://localhost:$BROKER_PORT}"
SESSION_SECRET="${SESSION_SECRET:-test-session-secret-for-development-only-32chars}"
USERS_FILE="${USERS_FILE:-test-users.yaml}"

# Check required environment variables
if [ -z "$GITLAB_URL" ] || [ -z "$GITLAB_CLIENT_ID" ] || [ -z "$GITLAB_CLIENT_SECRET" ]; then
    echo "Error: Required environment variables not set."
    echo ""
    echo "Please set the following environment variables:"
    echo "  export GITLAB_URL=https://your-gitlab.com"
    echo "  export GITLAB_CLIENT_ID=your-client-id"
    echo "  export GITLAB_CLIENT_SECRET=your-client-secret"
    echo ""
    echo "To create a GitLab OAuth application:"
    echo "  1. Go to GitLab > User Settings > Applications"
    echo "  2. Create a new application with:"
    echo "     - Name: RemoteLLM Test"
    echo "     - Redirect URI: $PUBLIC_URL/auth/callback"
    echo "     - Scopes: read_user"
    echo "  3. Copy the Application ID and Secret"
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE} RemoteLLM Web Portal Test Mode${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Create connector config file
CONNECTOR_CONFIG_FILE=$(mktemp)
cat > "$CONNECTOR_CONFIG_FILE" << EOF
connectors:
  - token: test-connector-1
    llm_api_key: sk-test-key-1
  - token: test-connector-2
    llm_api_key: sk-test-key-2
EOF

echo -e "${GREEN}Starting broker on port $BROKER_PORT...${NC}"

# Cleanup function
cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down...${NC}"
    kill $BROKER_PID 2>/dev/null || true
    kill $CONNECTOR1_PID 2>/dev/null || true
    kill $CONNECTOR2_PID 2>/dev/null || true
    rm -f "$CONNECTOR_CONFIG_FILE"
    echo -e "${GREEN}Done.${NC}"
}
trap cleanup EXIT

# Start broker with OAuth
uv run python -m remotellm.broker \
    --host 0.0.0.0 \
    --port "$BROKER_PORT" \
    --connector-config "$CONNECTOR_CONFIG_FILE" \
    --user-api-key sk-test-user-key \
    --health-port $((BROKER_PORT + 100)) \
    --log-level INFO \
    --gitlab-url "$GITLAB_URL" \
    --gitlab-client-id "$GITLAB_CLIENT_ID" \
    --gitlab-client-secret "$GITLAB_CLIENT_SECRET" \
    --gitlab-redirect-uri "$PUBLIC_URL/auth/callback" \
    --public-url "$PUBLIC_URL" \
    --session-secret "$SESSION_SECRET" \
    --users-file "$USERS_FILE" \
    &
BROKER_PID=$!

# Wait for broker to start
sleep 2

echo -e "${GREEN}Starting connector 1 (models: gpt-4, claude-3)...${NC}"
uv run python -m remotellm.connector \
    --llm-url http://localhost:11434 \
    --broker-url "ws://localhost:$((BROKER_PORT + 1))" \
    --broker-token test-connector-1 \
    --model gpt-4 \
    --model claude-3 \
    --health-port $((BROKER_PORT + 101)) \
    --log-level INFO \
    &
CONNECTOR1_PID=$!

echo -e "${GREEN}Starting connector 2 (models: llama-3, mixtral)...${NC}"
uv run python -m remotellm.connector \
    --llm-url http://localhost:11434 \
    --broker-url "ws://localhost:$((BROKER_PORT + 1))" \
    --broker-token test-connector-2 \
    --model llama-3 \
    --model mixtral \
    --health-port $((BROKER_PORT + 102)) \
    --log-level INFO \
    &
CONNECTOR2_PID=$!

# Wait for connectors to connect
sleep 3

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Test environment is ready!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "Web Portal:     ${YELLOW}$PUBLIC_URL${NC}"
echo -e "Health Check:   ${YELLOW}http://localhost:$((BROKER_PORT + 100))/health${NC}"
echo ""
echo -e "${BLUE}Available endpoints:${NC}"
echo -e "  /              - Home (login redirect)"
echo -e "  /auth/login    - GitLab OAuth login"
echo -e "  /dashboard     - User dashboard (API key)"
echo -e "  /services      - Connected services"
echo -e "  /admin         - Admin dashboard (first user is admin)"
echo -e "  /admin/users   - User management"
echo -e "  /admin/logs    - Request logs"
echo ""
echo -e "${BLUE}Models available:${NC}"
echo -e "  Connector 1: gpt-4, claude-3"
echo -e "  Connector 2: llama-3, mixtral"
echo ""
echo -e "${BLUE}API Access:${NC}"
echo -e "  curl $PUBLIC_URL/v1/models -H 'Authorization: Bearer sk-test-user-key'"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop${NC}"

# Wait for all processes
wait
