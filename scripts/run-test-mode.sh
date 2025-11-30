#!/usr/bin/env bash
# Run the broker in interactive test mode for manual portal testing.
#
# This script starts the broker with:
# - Test mode enabled (bypasses OAuth authentication)
# - Mock connectors with sample models
# - Debug logging enabled
#
# Usage:
#   ./scripts/run-test-mode.sh
#
# Then open http://localhost:8443 in your browser.
# You can log in with any username (e.g., "admin", "user1", "testuser").
# The first user to log in becomes an administrator.

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   RemoteLLM Broker - Interactive Test Mode${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Configuration
PORT=${PORT:-8443}
HEALTH_PORT=${HEALTH_PORT:-8080}
LOG_LEVEL=${LOG_LEVEL:-DEBUG}

echo -e "${YELLOW}Configuration:${NC}"
echo -e "  API Port:     ${GREEN}$PORT${NC}"
echo -e "  Health Port:  ${GREEN}$HEALTH_PORT${NC}"
echo -e "  Log Level:    ${GREEN}$LOG_LEVEL${NC}"
echo ""

echo -e "${YELLOW}Test Mode Features:${NC}"
echo -e "  - OAuth authentication is ${GREEN}bypassed${NC}"
echo -e "  - Login with ${GREEN}any username${NC} (e.g., admin, user1, testuser)"
echo -e "  - First user becomes ${GREEN}administrator${NC}"
echo -e "  - Mock connectors with ${GREEN}8 sample models${NC} are registered"
echo ""

echo -e "${YELLOW}URLs:${NC}"
echo -e "  Web Portal:   ${GREEN}http://localhost:${PORT}${NC}"
echo -e "  API Endpoint: ${GREEN}http://localhost:${PORT}/v1${NC}"
echo -e "  Health Check: ${GREEN}http://localhost:${HEALTH_PORT}/health${NC}"
echo ""

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}Starting broker...${NC}"
echo -e "Press ${RED}Ctrl+C${NC} to stop."
echo ""

# Clean up any previous test data
rm -f test-users.yaml

# Run the broker in test mode
exec uv run python -m remotellm.broker \
    --port "$PORT" \
    --health-port "$HEALTH_PORT" \
    --log-level "$LOG_LEVEL" \
    --users-file test-users.yaml \
    --test-mode
