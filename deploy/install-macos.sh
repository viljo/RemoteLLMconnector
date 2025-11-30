#!/bin/bash
# Install RemoteLLM services on macOS (launchd)
# Usage: ./install-macos.sh [connector|broker|both]

set -e

INSTALL_DIR="/usr/local/opt/remotellm"
LOG_DIR="/usr/local/var/log/remotellm"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Parse arguments
COMPONENT="${1:-both}"

if [[ "$COMPONENT" != "connector" && "$COMPONENT" != "broker" && "$COMPONENT" != "both" ]]; then
    log_error "Usage: $0 [connector|broker|both]"
    exit 1
fi

log_info "Installing RemoteLLM ($COMPONENT)"

# Create directories
log_info "Creating directories"
mkdir -p "$INSTALL_DIR"
mkdir -p "$LOG_DIR"
mkdir -p "$LAUNCH_AGENTS_DIR"

# Install using pip (assumes pip is available)
log_info "Installing RemoteLLM package"
pip3 install --user remotellm || pip3 install remotellm

# Install connector service
if [[ "$COMPONENT" == "connector" || "$COMPONENT" == "both" ]]; then
    log_info "Installing connector service"

    PLIST_FILE="$LAUNCH_AGENTS_DIR/com.remotellm.connector.plist"

    # Copy plist file
    cp "$(dirname "$0")/launchd/com.remotellm.connector.plist" "$PLIST_FILE"

    log_warn "Edit $PLIST_FILE to configure:"
    log_warn "  - REMOTELLM_BROKER_URL"
    log_warn "  - REMOTELLM_BROKER_TOKEN"
    log_warn "  - REMOTELLM_MODELS (comma-separated model names)"
    log_warn "  - REMOTELLM_LLM_URL (if not localhost:11434)"

    log_info "Connector service installed"
    log_info "  Load:    launchctl load $PLIST_FILE"
    log_info "  Unload:  launchctl unload $PLIST_FILE"
    log_info "  Start:   launchctl start com.remotellm.connector"
    log_info "  Stop:    launchctl stop com.remotellm.connector"
    log_info "  Logs:    tail -f $LOG_DIR/connector.log"
fi

# Install broker service
if [[ "$COMPONENT" == "broker" || "$COMPONENT" == "both" ]]; then
    log_info "Installing broker service"

    PLIST_FILE="$LAUNCH_AGENTS_DIR/com.remotellm.broker.plist"

    # Copy plist file
    cp "$(dirname "$0")/launchd/com.remotellm.broker.plist" "$PLIST_FILE"

    log_warn "Edit $PLIST_FILE to configure:"
    log_warn "  - REMOTELLM_BROKER_PORT (default: 8443)"
    log_warn "  - REMOTELLM_BROKER_CONNECTOR_TOKENS"
    log_warn "  - REMOTELLM_BROKER_USER_API_KEYS"
    log_warn "  - REMOTELLM_BROKER_CONNECTOR_CONFIG (path to connectors.yaml)"

    log_info "Broker service installed"
    log_info "  Load:    launchctl load $PLIST_FILE"
    log_info "  Unload:  launchctl unload $PLIST_FILE"
    log_info "  Start:   launchctl start com.remotellm.broker"
    log_info "  Stop:    launchctl stop com.remotellm.broker"
    log_info "  Logs:    tail -f $LOG_DIR/broker.log"
fi

log_info "Installation complete!"
log_info ""
log_info "To start services on login, run:"
if [[ "$COMPONENT" == "connector" || "$COMPONENT" == "both" ]]; then
    log_info "  launchctl load -w $LAUNCH_AGENTS_DIR/com.remotellm.connector.plist"
fi
if [[ "$COMPONENT" == "broker" || "$COMPONENT" == "both" ]]; then
    log_info "  launchctl load -w $LAUNCH_AGENTS_DIR/com.remotellm.broker.plist"
fi
